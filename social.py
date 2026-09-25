"""X (Twitter) posts for the Social Pulse panels — OPTIONAL and cost-capped.

X's API is pay-per-use (prepaid credits, roughly $0.005 per post read). Nothing happens unless
the repository secret X_BEARER_TOKEN is set. With `once_per_day`, the first run of the IST day
fetches and caches to archive/x-<date>.json; later runs that day reuse the cache (no extra cost).

Returns raw items in the same shape feeds.py produces (kind="social", platform="x") so
process.py can rank them next to Reddit/Bluesky. Never raises.
"""
from __future__ import annotations

import html
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
IST = timezone(timedelta(hours=5, minutes=30))
API = "https://api.x.com/2/tweets/search/recent"
MAX_QUERY = 512


def log(msg: str) -> None:
    print(f"[social] {msg}", flush=True)


def _queries(handles: list[str], suffix: str) -> list[str]:
    """Pack handles into as few `(from:a OR from:b …) <suffix>` queries as fit in MAX_QUERY."""
    out, cur = [], []
    for h in handles:
        trial = "(" + " OR ".join(f"from:{x}" for x in cur + [h]) + f") {suffix}"
        if cur and len(trial) > MAX_QUERY:
            out.append("(" + " OR ".join(f"from:{x}" for x in cur) + f") {suffix}")
            cur = [h]
        else:
            cur.append(h)
    if cur:
        out.append("(" + " OR ".join(f"from:{x}" for x in cur) + f") {suffix}")
    return out


def _get(url: str, token: str, timeout: int = 20) -> dict:
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}",
                                               "User-Agent": "pole-position/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def _to_items(payload: dict, section: str, official: set) -> list[dict]:
    users = {u["id"]: u for u in (payload.get("includes") or {}).get("users", [])}
    items = []
    for t in payload.get("data") or []:
        u = users.get(t.get("author_id"), {})
        uname = u.get("username") or "unknown"
        m = t.get("public_metrics") or {}
        try:
            pub = datetime.fromisoformat(t["created_at"].replace("Z", "+00:00"))
        except Exception:  # noqa: BLE001
            pub = None
        text = " ".join(html.unescape(t.get("text") or "").split())
        items.append({
            "title": text[:200], "url": f"https://x.com/{uname}/status/{t.get('id')}", "summary": text,
            "published": pub, "image": None, "source_id": "x", "source": f"@{uname}",
            "feed_id": f"x_{section}", "section": section, "subsection": None, "tier": 3,
            "kind": "social", "aggregator": False, "platform": "x", "author": f"@{uname}",
            "community": None, "text": text,
            "score": int(m.get("like_count", 0)) + int(m.get("retweet_count", 0)),
            "official": uname.lower() in official,
        })
    return items


def _serialise(items: list[dict]) -> list[dict]:
    return [dict(i, published=i["published"].isoformat() if i.get("published") else None) for i in items]


def _deserialise(items: list[dict]) -> list[dict]:
    out = []
    for i in items:
        p = i.get("published")
        out.append(dict(i, published=datetime.fromisoformat(p) if p else None))
    return out


def fetch_x(cfg: dict | None, now_utc: datetime) -> tuple[list[dict], list[dict]]:
    health = {"id": "x_api", "name": "X (Twitter)", "section": "social", "ok": False,
              "items": 0, "ms": 0, "error": None}
    t0 = time.time()
    try:
        xcfg = (cfg or {}).get("x") or {}
        token = os.environ.get("X_BEARER_TOKEN")
        if not token or not xcfg.get("queries"):
            log("X skipped: X_BEARER_TOKEN not set (optional, paid API)")
            return [], []  # not configured is not a failure -> no health entry

        today = now_utc.astimezone(IST).date().isoformat()
        cache = ROOT / "archive" / f"x-{today}.json"
        if xcfg.get("once_per_day", True) and cache.exists():
            items = _deserialise(json.loads(cache.read_text(encoding="utf-8")))
            health.update(ok=True, items=len(items), error="cached (no new reads today)")
            return items, [health]

        n = max(10, min(100, int(xcfg.get("max_results", 20))))
        since = (now_utc - timedelta(hours=30)).strftime("%Y-%m-%dT%H:%M:%SZ")
        items: list[dict] = []
        errors = []
        for section, groups in xcfg["queries"].items():
            official = {h.lower() for h in groups.get("official", [])}
            handles = list(groups.get("official", [])) + list(groups.get("voices", []))
            for q in _queries(handles, "-is:retweet -is:reply"):
                params = {"query": q, "max_results": n, "start_time": since,
                          "tweet.fields": "created_at,public_metrics,author_id",
                          "expansions": "author_id", "user.fields": "username,name,verified"}
                try:
                    items += _to_items(_get(f"{API}?{urllib.parse.urlencode(params)}", token), section, official)
                except urllib.error.HTTPError as e:
                    errors.append(f"HTTP {e.code}")
                except Exception as e:  # noqa: BLE001
                    errors.append(type(e).__name__)
        if items or not errors:
            cache.parent.mkdir(exist_ok=True)
            cache.write_text(json.dumps(_serialise(items), ensure_ascii=False), encoding="utf-8")
        health.update(ok=bool(items) or not errors, items=len(items),
                      error=", ".join(errors) if errors else None)
        log(f"X: {len(items)} posts ({', '.join(errors) or 'ok'})")
        return items, [health]
    except Exception as e:  # noqa: BLE001
        health["error"] = f"{type(e).__name__}: {e}"[:200]
        return [], [health]
    finally:
        health["ms"] = int((time.time() - t0) * 1000)


if __name__ == "__main__":
    cfg = json.loads((ROOT / "social.json").read_text(encoding="utf-8"))
    its, h = fetch_x(cfg, datetime.now(timezone.utc))
    print(h, len(its))
