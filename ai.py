#!/usr/bin/env python3
"""ai.py -- the morning AI briefing (Gemini/Anthropic/OpenAI) with a deterministic fallback.
Owned by Agent B. Interface fixed by CONTRACT.md/build.py: make_brief(sections, markets,
site_cfg, now_utc) and fallback_brief(sections, markets, now_utc) -> data.json["brief"]
(build.py doesn't pass flows in). `_post_json` is the one low-level HTTP call (tests
monkeypatch it); neither function ever raises -- failures fall through to fallback_brief."""
from __future__ import annotations

import bisect
import json
import os
import re
import time
import traceback
import urllib.error
import urllib.request
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")
ENV_KEYS = {"gemini": "GEMINI_API_KEY", "anthropic": "ANTHROPIC_API_KEY", "openai": "OPENAI_API_KEY"}
MOODS = {"risk-on", "risk-off", "mixed"}
SECTION_IDS = ("india", "world", "tech")

def log(msg: str) -> None:
    print(f"[ai] {msg}", flush=True)

def _post_json(url: str, headers: dict, body: dict, timeout: int) -> tuple:
    """POST JSON; returns (status, raw_bytes) even on HTTP error status. Tests monkeypatch this."""
    req = urllib.request.Request(url, data=json.dumps(body).encode("utf-8"), method="POST",
                                  headers={"Content-Type": "application/json", **headers})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()

def _err_text(raw: bytes) -> str:
    try:
        data = json.loads(raw)
        err = data.get("error") if isinstance(data, dict) else None
        return str(err.get("message") if isinstance(err, dict) else (err if err is not None else data))
    except Exception:  # noqa: BLE001
        return raw.decode("utf-8", "replace")[:300]

class _ProviderError(Exception):
    def __init__(self, status: int | None, message: str):
        super().__init__(f"HTTP {status}: {message}")
        self.status = status

def _request_spec(provider: str, model: str, api_key: str, system: str, user: str) -> tuple:
    """Build (url, headers, body) for one provider/model call."""
    if provider == "gemini":
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
        headers = {"x-goog-api-key": api_key}
        body = {"systemInstruction": {"parts": [{"text": system}]},
                "contents": [{"role": "user", "parts": [{"text": user}]}],
                "generationConfig": {"responseMimeType": "application/json", "temperature": 0.4, "maxOutputTokens": 16384}}
    elif provider == "anthropic":
        url = "https://api.anthropic.com/v1/messages"
        headers = {"x-api-key": api_key, "anthropic-version": "2023-06-01"}
        body = {"model": model, "max_tokens": 8000, "system": system, "messages": [{"role": "user", "content": user}]}
    else:  # openai; gpt-5 models reject `temperature`, so it's omitted
        url = "https://api.openai.com/v1/chat/completions"
        headers = {"Authorization": f"Bearer {api_key}"}
        body = {"model": model, "response_format": {"type": "json_object"}, "max_completion_tokens": 16384,
                "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]}
    return url, headers, body

def _extract_text(provider: str, status: int, raw: bytes) -> str:
    data = json.loads(raw)
    if provider == "gemini":
        candidates = data.get("candidates") or []
        if not candidates:
            raise _ProviderError(status, "no candidates in response")
        parts = ((candidates[0].get("content") or {}).get("parts")) or []
        text = "".join(p.get("text", "") for p in parts if not p.get("thought"))
    elif provider == "anthropic":
        blocks = data.get("content") or []
        text = "".join(b.get("text", "") for b in blocks if isinstance(b, dict) and b.get("type") == "text")
    else:
        choices = data.get("choices") or []
        if not choices:
            raise _ProviderError(status, "no choices in response")
        text = (choices[0].get("message") or {}).get("content", "")
    if not text.strip():
        raise _ProviderError(status, "empty text in response")
    return text

def _call_provider(provider: str, model: str, api_key: str, system: str, user: str, timeout: int) -> str:
    url, headers, body = _request_spec(provider, model, api_key, system, user)
    status, raw = _post_json(url, headers, body, timeout)
    if status >= 400:
        raise _ProviderError(status, _err_text(raw))
    return _extract_text(provider, status, raw)

def _try_provider(provider: str, models: list, api_key: str, system: str, user: str, timeout: int):
    """404/model-not-found -> next model now; 429/5xx -> retry after 10s and 25s, then next model; else -> next model."""
    backoff = (10, 25)  # overload spikes (429/503) are usually brief -> wait, then retry
    for model in models:
        for attempt in range(len(backoff) + 1):
            try:
                return _call_provider(provider, model, api_key, system, user, timeout), model
            except _ProviderError as e:
                retryable = e.status == 429 or (e.status is not None and e.status >= 500)
                if retryable and attempt < len(backoff):
                    log(f"{provider}/{model}: {e} -- retry {attempt + 1} in {backoff[attempt]}s")
                    time.sleep(backoff[attempt])
                    continue
                log(f"{provider}/{model}: {e} -- trying next model")
                break
            except Exception as e:  # noqa: BLE001 - timeouts, bad JSON, etc.
                log(f"{provider}/{model}: {e} -- trying next model")
                break
    return None, None

def _select_provider(site_cfg: dict) -> str | None:
    ai_cfg = site_cfg.get("ai", {}) if isinstance(site_cfg, dict) else {}
    provider = ai_cfg.get("provider", "auto")
    if provider != "auto":
        return provider if os.environ.get(ENV_KEYS.get(provider, "")) else None
    for p in ai_cfg.get("order", ["gemini", "anthropic", "openai"]):
        if os.environ.get(ENV_KEYS.get(p, "")):
            return p
    return None

SYSTEM_PROMPT = (
    "You are the editor of a pre-market briefing for a professional Indian equity analyst who reads it at "
    "roughly 7am IST, before the NSE opens at 9:15 IST. Write crisp, specific and factual copy -- no hype, "
    "no filler, no generic market commentary. Use ONLY the facts, numbers, ids and dates present in the "
    "input below; NEVER invent levels, dates, events or quotes. Refer to time correctly relative to the "
    "given IST date (\"overnight\" for US markets that closed after Indian hours, \"yesterday\" for the "
    "prior Indian session). Highlight what could move the Nifty, sectors or specific stocks at today's "
    "open. Write for a professional equity research analyst: name the specific companies, sectors and "
    "instruments affected and the likely direction; prefer large caps, market-wide drivers (flows, rates, "
    "crude, rupee, global cues), earnings, broker rating changes, deals, orders and policy over small-cap or "
    "SME/IPO chatter. The watchlist must include results and board meetings listed in the EXCHANGE CALENDAR "
    "for today/tomorrow (large caps first) and any data releases or policy events in the input. Each "
    "why_it_matters should state the read-through for stocks/sectors (e.g. 'positive for OMCs, negative for "
    "paints'). For technology, focus on AI developments with market relevance. IMPORTANT: every figure you "
    "write is machine-checked against the input; any sentence or item containing a number that does not "
    "appear in the input is deleted, so copy figures exactly and do not compute new ones. "
    "Respond with STRICT JSON only "
    "(no markdown fences, no prose outside the JSON object), exactly this shape:\n"
    '{"headline": "<= 90 chars", "summary": "2-3 sentences", "mood": "risk-on"|"risk-off"|"mixed", '
    '"takeaways": [{"text": "<= 30 words", "section": "india|world|tech", "story_ids": ["<id from input>"]}] '
    '(5-7 items), "sections": {"india": "3-4 sentences", "world": "3-4 sentences", "tech": "3-4 sentences"}, '
    '"watchlist": [{"label": "short label", "detail": "event/data/level", "section": "india|world|tech"}] '
    '(3-6 items, only ones supported by the input), "stories": [{"id": "<id from input>", '
    '"ai_summary": "1-2 sentences", "why_it_matters": "<= 20 words, analyst angle"}]}'
)

def _age_str(published: str | None, now_utc: datetime) -> str:
    if not published:
        return "time unknown"
    try:
        hours = (now_utc - datetime.fromisoformat(published)).total_seconds() / 3600
        if hours < 1:
            return f"{max(1, int(hours * 60))}m ago"
        return f"{int(hours)}h ago" if hours < 48 else f"{int(hours / 24)}d ago"
    except Exception:  # noqa: BLE001
        return "time unknown"

def _digest_section(section: dict, n: int, now_utc: datetime) -> str:
    lines = []
    for story in (section.get("stories") or [])[:n]:
        age = _age_str(story.get("published"), now_utc)
        sub = story.get("subsection") or "-"
        snippet = (story.get("summary") or "")[:200].strip()
        lines.append(f"[{story.get('id', '')}] ({story.get('source', '?')}, {age}, {sub}) "
                      f"{(story.get('title') or '').strip()} -- {snippet}")
    return "\n".join(lines) if lines else "(no stories)"

SNAPSHOT_SYMBOLS = [
    ("^NSEI", "Nifty 50"), ("^BSESN", "Sensex"), ("^NSEBANK", "Bank Nifty"), ("^INDIAVIX", "India VIX"),
    ("INR=X", "USD/INR"), ("^GSPC", "S&P 500"), ("^IXIC", "Nasdaq"), ("^DJI", "Dow Jones"),
    ("ES=F", "S&P 500 Futures"), ("NQ=F", "Nasdaq Futures"), ("YM=F", "Dow Futures"), ("^N225", "Nikkei 225"),
    ("^HSI", "Hang Seng"), ("BZ=F", "Brent Crude"), ("GC=F", "Gold"), ("^TNX", "US 10Y Yield"), ("DX-Y.NYB", "Dollar Index"),
]

def _find_item(markets: dict | None, symbol: str) -> dict | None:
    if not markets:
        return None
    for g in markets.get("groups", []):
        for it in g.get("items", []):
            if it.get("symbol") == symbol:
                return it
    return None

def _market_snapshot(markets: dict | None) -> str:
    if not markets:
        return "(market data unavailable)"
    lines = []
    for sym, label in SNAPSHOT_SYMBOLS:
        it = _find_item(markets, sym)
        if not it or it.get("value") is None:
            continue
        pct = it.get("change_pct")
        pct_s = f"{pct:+.2f}%" if pct is not None else "n/a"
        bps = f" ({it['change_bps']:+.1f} bps)" if it.get("unit") == "%" and it.get("change_bps") is not None else ""
        lines.append(f"{label}: {it['value']} {pct_s}{bps}")
    mv = markets.get("movers") if isinstance(markets, dict) else None
    if mv:
        lines.append(f"{mv.get('label')} breadth ({mv.get('as_of')}): {mv.get('advances')} up / {mv.get('declines')} down")
        if mv.get("gainers"):
            lines.append("Top gainers: " + ", ".join(f"{g['name']} {g['change_pct']:+.2f}%" for g in mv["gainers"]))
        if mv.get("losers"):
            lines.append("Top losers: " + ", ".join(f"{g['name']} {g['change_pct']:+.2f}%" for g in mv["losers"]))
    fl = markets.get("flows") if isinstance(markets, dict) else None
    if fl and fl.get("fii") and fl.get("dii"):
        lines.append(f"Institutional flows {fl.get('date')} ({fl.get('unit', 'Rs crore')}): "
                     f"FII/FPI net {fl['fii'].get('net')}, DII net {fl['dii'].get('net')}")
    return "\n".join(lines) if lines else "(market data unavailable)"

def _build_user_prompt(sections: list, markets: dict | None, site_cfg: dict, now_utc: datetime) -> str:
    n = (site_cfg.get("ai", {}) if isinstance(site_cfg, dict) else {}).get("stories_per_section", 18)
    parts = [f"Today: {now_utc.astimezone(IST).strftime('%A, %d %B %Y')} (IST)", "",
             "MARKET SNAPSHOT (values as of last close/session):", _market_snapshot(markets)]
    for section in sections or []:
        parts += ["", f"SECTION {section.get('id')} ({section.get('title')}):", _digest_section(section, n, now_utc)]
    fil = next((sec.get("filings") for sec in sections or [] if sec.get("id") == "india"), None) or {}
    cal = fil.get("calendar") or []
    if cal:
        parts += ["", "EXCHANGE CALENDAR (NSE board meetings, next 7 days):"]
        parts += [f"- {c['date']} · {c['company']} · {c['purpose']}" for c in cal[:12]]
    anns = [x for x in (fil.get("announcements") or []) if x.get("largecap")][:8]
    if anns:
        parts += ["", "EXCHANGE FILINGS (last 30h, large caps):"]
        parts += [f"- {x['company']} · {x['subject']} · {x['detail']}" for x in anns]
    acts = fil.get("actions") or []
    if acts:
        parts += ["", "CORPORATE ACTIONS (ex-dates, next 7 days):"]
        parts += [f"- {x['ex_date']} · {x['company']} · {x['purpose']}" for x in acts[:6]]
    k = (site_cfg.get("ai", {}) if isinstance(site_cfg, dict) else {}).get("enrich_top_per_section", 10)
    ids = [s.get("id") for sec in sections or [] for s in (sec.get("stories") or [])[:k] if s.get("id")]
    if ids:
        parts += ["", f"REQUIRED: the \"stories\" array must contain one entry for EACH of these {len(ids)} ids "
                      "(ai_summary + why_it_matters): " + ", ".join(ids)]
    return "\n".join(parts)

def _extract_json(text: str) -> dict:
    t = re.sub(r"^```(?:json)?\s*", "", text.strip())
    t = re.sub(r"\s*```\s*$", "", t).strip()
    try:
        return json.loads(t)
    except json.JSONDecodeError:
        pass
    m = re.search(r"\{.*\}", t, re.DOTALL)
    if not m:
        raise ValueError("no JSON object found in model output")
    blob = m.group(0)
    try:
        return json.loads(blob)
    except json.JSONDecodeError:
        return json.loads(re.sub(r",\s*([}\]])", r"\1", blob))  # lenient retry: drop trailing commas

def _clip_str(v, maxlen: int | None = None, default: str = "") -> str:
    s = (v if isinstance(v, str) else ("" if v is None else str(v))).strip()
    return (s[:maxlen] if maxlen else s) or default

def _valid_story_ids(sections: list) -> set:
    return {st["id"] for s in (sections or []) for st in (s.get("stories") or []) if st.get("id")}

def _coerce_brief(raw: dict, sections: list, provider: str, model: str) -> dict:
    if not isinstance(raw, dict):
        raise ValueError("model output is not a JSON object")
    valid_ids = _valid_story_ids(sections)
    takeaways = []
    for t in (raw.get("takeaways") or [])[:7]:
        if not isinstance(t, dict):
            continue
        text = _clip_str(t.get("text"), 220)
        if not text:
            continue
        sec = t.get("section") if t.get("section") in SECTION_IDS else "india"
        ids = [i for i in (t.get("story_ids") or []) if isinstance(i, str) and i in valid_ids][:4]
        takeaways.append({"text": text, "section": sec, "story_ids": ids})
    sec_paras = raw.get("sections") if isinstance(raw.get("sections"), dict) else {}
    sections_out = {sid: _clip_str(sec_paras.get(sid), 900) for sid in SECTION_IDS}
    watchlist = []
    for w in (raw.get("watchlist") or [])[:6]:
        if not isinstance(w, dict):
            continue
        label = _clip_str(w.get("label"), 80)
        if not label:
            continue
        watchlist.append({"label": label, "detail": _clip_str(w.get("detail"), 160),
                           "section": w.get("section") if w.get("section") in SECTION_IDS else "india"})
    stories_out, seen = [], set()
    for st in (raw.get("stories") or []):
        sid = st.get("id") if isinstance(st, dict) else None
        if not isinstance(sid, str) or sid not in valid_ids or sid in seen:
            continue
        seen.add(sid)
        stories_out.append({"id": sid, "ai_summary": _clip_str(st.get("ai_summary"), 400),
                             "why_it_matters": _clip_str(st.get("why_it_matters"), 160)})
    return {
        "headline": _clip_str(raw.get("headline"), 90, "Markets brief"),
        "summary": _clip_str(raw.get("summary"), 600),
        "mood": raw.get("mood") if raw.get("mood") in MOODS else None,
        "takeaways": takeaways, "sections": sections_out, "watchlist": watchlist,
        "stories": stories_out,  # popped by make_brief after enrichment; not part of data.json["brief"]
        "generated_by": provider, "model": model,
    }

def _ensure_story_defaults(sections: list) -> None:
    """Every story always carries ai_summary/why_it_matters (null if not enriched)."""
    for section in sections or []:
        for story in section.get("stories") or []:
            story.setdefault("ai_summary", None)
            story.setdefault("why_it_matters", None)

def _apply_enrichment(sections: list, stories_out: list) -> None:
    by_id = {st["id"]: st for st in stories_out}
    for section in sections or []:
        for story in section.get("stories") or []:
            hit = by_id.get(story.get("id"))
            if hit:
                story["ai_summary"] = hit.get("ai_summary") or story.get("ai_summary")
                story["why_it_matters"] = hit.get("why_it_matters") or story.get("why_it_matters")

def make_brief(sections: list, markets: dict | None, site_cfg: dict, now_utc: datetime) -> dict:
    """Returns data.json["brief"]. Never raises -- falls back on any failure."""
    try:
        return _make_brief_impl(sections, markets, site_cfg, now_utc)
    except Exception:  # noqa: BLE001 - absolute last resort
        log("make_brief crashed unexpectedly:\n" + traceback.format_exc())
        return fallback_brief(sections, markets, now_utc)

def _make_brief_impl(sections: list, markets: dict | None, site_cfg: dict, now_utc: datetime) -> dict:
    site_cfg = site_cfg if isinstance(site_cfg, dict) else {}
    ai_cfg = site_cfg.get("ai", {})
    provider = _select_provider(site_cfg)
    if not provider:
        log("no AI provider available (no matching API key in env) -- using fallback brief")
        return fallback_brief(sections, markets, now_utc)
    api_key = os.environ.get(ENV_KEYS[provider])
    models = ai_cfg.get("models", {}).get(provider) or []
    if not api_key or not models:
        log(f"provider {provider} missing key or models config -- using fallback brief")
        return fallback_brief(sections, markets, now_utc)
    user_prompt = _build_user_prompt(sections, markets, site_cfg, now_utc)
    text, model = _try_provider(provider, models, api_key, SYSTEM_PROMPT, user_prompt,
                                 ai_cfg.get("timeout_s", 90))
    if not text:
        log(f"all models exhausted for provider {provider} -- using fallback brief")
        return fallback_brief(sections, markets, now_utc)
    try:
        brief = _coerce_brief(_extract_json(text), sections, provider, model)
    except Exception as e:  # noqa: BLE001
        log(f"could not parse/validate JSON from {provider}/{model}: {e} -- using fallback brief")
        return fallback_brief(sections, markets, now_utc)
    _apply_enrichment(sections, brief.pop("stories", []))
    _ensure_story_defaults(sections)
    brief["verification"] = fact_check(brief, sections, user_prompt)
    log(f"brief generated by {provider}/{model}; verification {brief['verification']}")
    return brief


# ---------------------------------------------------------------- numeric fact-check
# Every figure the model writes must trace back to the data we sent it. Anything that doesn't
# is removed (sentence / list item) so the paper never prints an invented number.

_FIG_RE = re.compile(r"(?<![\w.$₹€£])[-−+]?[$₹€£]?\d[\d,]*(?:\.\d+)?%?(?![\w.]*\d)(?![A-Za-z])")
_CORPUS_NUM_RE = re.compile(r"\d[\d,]*(?:\.\d+)?")
_SENT_SPLIT_RE = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9₹$\"'(])")


def _num(token: str) -> tuple[float, int] | None:
    t = token.replace(",", "").replace("−", "-").strip("$₹€£%+-")
    t = t.lstrip("$₹€£")
    try:
        val = abs(float(t))
    except ValueError:
        return None
    dec = len(t.split(".")[1]) if "." in t else 0
    return val, dec


class _Corpus:
    def __init__(self, text: str):
        vals = []
        for m in _CORPUS_NUM_RE.finditer(text):
            try:
                vals.append(abs(float(m.group(0).replace(",", ""))))
            except ValueError:
                pass
        self.vals = sorted(set(vals))

    def supports(self, val: float, dec: int) -> bool:
        """True if some input number rounds to `val` at the precision it was written with
        (1.6 <- 1.63, 23,063 <- 23,063.1, 95.95 <- 95.945)."""
        tol = 0.5 * 10 ** -dec + 1e-9
        i = bisect.bisect_left(self.vals, val - tol)
        return i < len(self.vals) and self.vals[i] <= val + tol


def _ignorable(val: float, dec: int) -> bool:
    return dec == 0 and (val <= 31 or 1990 <= val <= 2035)


def _unsupported(text: str, corpus: _Corpus, counter: list) -> bool:
    bad = False
    for m in _FIG_RE.finditer(text or ""):
        parsed = _num(m.group(0))
        if not parsed or _ignorable(*parsed):
            continue
        counter[0] += 1
        if not corpus.supports(*parsed):
            log(f"fact-check: unsupported figure {m.group(0)!r} in: {text[:90]!r}")
            bad = True
    return bad


def _clean_paragraph(text, corpus, counter, removed) -> str:
    if not text:
        return text
    keep = []
    for sent in _SENT_SPLIT_RE.split(text):
        if _unsupported(sent, corpus, counter):
            removed[0] += 1
        else:
            keep.append(sent)
    return " ".join(keep).strip()


def fact_check(brief: dict, sections: list, corpus_text: str) -> dict:
    """Mutates brief + story enrichments in place. Returns the verification summary."""
    corpus = _Corpus(corpus_text)
    counter, removed = [0], [0]
    lead = next((s["stories"][0]["title"] for s in sections or [] if s.get("stories")), None)
    if _unsupported(brief.get("headline", ""), corpus, counter):
        removed[0] += 1
        brief["headline"] = lead or ""
    brief["summary"] = _clean_paragraph(brief.get("summary", ""), corpus, counter, removed)
    for key, para in list((brief.get("sections") or {}).items()):
        brief["sections"][key] = _clean_paragraph(para, corpus, counter, removed)
    for key, fields in (("takeaways", ("text",)), ("watchlist", ("label", "detail"))):
        kept = []
        for item in brief.get(key) or []:
            if any(_unsupported(item.get(f, ""), corpus, counter) for f in fields):
                removed[0] += 1
            else:
                kept.append(item)
        brief[key] = kept
    for sec in sections or []:
        for st in sec.get("stories") or []:
            if st.get("ai_summary"):
                st["ai_summary"] = _clean_paragraph(st["ai_summary"], corpus, counter, removed) or None
            if st.get("why_it_matters") and _unsupported(st["why_it_matters"], corpus, counter):
                removed[0] += 1
                st["why_it_matters"] = None
    return {"status": "verified" if removed[0] == 0 else "partial",
            "numbers_checked": counter[0], "removed": removed[0]}

_WATCH_PATTERNS = [
    (re.compile(r"\b(q[1-4]\s*results|earnings|results on|to announce results|results preview)\b", re.I), "Results/Earnings"),
    (re.compile(r"\b(mpc|monetary policy committee|rbi policy|fed meeting|fomc|rate decision)\b", re.I), "Policy Decision"),
    (re.compile(r"\b(cpi|gdp|pmi|iip|jobs report|payrolls|inflation data)\b", re.I), "Data Release"),
    (re.compile(r"\b(ipo|to list on|listing on|debuts? on the (nse|bse))\b", re.I), "IPO/Listing"),
    (re.compile(r"\bboard meeting\b", re.I), "Board Meeting"),
]

def _fallback_watchlist(sections: list) -> list:
    out, seen = [], set()
    for section in sections or []:
        for story in section.get("stories") or []:
            title = (story.get("title") or "").strip()
            if not title or title in seen:
                continue
            for pattern, label in _WATCH_PATTERNS:
                if pattern.search(title):
                    out.append({"label": label, "detail": title[:160], "section": section.get("id")})
                    seen.add(title)
                    break
            if len(out) >= 5:
                return out
    return out

def _fallback_mood(markets: dict | None) -> str | None:
    if not markets:
        return None
    ups = downs = 0
    for sym in ("^GSPC", "^IXIC", "^NSEI", "ES=F"):
        it = _find_item(markets, sym)
        pct = it.get("change_pct") if it else None
        if pct is None:
            continue
        ups, downs = (ups + 1, downs) if pct > 0 else ((ups, downs + 1) if pct < 0 else (ups, downs))
    if ups == 0 and downs == 0:
        return None
    return "risk-on" if ups > downs else ("risk-off" if downs > ups else "mixed")

def _fallback_summary(sections: list, markets: dict | None) -> str:
    parts = []
    if markets:
        us_bits, signs = [], set()
        for sym, label in (("^GSPC", "S&P 500"), ("^IXIC", "Nasdaq"), ("^DJI", "Dow")):
            it = _find_item(markets, sym)
            pct = it.get("change_pct") if it else None
            if pct is None:
                continue
            us_bits.append(f"{label} {pct:+.2f}%")
            signs.add(pct >= 0)
        if us_bits:
            lead = ("Wall Street closed higher" if signs == {True} else
                    "Wall Street closed lower" if signs == {False} else "Wall Street ended mixed")
            parts.append(f"{lead}: " + ", ".join(us_bits) + ".")
        bits = []
        brent = _find_item(markets, "BZ=F")
        if brent and brent.get("value") is not None:
            pct = brent.get("change_pct")
            bits.append(f"Brent ${brent['value']}" + (f" ({pct:+.1f}%)" if pct is not None else ""))
        inr = _find_item(markets, "INR=X")
        if inr and inr.get("value") is not None:
            bits.append(f"USD/INR {inr['value']}")
        if bits:
            parts.append(", ".join(bits) + ".")
    story_count = sum(len(s.get("stories") or []) for s in (sections or []))
    if story_count:
        src_count = len({st.get("source") for s in sections for st in (s.get("stories") or []) if st.get("source")})
        parts.append(f"{story_count} stories across {src_count} sources.")
    return " ".join(parts) if parts else "No market or news data available for this edition."

def fallback_brief(sections: list, markets: dict | None, now_utc: datetime) -> dict:
    """Deterministic, network-free brief. Must work with markets=None and sections=[]. Never raises."""
    try:
        return _fallback_impl(sections, markets, now_utc)
    except Exception:  # noqa: BLE001 - absolute last resort
        log("fallback_brief crashed unexpectedly:\n" + traceback.format_exc())
        return {"headline": "Markets brief unavailable", "summary": "", "mood": None,
                "takeaways": [], "sections": {"india": "", "world": "", "tech": ""},
                "watchlist": [], "generated_by": "fallback", "model": None, "verification": {"status": "n/a", "numbers_checked": 0, "removed": 0}}

def _fallback_impl(sections: list, markets: dict | None, now_utc: datetime) -> dict:  # noqa: ARG001 (now_utc kept for interface parity)
    sections = sections or []
    _ensure_story_defaults(sections)
    by_id = {s.get("id"): s for s in sections}
    india_stories = (by_id.get("india") or {}).get("stories") or []
    lead = india_stories[0] if india_stories else next(
        (st[0] for s in sections if (st := s.get("stories") or [])), None)
    headline = _clip_str(lead.get("title") if lead else None, 90, "Markets brief")
    takeaways = []
    for sid, count in (("india", 3), ("world", 2), ("tech", 2)):
        for story in ((by_id.get(sid) or {}).get("stories") or [])[:count]:
            title = (story.get("title") or "").strip()
            if title:
                takeaways.append({"text": title[:220], "section": sid,
                                   "story_ids": [story["id"]] if story.get("id") else []})
    sections_out = {}
    for sid in SECTION_IDS:
        titles = [st.get("title", "").strip() for st in ((by_id.get(sid) or {}).get("stories") or [])[:3] if st.get("title")]
        sections_out[sid] = f"Top stories: {'; '.join(titles)}." if titles else ""
    return {
        "headline": headline, "summary": _fallback_summary(sections, markets), "mood": _fallback_mood(markets),
        "takeaways": takeaways[:7], "sections": sections_out, "watchlist": _fallback_watchlist(sections),
        "generated_by": "fallback", "model": None,
        "verification": {"status": "n/a", "numbers_checked": 0, "removed": 0},
    }

if __name__ == "__main__":
    # Standalone smoke test: no key in env -> fallback; exits 0 even fully offline.
    demo = [{"id": "india", "title": "India", "stories": [
                {"id": "demo0001aa", "title": "Nifty ends flat ahead of RBI MPC decision",
                 "summary": "Benchmarks closed little changed awaiting tomorrow's policy verdict.",
                 "source": "Demo Wire", "published": None, "subsection": "markets"}]},
            {"id": "world", "title": "World", "stories": []},
            {"id": "tech", "title": "AI & Technology", "stories": []}]
    demo_cfg = {"ai": {"provider": "auto", "order": ["gemini", "anthropic", "openai"],
                        "models": {"gemini": ["gemini-flash-latest"]}, "stories_per_section": 18, "timeout_s": 90}}
    print(json.dumps(make_brief(demo, None, demo_cfg, datetime.now(timezone.utc)), indent=2, ensure_ascii=False))
