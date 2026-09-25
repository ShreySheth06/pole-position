# Pole Position — Morning Market Newspaper · Build Contract

A personal morning newspaper for an Indian stock-market analyst. A GitHub Actions cron runs
`python build.py` 3–4×/day (IST mornings). It fetches news RSS + market data, ranks & clusters stories,
asks Gemini for a briefing, and renders ONE self-contained static page `site/index.html` (plus
`site/data.json`) deployed to GitHub Pages. Opened on phone + laptop. No server at runtime.

HARD RULES (all agents)
- **Python 3.10+ standard library ONLY** (urllib, xml.etree, json, concurrent.futures, html, re, datetime, zoneinfo, hashlib). No pip deps.
- **Flat repo root** (no packages). Each agent owns only its files (below). Don't edit others' files.
- The build sandbox has NO internet (proxy 403s every news site). Test with local fixtures in `dev/fixtures/`
  and by monkeypatching the fetch function. Code must be defensive: any single feed/symbol/AI failure
  is logged and skipped, never crashes the build.
- All times shown to the user are IST (`Asia/Kolkata`). Store ISO-8601 strings with offset, e.g. `2026-09-24T06:05:00+05:30`.
- Never invent data. Missing values → `null` and the UI hides that widget.

## Files & owners
| File | Owner | Purpose |
|---|---|---|
| `CONTRACT.md`, `site.json`, `build.py`, `.github/workflows/daily.yml`, `README.md` | lead | config, orchestration, render, deploy |
| `feeds.json`, `feeds.py`, `process.py`, `dev/fixtures/rss/*` , `dev/test_news.py` | Agent A (news) | fetch/parse RSS, clean, dedupe, cluster, classify, rank |
| `markets.json`, `markets.py`, `ai.py`, `dev/fixtures/yahoo/*`, `dev/test_markets_ai.py` | Agent B (markets+AI) | market data, FII/DII, AI briefing |
| `template.html`, `dev/make_sample.py`, `dev/sample_data.json` | Agent C (frontend) | the newspaper UI |

## Python interfaces (build.py calls exactly these)
```python
# feeds.py
def fetch_all(feeds: list[dict], timeout: int = 15, max_workers: int = 16) -> tuple[list[dict], list[dict]]:
    """Returns (raw_items, health). raw item keys:
       title, url, summary (plain text), published (aware datetime in UTC or None), image (url|None),
       source_id, source (display name), feed_id, section, subsection (hint|None), tier (1|2|3)
       health entry: {id, name, section, ok: bool, items: int, ms: int, error: str|None}"""

# process.py
def build_sections(raw_items: list[dict], site_cfg: dict, now_utc: datetime) -> tuple[list[dict], dict]:
    """Returns (sections, stats) exactly as in data.json below (without ai_* fields)."""

# markets.py
def fetch_markets(markets_cfg: dict, now_utc: datetime) -> dict | None   # data.json["markets"]
def fetch_flows(now_utc: datetime) -> dict | None                          # data.json["flows"]

# ai.py
def make_brief(sections: list[dict], markets: dict | None, site_cfg: dict, now_utc: datetime) -> dict:
    """Returns data.json["brief"]. Reads API key from env (GEMINI_API_KEY | ANTHROPIC_API_KEY | OPENAI_API_KEY).
       MUTATES stories in `sections`, adding ai_summary / why_it_matters to the top stories.
       On ANY failure or no key → returns fallback_brief(...) with generated_by="fallback". Never raises."""
def fallback_brief(sections, markets, now_utc) -> dict
```
Each module must also run standalone for testing: `python feeds.py` / `python markets.py` etc. print a short report
(with no network it should fail gracefully and still exit 0).

## site.json (lead-owned, read by process.py & ai.py)
See file. Key parts: `sections` → ids `india`, `world`, `tech`, each with `subsections` (id, label, keywords)
used by process.py to assign a subsection; `window_hours`; `limits`; `importance_keywords`; `ai` settings.

## feeds.json (Agent A)
```json
{"feeds":[{"id":"et_markets","source_id":"et","source":"Economic Times","url":"https://...","section":"india",
           "subsection":"markets","tier":1,"enabled":true}]}
```
`section` ∈ india|world|tech. `subsection` is a hint (may be null → classify by keywords). Google News RSS
search feeds are allowed as aggregator fallbacks for Reuters/Bloomberg (set `"aggregator":true`; real
publisher = the item's `<source>` element; strip trailing " - Publisher" from titles).

## data.json — THE CONTRACT between backend and template.html
```jsonc
{
  "meta": {
    "title": "Pole Position", "tagline": "The Morning Edition · Before the Bell",
    "edition_date": "2026-09-24",                 // IST date
    "edition_label": "Thursday, 24 September 2026",
    "edition_no": 267,                             // day of year
    "generated_at": "2026-09-24T06:05:12+05:30",
    "market_open": "2026-09-24T09:15:00+05:30",   // next NSE open (skip Sat/Sun)
    "is_market_day": true,
    "sources_ok": 38, "sources_total": 45, "story_count": 96,
    "ai": {"provider": "gemini", "model": "gemini-flash-latest", "ok": true},
    "repo_url": "https://github.com/<user>/<repo>" | null,     // for "refresh now" link
    "archive": ["2026-09-23", "2026-09-22"]        // past editions available at archive/<date>.json (may be [])
  },
  "brief": {
    "headline": "Short punchy line for the day (≤ 90 chars)",
    "summary": "2–3 sentence overview of what matters before the open",
    "mood": "risk-on" | "risk-off" | "mixed" | null,
    "takeaways": [ {"text": "one sentence", "section": "india|world|tech", "story_ids": ["a1b2c3d4e5"]} ],  // 5–7
    "sections": {"india": "paragraph", "world": "paragraph", "tech": "paragraph"},
    "watchlist": [ {"label": "RBI MPC decision", "detail": "10:00 IST · repo seen unchanged", "section": "india"} ],  // 3–6 things to watch today
    "generated_by": "gemini" | "anthropic" | "openai" | "fallback"
  },
  "markets": {                                      // null if everything failed
    "as_of": "2026-09-24T06:04:00+05:30",
    "groups": [
      {"id": "india", "label": "India", "items": [ITEM, ...]},
      {"id": "sectors", "label": "Nifty Sectors", "items": [...]},
      {"id": "global", "label": "Global Indices", "items": [...]},     // ITEM.region = "US"|"Europe"|"Asia"
      {"id": "futures", "label": "US Futures", "items": [...]},
      {"id": "fx", "label": "Currencies", "items": [...]},
      {"id": "commodities", "label": "Commodities", "items": [...]},
      {"id": "rates", "label": "Bond Yields", "items": [...]},
      {"id": "tech", "label": "Tech Bellwethers", "items": [...]},
      {"id": "crypto", "label": "Crypto", "items": [...]}
    ]
  },
  // ITEM:
  // {"symbol":"^NSEI","name":"Nifty 50","short":"NIFTY","region":null,"value":25890.15,"change":120.3,
  //  "change_pct":0.47,"prev_close":25769.85,"currency":"INR","decimals":2,"unit":"" ("%" for yields),
  //  "as_of":"2026-09-23","high_52w":26277.35,"low_52w":21743.65,
  //  "spark":[[ "2026-06-24", 24800.2 ], ...]}        // ~3 months of daily closes [date, close], oldest first
  "flows": {                                          // null if unavailable
    "date": "2026-09-23", "unit": "₹ crore",
    "fii": {"buy": 12345.6, "sell": 13456.7, "net": -1111.1},
    "dii": {"buy": 11111.1, "sell": 9999.9, "net": 1111.2}
  },
  "sections": [
    {
      "id": "india", "number": "01", "title": "India", "kicker": "Markets · Economy · Corporate",
      "subsections": [{"id": "markets", "label": "Markets"}, {"id": "economy", "label": "Economy & Policy"}, {"id": "corporate", "label": "Companies"}],
      "stories": [                                    // sorted by rank; 1st is the lead
        {
          "id": "a1b2c3d4e5",                         // sha1(normalized title)[:10]
          "title": "…", "summary": "plain text ≤ 420 chars",
          "url": "https://…", "source": "Economic Times", "source_id": "et",
          "published": "2026-09-24T05:12:00+05:30" | null,
          "image": "https://…" | null,
          "subsection": "markets", "tags": ["RBI", "Banks"],
          "score": 87.5, "rank": 1, "is_lead": true,
          "coverage": [{"source": "Mint", "url": "https://…"}],   // other outlets on same story
          "ai_summary": "1–2 sentences" | null,        // added by ai.py (top stories only)
          "why_it_matters": "≤ 20 words for an equity analyst" | null
        }
      ]
    },
    {"id": "world", "number": "02", "title": "World", "kicker": "Global Markets · Economies · Governments", "subsections": [{"id":"markets","label":"Markets"},{"id":"economy","label":"Economies & Central Banks"},{"id":"politics","label":"Governments & Geopolitics"}], "stories": []},
    {"id": "tech", "number": "03", "title": "AI & Technology", "kicker": "Artificial Intelligence · Big Tech · Startups", "subsections": [{"id":"ai","label":"Artificial Intelligence"},{"id":"bigtech","label":"Big Tech & Chips"},{"id":"startups","label":"Indian Tech & Startups"}], "stories": []}
  ],
  "stats": {
    "by_source": [{"source": "Economic Times", "count": 34}],
    "by_section": {"india": 40, "world": 32, "tech": 24},
    "top_tags": {"india": [{"tag": "RBI", "count": 7}], "world": [...], "tech": [...]}
  },
  "health": [{"id": "et_markets", "name": "ET Markets", "section": "india", "ok": true, "items": 50, "ms": 430, "error": null}]
}
```
Story counts: india ≤ 40, world ≤ 36, tech ≤ 30 (see site.json limits). ~20–40 KB of stories.

### Addenda
- `meta.browser_ai` = `{"default_provider":"gemini","models":{"gemini":"gemini-flash-latest","anthropic":"claude-haiku-4-5","openai":"gpt-5-mini"}}` (copied from site.json; used by the in-page "Ask the Editor").
- `meta.stale` = true when the build failed to fetch any news and re-served the last archived edition.
- Market ITEMs with `unit: "%"` (bond yields) also carry `"change_bps"` (change in basis points).
- `brief` may carry `"model"` (build.py moves it to `meta.ai.model`).
- The template contains exactly `<script id="edition-data" type="application/json">__EDITION_DATA__</script>`; build.py replaces the placeholder with the JSON.
  Past editions live at `archive/<YYYY-MM-DD>.json` (same schema) next to index.html.

## v2 addendum (25 Sep 2026): social + video sources, accuracy safeguards
### New per-section arrays (process.py fills; template renders; both may be empty [])
```jsonc
"videos": [   // ≤ 8 per section, newest/most relevant first. From YouTube channel RSS.
  {"id": "yt:VIDEOID", "title": "…", "url": "https://www.youtube.com/watch?v=VIDEOID",
   "channel": "CNBC-TV18", "published": "2026-09-24T18:01:00+05:30",
   "thumbnail": "https://i.ytimg.com/vi/VIDEOID/hqdefault.jpg", "views": 12345 | null}
],
"social": [   // ≤ 10 per section. UNVERIFIED chatter — never merged into stories, never sent to the AI brief.
  {"id": "…", "platform": "reddit" | "bluesky", "author": "u/name | @handle",
   "community": "r/IndianStockMarket" | null, "text": "≤ 280 chars plain text", "url": "https://…",
   "published": "…+05:30" | null, "score": 123 | null,          // upvotes / likes when known
   "official": false}                                           // reserved
]
```
### New fields
- story `"verified_by"`: int = number of distinct outlets carrying the story (1 + len(coverage)).
- brief `"verification"`: `{"status": "verified" | "partial" | "n/a", "numbers_checked": 41, "removed": 2}` — every figure in AI text is traced back to the input data; unsupported sentences/items are removed (`n/a` for fallback briefs).
- market ITEM `"stale": bool` (as_of too old) and `"suspect": bool` (implausible move → change/change_pct set to null).
- feeds.json entries gain `"kind": "news" | "video" | "social"` (default news) and `"platform"` for social ("reddit" | "bluesky"). (X/Twitter was removed at the owner's request on 25 Sep 2026.)
- Aggregator (Google News) items whose publisher is not a known trusted outlet are dropped.
