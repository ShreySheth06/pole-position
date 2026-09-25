#!/usr/bin/env python3
"""Pole Position — builds today's edition.

    python build.py              # full build: news + markets + AI brief -> site/
    python build.py --no-ai      # skip the AI call (fallback brief)
    python build.py --sample     # render dev/sample_data.json only (UI development)

Stdlib only. Output: site/index.html (self-contained), site/data.json, site/archive/*.json
"""
from __future__ import annotations

import argparse
import concurrent.futures as cf
import json
import os
import shutil
import sys
import time
import traceback
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent
SITE = ROOT / "site"
ARCHIVE = ROOT / "archive"
PLACEHOLDER = "__EDITION_DATA__"
KEEP_ARCHIVE_DAYS = 30


def log(msg: str) -> None:
    print(f"[build {datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)


def load_json(name: str, default=None):
    p = ROOT / name
    if not p.exists():
        return default
    with p.open(encoding="utf-8") as f:
        return json.load(f)


def safe(fn, *args, label: str = "", default=None):
    """Run fn(*args); on any exception log it and return default. The build never dies on one component."""
    t = time.time()
    try:
        out = fn(*args)
        log(f"{label} ok ({time.time() - t:.1f}s)")
        return out
    except Exception:  # noqa: BLE001
        log(f"{label} FAILED:\n{traceback.format_exc()}")
        return default


# ---------------------------------------------------------------- calendar helpers
def next_market_open(now_ist: datetime, hhmm: str) -> datetime:
    h, m = (int(x) for x in hhmm.split(":"))
    cand = now_ist.replace(hour=h, minute=m, second=0, microsecond=0)
    if now_ist >= cand + timedelta(hours=6, minutes=15):  # after today's close -> next day
        cand += timedelta(days=1)
    while cand.weekday() >= 5:  # Sat/Sun
        cand += timedelta(days=1)
    return cand


def build_meta(site: dict, now_utc: datetime) -> dict:
    tz = ZoneInfo(site.get("timezone", "Asia/Kolkata"))
    now = now_utc.astimezone(tz)
    mo = next_market_open(now, site.get("market_open", "09:15"))
    repo = os.environ.get("GITHUB_REPOSITORY")
    return {
        "title": site.get("title", "Pole Position"),
        "tagline": site.get("tagline", ""),
        "edition_date": now.date().isoformat(),
        "edition_label": now.strftime("%A, %-d %B %Y") if os.name != "nt" else now.strftime("%A, %d %B %Y"),
        "edition_no": now.timetuple().tm_yday,
        "generated_at": now.isoformat(timespec="seconds"),
        "market_open": mo.isoformat(timespec="seconds"),
        "is_market_day": now.weekday() < 5,
        "sources_ok": 0,
        "sources_total": 0,
        "story_count": 0,
        "ai": {"provider": None, "model": None, "ok": False},
        "repo_url": site.get("repo_url") or (f"https://github.com/{repo}" if repo else None),
        "archive": [],
        "browser_ai": site.get("browser_ai", {}),
        "stale": False,
    }


# ---------------------------------------------------------------- archive
def update_archive(data: dict) -> list[str]:
    ARCHIVE.mkdir(exist_ok=True)
    today = data["meta"]["edition_date"]
    (ARCHIVE / f"{today}.json").write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    files = sorted(ARCHIVE.glob("????-??-??.json"), reverse=True)
    for old in files[KEEP_ARCHIVE_DAYS:]:
        old.unlink(missing_ok=True)
    return [f.stem for f in sorted(ARCHIVE.glob("????-??-??.json"), reverse=True) if f.stem != today]


def latest_archive() -> dict | None:
    files = sorted(ARCHIVE.glob("????-??-??.json"), reverse=True) if ARCHIVE.exists() else []
    for f in files:
        try:
            return json.loads(f.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            continue
    return None


# ---------------------------------------------------------------- render
def render(data: dict) -> None:
    tpl = (ROOT / "template.html").read_text(encoding="utf-8")
    if PLACEHOLDER not in tpl:
        raise SystemExit("template.html is missing the __EDITION_DATA__ placeholder")
    payload = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    payload = payload.replace("</", "<\\/").replace("\u2028", "\\u2028").replace("\u2029", "\\u2029")
    SITE.mkdir(exist_ok=True)
    (SITE / "index.html").write_text(tpl.replace(PLACEHOLDER, payload, 1), encoding="utf-8")
    (SITE / "data.json").write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    (SITE / ".nojekyll").write_text("", encoding="utf-8")
    (SITE / "robots.txt").write_text("User-agent: *\nDisallow: /\n", encoding="utf-8")
    if ARCHIVE.exists():
        shutil.copytree(ARCHIVE, SITE / "archive", dirs_exist_ok=True)
    log(f"rendered site/index.html ({(SITE / 'index.html').stat().st_size / 1024:.0f} KB)")


# ---------------------------------------------------------------- Reddit rotation + cache
# Reddit answers only ~1 unauthenticated request per run from GitHub's servers (HTTP 429 after),
# so each hourly run refreshes ONE section's Reddit feed and reuses cached posts for the others.
def _reddit_cache_path() -> Path:
    return ARCHIVE / "reddit-cache.json"


def reddit_rotation(enabled: list, now_utc: datetime) -> tuple[list, dict | None]:
    reddit = [f for f in enabled if f.get("platform") == "reddit"]
    if len(reddit) <= 1:
        return enabled, (reddit[0] if reddit else None)
    pick = reddit[now_utc.hour % len(reddit)]
    return [f for f in enabled if f.get("platform") != "reddit" or f is pick], pick


def reddit_cache_merge(raw_items: list, health: list, reddit_feeds: list, pick: dict | None,
                       now_utc: datetime) -> tuple[list, list]:
    try:
        path = _reddit_cache_path()
        cache = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    except Exception:  # noqa: BLE001
        cache = {}
    if pick:
        fresh = [i for i in raw_items if i.get("feed_id") == pick["id"]]
        if fresh:
            cache[pick["id"]] = {"at": now_utc.isoformat(), "items": [
                dict(i, published=i["published"].isoformat() if i.get("published") else None) for i in fresh]}
    for f in reddit_feeds:
        if pick and f["id"] == pick["id"] and any(i.get("feed_id") == f["id"] for i in raw_items):
            continue
        entry = cache.get(f["id"])
        if not entry:
            continue
        age_h = (now_utc - datetime.fromisoformat(entry["at"])).total_seconds() / 3600
        if age_h > 30:
            continue
        items = [dict(i, published=datetime.fromisoformat(i["published"]) if i.get("published") else None)
                 for i in entry["items"]]
        raw_items = raw_items + items
        health = [h for h in health if h.get("id") != f["id"]] + [{
            "id": f["id"], "name": f.get("source"), "section": f.get("section"), "ok": True,
            "items": len(items), "ms": 0, "error": f"cached {age_h:.0f}h ago"}]
    try:
        ARCHIVE.mkdir(exist_ok=True)
        _reddit_cache_path().write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")
    except Exception:  # noqa: BLE001
        pass
    return raw_items, health


# ---------------------------------------------------------------- main
def build(no_ai: bool = False) -> dict:
    site = load_json("site.json", {})
    feeds_cfg = load_json("feeds.json", {"feeds": []})
    markets_cfg = load_json("markets.json", {})
    now_utc = datetime.now(timezone.utc)
    meta = build_meta(site, now_utc)

    import feeds, process, markets, ai  # noqa: E401  (flat modules)

    enabled = [f for f in feeds_cfg.get("feeds", []) if f.get("enabled", True)]
    reddit_feeds = [f for f in enabled if f.get("platform") == "reddit"]
    enabled, reddit_pick = reddit_rotation(enabled, now_utc)
    log(f"fetching {len(enabled)} feeds + markets in parallel")
    with cf.ThreadPoolExecutor(4) as ex:
        f_news = ex.submit(safe, feeds.fetch_all, enabled, label="feeds", default=([], []))
        f_mkts = ex.submit(safe, markets.fetch_markets, markets_cfg, now_utc, label="markets", default=None)
        f_flow = ex.submit(safe, markets.fetch_flows, now_utc, label="flows", default=None)
        raw_items, health = f_news.result()
        raw_items, health = reddit_cache_merge(raw_items, health, reddit_feeds, reddit_pick, now_utc)
        market_data = f_mkts.result()
        flows = f_flow.result()

    log(f"raw items: {len(raw_items)}  |  feeds ok: {sum(h.get('ok') for h in health)}/{len(health)}")
    sections, stats = safe(process.build_sections, raw_items, site, now_utc, label="process", default=([], {}))

    story_count = sum(len(s.get("stories", [])) for s in sections)
    if story_count == 0:
        prev = latest_archive()
        if prev:
            log("WARNING: no stories fetched — re-serving the latest archived edition (marked stale)")
            prev.setdefault("meta", {})["stale"] = True
            return prev

    ai_markets = {**market_data, "flows": flows} if market_data else None  # AI also sees FII/DII
    if no_ai:
        brief = ai.fallback_brief(sections, market_data, now_utc)
    else:
        brief = safe(ai.make_brief, sections, ai_markets, site, now_utc, label="ai", default=None) \
            or ai.fallback_brief(sections, market_data, now_utc)

    debug = {"candidates": (stats or {}).pop("_candidates", None), "funnel": (stats or {}).pop("funnel", None)}
    (SITE).mkdir(exist_ok=True)
    (SITE / "debug.json").write_text(json.dumps(debug, ensure_ascii=False, default=str), encoding="utf-8")
    meta.update(
        sources_ok=sum(1 for h in health if h.get("ok")),
        sources_total=len(health),
        story_count=story_count,
        ai={"provider": brief.get("generated_by"), "model": brief.pop("model", None),
            "ok": brief.get("generated_by") not in (None, "fallback")},
    )
    return {"meta": meta, "brief": brief, "markets": market_data, "flows": flows,
            "sections": sections, "stats": stats, "health": health}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-ai", action="store_true")
    ap.add_argument("--sample", action="store_true", help="render dev/sample_data.json (no fetching)")
    ap.add_argument("--no-archive", action="store_true")
    args = ap.parse_args()
    sys.path.insert(0, str(ROOT))

    if args.sample:
        data = json.loads((ROOT / "dev" / "sample_data.json").read_text(encoding="utf-8"))
        render(data)
        return

    data = build(no_ai=args.no_ai)
    if not args.no_archive and not data["meta"].get("stale"):
        data["meta"]["archive"] = update_archive(data)
    render(data)
    m = data["meta"]
    log(f"DONE  {m['edition_label']}  stories={m['story_count']}  sources={m['sources_ok']}/{m['sources_total']}  ai={m['ai']}")
    failed = [h for h in data.get("health", []) if not h.get("ok")]
    for h in failed:
        log(f"  feed down: {h.get('id')}: {h.get('error')}")


if __name__ == "__main__":
    main()
