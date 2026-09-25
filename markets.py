#!/usr/bin/env python3
"""markets.py — Yahoo Finance (+ Stooq fallback) market data and NSE FII/DII flows.
Owned by Agent B. Stdlib only — see CONTRACT.md for the data.json["markets"]/["flows"] schema.
`_fetch_url` is the single low-level HTTP GET (Yahoo+Stooq); `_nse_fetch` is the cookie-jar
GET used only for NSE. Each symbol is fetched once even if it appears in several groups, then
formatted per occurrence with that occurrence's decimals/unit/currency. Nothing here raises:
a bad symbol/feed/outage is logged and dropped."""
from __future__ import annotations

import csv
import io
import json
import random
import time
import urllib.error
import urllib.parse
import urllib.request
import http.cookiejar
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
YAHOO_HOSTS = ("query1.finance.yahoo.com", "query2.finance.yahoo.com")
YAHOO_HEADERS = {"User-Agent": UA, "Accept": "application/json,text/plain,*/*"}
TIMEOUT, MAX_WORKERS = 12, 8

def log(msg: str) -> None:
    print(f"[markets] {msg}", flush=True)

def _fetch_url(url: str, headers: dict, timeout: int) -> bytes:
    """The one place an HTTP GET happens for Yahoo/Stooq. Tests monkeypatch this."""
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()

def _fetch_yahoo(symbol: str) -> bytes:
    """query1 -> query2, with a jittered-backoff single retry on HTTP 429."""
    q = urllib.parse.quote(symbol, safe="")
    last_err: Exception | None = None
    for host in YAHOO_HOSTS:
        url = f"https://{host}/v8/finance/chart/{q}?range=3mo&interval=1d&includePrePost=false"
        for attempt in (1, 2):
            try:
                return _fetch_url(url, YAHOO_HEADERS, TIMEOUT)
            except urllib.error.HTTPError as e:
                last_err = e
                if e.code == 429 and attempt == 1:
                    time.sleep(2 + random.random())
                    continue
                break  # non-429, or already retried once -> try next host
            except Exception as e:  # noqa: BLE001 - timeouts, DNS, etc.
                last_err = e
                break
    raise last_err or RuntimeError(f"yahoo fetch failed for {symbol}")

def _fetch_stooq(stooq_symbol: str) -> list[tuple]:
    """Daily CSV history from Stooq. Returns [(date, close), ...] unsorted."""
    url = f"https://stooq.com/q/d/l/?s={urllib.parse.quote(stooq_symbol)}&i=d"
    raw = _fetch_url(url, {"User-Agent": UA}, TIMEOUT)
    text = raw.decode("utf-8", "replace")
    if text.strip().lower().startswith("no data") or not text.strip():
        raise ValueError("stooq: no data")
    pairs = []
    for row in csv.DictReader(io.StringIO(text)):
        try:
            d = datetime.strptime(row["Date"], "%Y-%m-%d").date()
            c = float(row["Close"])
        except (KeyError, ValueError, TypeError):
            continue
        pairs.append((d, c))
    if not pairs:
        raise ValueError("stooq: no parseable rows")
    return pairs

def _parse_yahoo(payload: bytes) -> dict:
    """Yahoo chart JSON -> {pairs, value, as_of, high52, low52, currency}."""
    data = json.loads(payload)
    chart = data.get("chart") or {}
    result = chart.get("result")
    if not result:
        err = chart.get("error") or {}
        raise ValueError(err.get("description") or err.get("code") or "empty chart result")
    r = result[0] or {}
    meta = r.get("meta") or {}
    timestamps = r.get("timestamp") or []
    quotes = ((r.get("indicators") or {}).get("quote") or [{}])[0] or {}
    closes = quotes.get("close") or []
    tz = timezone(timedelta(seconds=meta.get("gmtoffset") or 0))
    by_date: dict = {}
    for ts, c in zip(timestamps, closes):
        if ts is None or c is None:  # drop nulls (holidays / incomplete bars)
            continue
        by_date[datetime.fromtimestamp(ts, tz=tz).date()] = float(c)
    if not by_date:
        raise ValueError("no usable close prices")
    dates_sorted = sorted(by_date)
    rmt = meta.get("regularMarketTime")
    as_of = datetime.fromtimestamp(rmt, tz=tz).date() if rmt is not None else dates_sorted[-1]
    value = meta.get("regularMarketPrice")
    if value is None:
        prior_or_eq = [d for d in dates_sorted if d <= as_of]
        as_of = prior_or_eq[-1] if prior_or_eq else dates_sorted[-1]
        value = by_date[as_of]
    else:
        value = float(value)
    pairs = [(d, by_date[d]) for d in dates_sorted if d <= as_of]
    if not pairs or pairs[-1][0] != as_of:
        pairs.append((as_of, value))  # last point = value, on the regularMarketTime date
    high52 = meta.get("fiftyTwoWeekHigh")
    low52 = meta.get("fiftyTwoWeekLow")
    return {
        "pairs": pairs, "value": value, "as_of": as_of,
        "high52": float(high52) if high52 is not None else None,
        "low52": float(low52) if low52 is not None else None,
        "currency": meta.get("currency"),
    }

def _pairs_to_raw(pairs: list[tuple]) -> dict:
    """Stooq CSV rows -> the same common shape as _parse_yahoo."""
    pairs = sorted(pairs)
    as_of, value = pairs[-1]
    window = pairs[-252:] if len(pairs) >= 200 else []
    high52 = max(c for _, c in window) if window else None
    low52 = min(c for _, c in window) if window else None
    return {
        "pairs": pairs[-70:], "value": value, "as_of": as_of,
        "high52": high52, "low52": low52, "currency": None,
    }

def _fetch_symbol(symbol: str, stooq_symbol: str | None) -> dict | None:
    """Yahoo first, Stooq fallback (if configured), None (logged) if both fail."""
    try:
        return _parse_yahoo(_fetch_yahoo(symbol))
    except Exception as e:  # noqa: BLE001
        log(f"yahoo failed for {symbol}: {e}")
        if stooq_symbol:
            try:
                return _pairs_to_raw(_fetch_stooq(stooq_symbol))
            except Exception as e2:  # noqa: BLE001
                log(f"stooq fallback failed for {symbol} ({stooq_symbol}): {e2}")
    return None

def _format_item(cfg: dict, raw: dict) -> dict | None:
    decimals = cfg.get("decimals", 2)
    unit = cfg.get("unit", "")
    pairs = list(raw["pairs"])
    as_of = raw["as_of"]
    value = raw["value"]
    high52, low52 = raw.get("high52"), raw.get("low52")
    prior = [c for d, c in pairs if d < as_of]
    prev_close = prior[-1] if prior else None
    # Legacy Yahoo yield feed sometimes reports e.g. 42.5 for a 4.25% yield.
    if unit == "%" and value is not None and value > 20:
        value /= 10
        prev_close = prev_close / 10 if prev_close is not None else None
        high52 = high52 / 10 if high52 is not None else None
        low52 = low52 / 10 if low52 is not None else None
        pairs = [(d, c / 10) for d, c in pairs]
    if value is None:
        return None
    change = (value - prev_close) if prev_close is not None else None
    change_pct = (change / prev_close * 100) if (change is not None and prev_close) else None
    spark = [[d.isoformat(), round(c, decimals)] for d, c in pairs if d <= as_of]
    if spark and spark[-1][0] == as_of.isoformat():
        spark[-1][1] = round(value, decimals)
    else:
        spark.append([as_of.isoformat(), round(value, decimals)])
    item = {
        "symbol": cfg["symbol"], "name": cfg["name"], "short": cfg["short"],
        "region": cfg.get("region"),
        "value": round(value, decimals),
        "change": round(change, decimals) if change is not None else None,
        "change_pct": round(change_pct, 2) if change_pct is not None else None,
        "prev_close": round(prev_close, decimals) if prev_close is not None else None,
        "currency": cfg.get("currency", raw.get("currency")),
        "decimals": decimals, "unit": unit,
        "as_of": as_of.isoformat(),
        "high_52w": round(high52, decimals) if high52 is not None else None,
        "low_52w": round(low52, decimals) if low52 is not None else None,
        "spark": spark,
    }
    if unit == "%":
        item["change_bps"] = round(change * 100, 1) if change is not None else None
    return item

# ---- accuracy guards: implausible one-day moves are blanked, old prints are flagged -------
MOVE_LIMIT_PCT = {"india": 12, "global": 12, "sectors": 12, "futures": 12, "tech": 25,
                  "fx": 5, "commodities": 20, "crypto": 30}
MAX_BPS = 60
STALE_DAYS = {"crypto": 1, "futures": 1}


def _sanity(item: dict, group_id: str, today) -> dict:
    as_of = datetime.fromisoformat(item["as_of"]).date() if item.get("as_of") else None
    item["stale"] = bool(as_of and (today - as_of).days > STALE_DAYS.get(group_id, 4))
    suspect = False
    if item.get("unit") == "%":
        bps = item.get("change_bps")
        suspect = bps is not None and abs(bps) > MAX_BPS
    else:
        pct = item.get("change_pct")
        suspect = pct is not None and abs(pct) > MOVE_LIMIT_PCT.get(group_id, 15)
    if suspect:
        log(f"suspect move blanked: {item['symbol']} {item.get('change_pct')}% / {item.get('change_bps')} bps")
        item["change"] = item["change_pct"] = None
        if "change_bps" in item:
            item["change_bps"] = None
    item["suspect"] = suspect
    if item["stale"]:
        log(f"stale print flagged: {item['symbol']} as of {item.get('as_of')}")
    return item


def _movers(cfg: dict | None, today) -> dict | None:
    """Large-cap breadth + top gainers/losers from the latest common session. Stale prints skipped."""
    syms = (cfg or {}).get("symbols") or []
    if not syms:
        return None
    rows = []
    with ThreadPoolExecutor(max_workers=4) as ex:  # gentle on Yahoo: runs after the main board
        futs = {ex.submit(_fetch_symbol, s["symbol"], None): s for s in syms}
        for fut in futs:
            s = futs[fut]
            try:
                raw = fut.result()
                item = _format_item({"symbol": s["symbol"], "name": s["short"], "short": s["short"],
                                     "decimals": 2, "currency": "INR"}, raw) if raw else None
            except Exception:  # noqa: BLE001
                item = None
            if item and item.get("change_pct") is not None and abs(item["change_pct"]) <= 25:
                rows.append(item)
    if len(rows) < 10:
        return None
    latest = max(r["as_of"] for r in rows)
    if (today - datetime.fromisoformat(latest).date()).days > 4:
        return None
    rows = [r for r in rows if r["as_of"] == latest]
    rows.sort(key=lambda r: r["change_pct"])
    slim = lambda r: {"symbol": r["symbol"], "name": r["short"], "value": r["value"],  # noqa: E731
                      "change_pct": r["change_pct"], "change": r["change"]}
    return {"label": cfg.get("label", "Nifty 50 heavyweights"), "as_of": latest, "count": len(rows),
            "advances": sum(1 for r in rows if r["change_pct"] > 0),
            "declines": sum(1 for r in rows if r["change_pct"] < 0),
            "unchanged": sum(1 for r in rows if r["change_pct"] == 0),
            "gainers": [slim(r) for r in reversed(rows[-5:]) if r["change_pct"] > 0],
            "losers": [slim(r) for r in rows[:5] if r["change_pct"] < 0]}


def fetch_markets(markets_cfg: dict, now_utc: datetime) -> dict | None:
    """Fetch each symbol once, format per group occurrence. Drops failed symbols and empty
    groups; returns None if everything failed. Never raises."""
    try:
        groups_cfg = (markets_cfg or {}).get("groups", [])
        stooq_by_symbol: dict[str, str] = {}
        for g in groups_cfg:
            for it in g.get("items", []):
                sym, stq = it.get("symbol"), it.get("stooq")
                if sym and stq and sym not in stooq_by_symbol:
                    stooq_by_symbol[sym] = stq
        unique_symbols = sorted({it["symbol"] for g in groups_cfg for it in g.get("items", [])})
        raw_by_symbol: dict[str, dict | None] = {}
        if unique_symbols:
            with ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
                futs = {ex.submit(_fetch_symbol, sym, stooq_by_symbol.get(sym)): sym for sym in unique_symbols}
                for fut in futs:
                    sym = futs[fut]
                    try:
                        raw_by_symbol[sym] = fut.result()
                    except Exception as e:  # noqa: BLE001 - defensive; _fetch_symbol shouldn't raise
                        log(f"unexpected error fetching {sym}: {e}")
                        raw_by_symbol[sym] = None
        groups_out = []
        for g in groups_cfg:
            items_out = []
            for cfg in g.get("items", []):
                raw = raw_by_symbol.get(cfg["symbol"])
                if raw is None:
                    if not cfg.get("optional"):
                        log(f"dropping required symbol {cfg['symbol']} ({g.get('id')}) — no data")
                    continue
                try:
                    item = _format_item(cfg, raw)
                except Exception as e:  # noqa: BLE001
                    log(f"failed to format {cfg.get('symbol')}: {e}")
                    item = None
                if item is not None and item.get("value") and item["value"] > 0:
                    items_out.append(_sanity(item, g.get("id"), now_utc.astimezone(IST).date()))
            if items_out:
                groups_out.append({"id": g.get("id"), "label": g.get("label"), "items": items_out})
            else:
                log(f"group '{g.get('id')}' produced 0 items — dropped")
        if not groups_out:
            return None
        as_of = now_utc.astimezone(IST).isoformat(timespec="seconds")
        out = {"as_of": as_of, "groups": groups_out}
        try:
            mv = _movers((markets_cfg or {}).get("movers"), now_utc.astimezone(IST).date())
            if mv:
                out["movers"] = mv
        except Exception as e:  # noqa: BLE001
            log(f"movers failed: {e}")
        return out
    except Exception as e:  # noqa: BLE001 - fetch_markets must never raise
        log(f"fetch_markets crashed: {e}")
        return None

NSE_HOME = "https://www.nseindia.com/"
NSE_API = "https://www.nseindia.com/api/fiidiiTradeReact"
NSE_REFERER = "https://www.nseindia.com/reports/fii-dii"
NSE_HEADERS = {"User-Agent": UA, "Accept-Language": "en-US,en;q=0.9", "Accept": "*/*"}
NSE_TIMEOUT = 8

def _nse_fetch(url: str, headers: dict, timeout: int, cj: "http.cookiejar.CookieJar") -> bytes:
    """Cookie-jar GET used only for NSE (it gates the API behind a homepage visit)."""
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))
    req = urllib.request.Request(url, headers=headers)
    with opener.open(req, timeout=timeout) as resp:
        return resp.read()

def _nse_date(s: str | None) -> str | None:
    if not s:
        return None
    try:
        return datetime.strptime(s.strip(), "%d-%b-%Y").date().isoformat()
    except ValueError:
        return None

def _nse_num(x) -> float | None:
    try:
        return float(str(x).replace(",", "").strip())
    except (TypeError, ValueError):
        return None

def fetch_flows(now_utc: datetime) -> dict | None:
    """NSE provisional FII/DII activity. None on any failure (NSE often blocks datacenter IPs)."""
    try:
        cj = http.cookiejar.CookieJar()
        _nse_fetch(NSE_HOME, NSE_HEADERS, NSE_TIMEOUT, cj)  # seed cookies
        headers = dict(NSE_HEADERS, Referer=NSE_REFERER, Accept="application/json")
        raw = _nse_fetch(NSE_API, headers, NSE_TIMEOUT, cj)
        rows = json.loads(raw)
        if not isinstance(rows, list):
            raise ValueError("unexpected NSE payload shape")
        out: dict = {}
        date_iso = None
        for row in rows:
            cat = (row.get("category") or "").upper()
            if cat.startswith("DII"):
                key = "dii"
            elif cat.startswith("FII") or cat.startswith("FPI"):
                key = "fii"
            else:
                continue
            buy, sell, net = _nse_num(row.get("buyValue")), _nse_num(row.get("sellValue")), _nse_num(row.get("netValue"))
            out[key] = {"buy": buy, "sell": sell, "net": net}
            date_iso = date_iso or _nse_date(row.get("date"))
        if "fii" not in out or "dii" not in out:
            raise ValueError("missing fii/dii rows in NSE response")
        return {"date": date_iso, "unit": "₹ crore", "fii": out["fii"], "dii": out["dii"]}
    except Exception as e:  # noqa: BLE001
        log(f"fetch_flows failed (often expected off-NSE-network): {e}")
        return None

def _print_table(data: dict | None) -> None:
    if not data:
        print("markets: no data (every symbol failed — offline sandbox, or Yahoo unreachable)")
        return
    print(f"as_of: {data['as_of']}")
    for g in data["groups"]:
        print(f"\n== {g['label']} ({g['id']}) — {len(g['items'])} items ==")
        for it in g["items"]:
            chg = f"{it['change']:+}" if it["change"] is not None else "n/a"
            pct = f"{it['change_pct']:+.2f}%" if it["change_pct"] is not None else "n/a"
            print(f"  {it['short']:<10} {it['value']!s:>12} {chg:>10} {pct:>9}  as_of={it['as_of']}")

def main() -> None:
    from pathlib import Path
    cfg_path = Path(__file__).with_name("markets.json")
    cfg = json.loads(cfg_path.read_text(encoding="utf-8")) if cfg_path.exists() else {"groups": []}
    now = datetime.now(timezone.utc)
    data = fetch_markets(cfg, now)
    _print_table(data)
    flows = fetch_flows(now)
    print("\n== FII/DII Flows ==")
    print(flows if flows else "  no flow data (NSE likely blocked this IP, or offline sandbox)")

if __name__ == "__main__":
    main()
