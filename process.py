#!/usr/bin/env python3
"""Pole Position — process.py (Agent A)

Turns the raw RSS items from ``feeds.py`` into the three ranked newspaper sections
(``india`` / ``world`` / ``tech``) described in CONTRACT.md: window + quality filtering,
exact dedupe, cross-outlet near-duplicate clustering, India re-homing, subsection
classification, tagging, scoring, per-source diversity and subsection-aware top-12 mixing.

Standard library only. Run standalone with ``python process.py`` for a quick smoke report
(runs on an empty item list — no network needed).
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import re
from datetime import datetime, timedelta, timezone

IST = timezone(timedelta(hours=5, minutes=30))

# ---------------------------------------------------------------- low-value / quality

_LOWQ_RES = [re.compile(p, re.I) for p in (
    r"\bopinion\b", r"\bcolumn\b", r"\bpodcast\b", r"\bexplained\b", r"\bhow to\b",
    r"\bview:\s", r"\beditorial\b",
    r"^\s*\d+\s+(ways|things|reasons|tips|stocks|charts|takeaways|things to know)\b",
)]

_INDIA_FOCUS_WORDS = (
    "india", "indian", "nifty", "sensex", "bank nifty", "rbi", "sebi", "rupee",
    "mumbai", "new delhi", "bengaluru", "bombay", "dalal street", "nse", "bse",
    "adani", "reliance", "tata", "infosys", "tcs", "hdfc", "icici", "modi", "sitharaman",
)
_INDIA_FOCUS_RE = re.compile(r"\b(?:" + "|".join(re.escape(w) for w in _INDIA_FOCUS_WORDS) + r")\b", re.I)

_STOPWORDS = {
    "a", "an", "the", "of", "in", "on", "for", "to", "and", "or", "is", "are", "was", "were",
    "with", "by", "at", "as", "its", "it", "this", "that", "these", "those", "after", "before",
    "amid", "amidst", "over", "under", "into", "from", "up", "down", "new", "says", "said", "say",
    "will", "may", "can", "could", "should", "would", "not", "no", "yet", "also", "than", "more",
    "most", "less", "least", "how", "what", "why", "who", "which", "their", "his", "her", "he",
    "she", "they", "we", "you", "your", "our", "be", "been", "being", "has", "have", "had", "but",
    "if", "so", "out", "about", "between", "amongst", "per", "via", "vs", "during", "while",
    "when", "where", "then", "now", "still", "just", "get", "gets", "getting", "set", "sets",
    "top", "big", "key",
}

# ---------------------------------------------------------------- ~70 canonical tags
# name -> trigger phrases (each wrapped in \b...\b, case-insensitive, at module load below)
TAG_RULES = {
    "RBI": ["rbi", "reserve bank of india"], "SEBI": ["sebi"],
    "Fed": ["fed", "federal reserve", "powell", "fomc"],
    "ECB": ["ecb", "european central bank", "lagarde"],
    "BoJ": ["boj", "bank of japan"], "PBoC": ["pboc", "people's bank of china"],
    "Crude": ["crude", "brent", "wti", "opec"], "Gold": ["gold", "bullion"],
    "Rupee": ["rupee"], "Dollar": ["dollar", "greenback", "dxy"],
    "Bonds": ["bond", "bonds", "treasury", "treasuries", "yield", "yields", "gilt"],
    "IPO": ["ipo", "public offer"],
    "Earnings": ["earnings", "results", "quarterly results", "profit", "quarterly"],
    "FII": ["fii", "fpi", "foreign investors", "foreign portfolio"],
    "Tariffs": ["tariff", "tariffs"], "China": ["china", "chinese", "beijing"],
    "US": ["united states", "america", "american", "washington"],
    "Japan": ["japan", "japanese", "tokyo", "yen"],
    "Europe": ["europe", "european", "eurozone", "euro"],
    "Banks": ["bank", "banks", "banking", "lender", "lenders"],
    "IT": ["it stocks", "software exporters", "tcs", "infosys", "wipro", "hcl tech", "tech mahindra"],
    "Auto": ["auto", "automobile", "automotive", "maruti", "tata motors", "mahindra", "two-wheeler"],
    "Pharma": ["pharma", "pharmaceutical", "pharmaceuticals", "drugmaker", "drug maker"],
    "FMCG": ["fmcg", "consumer goods", "hindustan unilever"],
    "Metals": ["metal", "metals", "steel", "aluminium", "copper", "zinc"],
    "Realty": ["realty", "real estate", "housing sector"],
    "Energy": ["energy sector", "renewable", "renewables", "power sector", "solar"],
    "Telecom": ["telecom", "airtel", "jio", "vodafone idea", "5g"],
    "Defence": ["defence", "defense", "military hardware"],
    "Infra": ["infrastructure", "infra"],
    "PSU": ["psu", "public sector undertaking", "public sector bank"],
    "Adani": ["adani"], "Reliance": ["reliance"], "Tata": ["tata group", "tata sons", "tata"],
    "Inflation": ["inflation", "cpi", "wpi", "price rise"],
    "GDP": ["gdp", "gross domestic product"],
    "Jobs": ["jobs", "payrolls", "employment", "unemployment", "layoffs", "hiring"],
    "Geopolitics": ["geopolitical", "geopolitics", "war", "sanctions", "ukraine", "russia",
                     "israel", "iran", "gaza", "conflict"],
    "M&A": ["merger", "mergers", "acquisition", "acquisitions", "takeover", "m&a"],
    "Crypto": ["crypto", "bitcoin", "ethereum", "cryptocurrency"],
    "AI": ["ai", "artificial intelligence", "generative ai", "genai"],
    "OpenAI": ["openai", "chatgpt"], "Anthropic": ["anthropic", "claude"],
    "Google": ["google", "alphabet", "gemini"], "Nvidia": ["nvidia"],
    "Chips": ["chip", "chips", "semiconductor", "semiconductors", "gpu", "gpus", "tsmc"],
    "Apple": ["apple", "iphone"], "Microsoft": ["microsoft", "copilot", "azure"],
    "Meta": ["meta", "facebook", "instagram", "whatsapp"], "Amazon": ["amazon", "aws"],
    "Tesla": ["tesla", "musk"], "Startups": ["startup", "startups", "unicorn"],
    "Funding": ["funding", "series a", "series b", "series c", "venture capital", "venture funding"],
    "Regulation": ["regulation", "regulator", "regulators", "antitrust", "compliance"],
    "Cybersecurity": ["cybersecurity", "hack", "hacked", "hackers", "breach", "ransomware"],
    "Nifty": ["nifty", "bank nifty", "nifty50", "nifty 50"], "Sensex": ["sensex"],
    "Mutual Funds": ["mutual fund", "mutual funds", "sip"],
    "Budget": ["union budget", "budget session", "fiscal budget"],
    "Trade": ["trade deficit", "exports", "imports", "trade balance"],
    "PMI": ["pmi", "manufacturing activity", "factory output"],
    "Elections": ["election", "elections", "poll results", "assembly polls"],
    "Airlines": ["airline", "airlines", "aviation"],
    "Insurance": ["insurance", "insurer", "insurers"],
    "Retail": ["retail sales", "retailer", "retailers"],
    "Data Centers": ["data center", "data centers", "datacenter", "datacenters"],
    "Cloud": ["cloud computing", "cloud provider"],
    "Interest Rates": ["interest rate", "interest rates", "rate cut", "rate hike", "repo rate"],
    "Recession": ["recession", "slowdown", "hard landing"],
    "Quantum": ["quantum computing", "quantum chip"],
}


def _wb(phrase: str):
    return re.compile(r"\b" + re.escape(phrase) + r"\b", re.I)


TAG_PATTERNS = [(name, [_wb(p) for p in phrases]) for name, phrases in TAG_RULES.items()]


# ---------------------------------------------------------------- text / token helpers

def _normalize_title_key(title: str) -> str:
    t = re.sub(r"[^a-z0-9\s]", " ", (title or "").lower())
    return re.sub(r"\s+", " ", t).strip()


def _stem(word: str) -> str:
    if len(word) > 4 and word.endswith("ies"):
        return word[:-3] + "y"
    if len(word) > 4 and (word.endswith("ches") or word.endswith("shes") or word.endswith("xes")):
        return word[:-2]
    if len(word) > 4 and word.endswith("s") and not word.endswith("ss"):
        return word[:-1]
    return word


def _tokenize(title: str) -> set:
    words = re.sub(r"[^a-z0-9\s]", " ", (title or "").lower()).split()
    return {_stem(w) for w in words if len(w) >= 3 and w not in _STOPWORDS}


def _truncate(text: str, limit: int = 420) -> str:
    text = (text or "").strip()
    if len(text) <= limit:
        return text
    cut = text[:limit]
    sp = cut.rfind(" ")
    if sp > 0:
        cut = cut[:sp]
    return cut.rstrip(" .,;:-") + "…"


def _passes_quality_filters(item: dict, low_value_patterns: list) -> bool:
    title = (item.get("title") or "").strip()
    if len(title) < 25:
        return False
    hay = f"{title} {item.get('url', '')}".lower()
    return not any(pat in hay for pat in low_value_patterns)


def _best_of(items: list) -> dict:
    """Tie-break for both exact-dedupe and cluster-primary selection: best tier, then has a
    summary, then earliest published (undated sorts last, since we can't call it 'earliest')."""
    far_future = datetime.max.replace(tzinfo=timezone.utc, microsecond=0) - timedelta(days=1)

    def key(it):
        return (it.get("tier", 3), 0 if (it.get("summary") or "").strip() else 1,
                it.get("published") or far_future)

    return min(items, key=key)


def _exact_dedupe(items: list) -> list:
    by_url: dict = {}
    for it in items:
        by_url.setdefault(it["url"].strip().rstrip("/"), []).append(it)
    stage1 = [_best_of(g) for g in by_url.values()]
    by_title: dict = {}
    for it in stage1:
        by_title.setdefault(_normalize_title_key(it["title"]), []).append(it)
    return [_best_of(g) for g in by_title.values()]


# ---------------------------------------------------------------- clustering (union-find)

def _cluster_items(items: list) -> list:
    """Group near-duplicate stories across outlets. Same story if title-token Jaccard >= 0.45
    OR overlap >= 0.6 of the shorter title's token count, requiring >= 3 shared tokens."""
    n = len(items)
    if n <= 1:
        return [[it] for it in items]
    parent = list(range(n))

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb

    tokens = [_tokenize(it["title"]) for it in items]
    for i in range(n):
        ti = tokens[i]
        if not ti:
            continue
        for j in range(i + 1, n):
            tj = tokens[j]
            if not tj:
                continue
            shared = ti & tj
            if len(shared) < 3:
                continue
            jaccard = len(shared) / len(ti | tj)
            overlap = len(shared) / min(len(ti), len(tj))
            if jaccard >= 0.45 or overlap >= 0.6:
                union(i, j)

    groups: dict = {}
    for i in range(n):
        groups.setdefault(find(i), []).append(items[i])
    return list(groups.values())


# ---------------------------------------------------------------- subsection / tags / score

def _build_keyword_patterns(section_cfg: dict) -> dict:
    return {
        sub["id"]: [_wb(kw) for kw in sub.get("keywords", [])]
        for sub in section_cfg.get("subsections", [])
    }


def _classify_subsection(section_cfg: dict, hint, text_lower: str, sub_patterns: dict) -> str:
    subs = section_cfg["subsections"]
    scores = {}
    for sub in subs:
        sid = sub["id"]
        cnt = sum(1 for pat in sub_patterns.get(sid, []) if pat.search(text_lower))
        if hint == sid:
            cnt += 1  # feed hint breaks ties; keywords decide
        scores[sid] = cnt
    best = max(scores.values())
    tied = [sid for sid, sc in scores.items() if sc == best]
    if len(tied) == 1:
        return tied[0]
    if hint in tied:
        return hint
    return subs[0]["id"]


def _match_tags(text_lower: str) -> list:
    tags = []
    for name, pats in TAG_PATTERNS:
        if any(p.search(text_lower) for p in pats):
            tags.append(name)
            if len(tags) >= 4:
                break
    return tags


def _importance_score(text_lower: str, high_res: list, medium_res: list) -> float:
    hi = sum(1 for p in high_res if p.search(text_lower))
    med = sum(1 for p in medium_res if p.search(text_lower))
    return min(hi * 8 + med * 3, 30)


def _recency_score(published, now_utc: datetime) -> float:
    if published is None:
        return 10.0  # neutral: roughly what a ~14h-old story would score
    age_h = max(0.0, (now_utc - published).total_seconds() / 3600.0)
    return 25 * (0.5 ** (age_h / 10.0))


# Score = tier weight (premier outlets lead) + coverage bonus (multi-outlet stories are what
# matters to an analyst) + importance keywords + recency decay + small summary/image bumps
# minus a flat penalty for opinion/listicle-y titles. See _build_story for the exact terms.
_TIER_WEIGHT = {1: 30, 2: 22, 3: 12}


_DEBUG_PARTS: dict = {}


def _build_story(cluster: list, section_cfg: dict, sub_patterns: dict,
                  high_res: list, medium_res: list, now_utc: datetime) -> dict | None:
    primary = _best_of(cluster)
    title = (primary.get("title") or "").strip()
    if not title:
        return None

    seen_src = {primary["source_id"]}
    coverage = []
    for it in sorted((x for x in cluster if x is not primary), key=lambda x: x.get("tier", 3)):
        if it["source_id"] in seen_src:
            continue
        seen_src.add(it["source_id"])
        coverage.append({"source": it["source"], "url": it["url"]})
        if len(coverage) >= 5:
            break

    summary = _truncate(primary.get("summary") or "", 420)
    text_lower = f"{title} {summary}".lower()

    hint = primary.get("subsection") or next(
        (it.get("subsection") for it in cluster if it.get("subsection")), None
    )
    subsection = _classify_subsection(section_cfg, hint, text_lower, sub_patterns)
    tags = _match_tags(text_lower)

    tier = primary.get("tier", 3)
    coverage_bonus = min(len(coverage) * 12, 48)
    importance = _importance_score(text_lower, high_res, medium_res)
    recency = _recency_score(primary.get("published"), now_utc)
    summary_bonus = 4 if (summary or "news.google.com" in (primary.get("url") or "")) else 0
    image_bonus = 2 if primary.get("image") else 0
    penalty = -10 if any(p.search(title) for p in _LOWQ_RES) else 0
    score = round(_TIER_WEIGHT.get(tier, 12) + coverage_bonus + importance + recency
                  + summary_bonus + image_bonus + penalty, 1)
    _DEBUG_PARTS[id(primary)] = {"feed": primary.get("feed_id"), "tier": tier, "cov": len(coverage),
                                 "imp": importance, "rec": round(recency, 1), "sum": summary_bonus,
                                 "img": image_bonus, "pen": penalty, "n": len(cluster)}

    published = primary.get("published")
    published_str = published.astimezone(IST).isoformat(timespec="seconds") if published else None
    story_id = hashlib.sha1(_normalize_title_key(title).encode("utf-8")).hexdigest()[:10]

    return {
        "id": story_id, "title": title, "summary": summary, "url": primary["url"],
        "source": primary["source"], "source_id": primary["source_id"],
        "published": published_str, "image": primary.get("image"),
        "subsection": subsection, "tags": tags, "score": score,
        "rank": 0, "is_lead": False, "coverage": coverage,
        "ai_summary": None, "why_it_matters": None,
    }


# ---------------------------------------------------------------- selection / ranking

def _select_section_stories(stories: list, limit: int, per_source_share: float,
                            min_sub_share: float = 0.2) -> list:
    """Score-ranked pick up to `limit`, keeping any one source under `per_source_share` of
    the section where candidates allow; if that leaves the quota unfilled (too few diverse
    sources), the cap is relaxed just enough to fill it. Each subsection is first guaranteed
    ~`min_sub_share` of the slots (when it has candidates) so one busy desk can't crowd out
    Economy/Companies or Geopolitics."""
    if limit <= 0:
        return []
    ordered = sorted(stories, key=lambda s: -s["score"])
    cap = max(1, math.ceil(limit * per_source_share))
    quota = max(1, int(limit * min_sub_share))
    reserved, taken, rsrc = [], set(), {}
    for sub in dict.fromkeys(s["subsection"] for s in ordered):
        n = 0
        for s in ordered:
            if n >= quota:
                break
            if s["subsection"] == sub and rsrc.get(s["source_id"], 0) < cap:
                reserved.append(s); taken.add(id(s)); n += 1
                rsrc[s["source_id"]] = rsrc.get(s["source_id"], 0) + 1
    ordered = reserved + [s for s in ordered if id(s) not in taken]
    selected, leftover, counts = [], [], {}
    for s in ordered:
        if len(selected) >= limit:
            break
        src = s["source_id"]
        if counts.get(src, 0) >= cap:
            leftover.append(s)
            continue
        selected.append(s)
        counts[src] = counts.get(src, 0) + 1
    for s in leftover:
        if len(selected) >= limit:
            break
        selected.append(s)
        counts[s["source_id"]] = counts.get(s["source_id"], 0) + 1
    return selected


def _build_top12_order(stories_sorted: list) -> list:
    """Reorders so the top 12 include >= 2 stories per subsection wherever that subsection
    has >= 2 stories available at all; everything past position 12 stays score-ordered."""
    top_n = min(12, len(stories_sorted))
    if top_n == 0:
        return stories_sorted

    by_sub: dict = {}
    for s in stories_sorted:
        by_sub.setdefault(s["subsection"], []).append(s)  # already desc by score

    top_pool, chosen_ids = [], set()
    for items in by_sub.values():
        if len(items) >= 2:
            for s in items[:2]:
                if s["id"] not in chosen_ids:
                    top_pool.append(s)
                    chosen_ids.add(s["id"])
    for s in stories_sorted:
        if len(top_pool) >= top_n:
            break
        if s["id"] not in chosen_ids:
            top_pool.append(s)
            chosen_ids.add(s["id"])
    top_pool = sorted(top_pool[:top_n], key=lambda s: -s["score"])

    rest = [s for s in stories_sorted if s["id"] not in {t["id"] for t in top_pool}]
    return top_pool + rest


# ---------------------------------------------------------------- public API

def build_sections(raw_items: list, site_cfg: dict, now_utc: datetime) -> tuple[list, dict]:
    sections_cfg = site_cfg.get("sections", [])
    limits = site_cfg.get("limits", {})
    per_source_share = limits.get("per_source_share", 0.35)
    low_value_patterns = [p.lower() for p in site_cfg.get("low_value_patterns", [])]

    now_ist = now_utc.astimezone(IST)
    window_hours = (site_cfg.get("window_hours_monday", 66) if now_ist.weekday() == 0
                    else site_cfg.get("window_hours", 26))
    cutoff = now_utc - timedelta(hours=window_hours)

    importance_cfg = site_cfg.get("importance_keywords", {})
    high_res = [_wb(k) for k in importance_cfg.get("high", [])]
    medium_res = [_wb(k) for k in importance_cfg.get("medium", [])]
    keyword_patterns = {s["id"]: _build_keyword_patterns(s) for s in sections_cfg}

    # per-feed funnel (raw -> in window -> printed) for tuning; rendered nowhere, kept in stats
    funnel: dict = {}
    for it in raw_items:
        f = funnel.setdefault(it.get("feed_id") or "?", {"raw": 0, "window": 0, "printed": 0, "newest_h": None})
        f["raw"] += 1
        pub = it.get("published")
        if pub is not None:
            age = round((now_utc - pub).total_seconds() / 3600, 1)
            f["newest_h"] = age if f["newest_h"] is None else min(f["newest_h"], age)

    # 1) quality + recency window
    filtered = []
    for it in raw_items:
        try:
            if not it.get("title") or not it.get("url") or not it.get("source_id"):
                continue
            if not _passes_quality_filters(it, low_value_patterns):
                continue
            pub = it.get("published")
            if pub is not None and pub < cutoff:
                continue
            filtered.append(it)
            if (it.get("feed_id") or "?") in funnel:
                funnel[it.get("feed_id") or "?"]["window"] += 1
        except Exception:  # noqa: BLE001 - one bad raw item must never sink the build
            continue

    # 2) re-home clearly India-focused stories that came in on a world feed
    for it in filtered:
        if it.get("section") == "world" and _INDIA_FOCUS_RE.search(it.get("title") or ""):
            it["section"] = "india"

    # 3) exact-duplicate collapse (same URL, or same normalized title from a syndication copy)
    deduped = _exact_dedupe(filtered)

    debug_rows: dict = {}
    out_sections = []
    stats_by_source: dict = {}
    stats_by_section: dict = {}
    for section_cfg in sections_cfg:
        sid = section_cfg["id"]
        items = [it for it in deduped if it.get("section") == sid]

        stories = []
        for cluster in _cluster_items(items):
            try:
                story = _build_story(cluster, section_cfg, keyword_patterns[sid],
                                      high_res, medium_res, now_utc)
            except Exception:  # noqa: BLE001 - one bad cluster must never sink the section
                story = None
            if story:
                stories.append(story)
                parts = _DEBUG_PARTS.pop(id(_best_of(cluster)), {})
                debug_rows.setdefault(sid, []).append(
                    dict(parts, score=story["score"], sub=story["subsection"], src=story["source"],
                         title=story["title"][:90]))

        limit = limits.get(sid, len(stories))
        selected = _select_section_stories(stories, limit, per_source_share)
        ordered = _build_top12_order(selected)
        for i, s in enumerate(ordered, start=1):
            s["rank"] = i
            s["is_lead"] = i == 1

        out_sections.append({
            "id": sid, "number": section_cfg["number"], "title": section_cfg["title"],
            "kicker": section_cfg["kicker"],
            "subsections": [{"id": sub["id"], "label": sub["label"]}
                             for sub in section_cfg["subsections"]],
            "stories": ordered,
        })
        stats_by_section[sid] = len(ordered)
        for s in ordered:
            stats_by_source[s["source"]] = stats_by_source.get(s["source"], 0) + 1

    by_source = sorted(
        ({"source": k, "count": v} for k, v in stats_by_source.items()),
        key=lambda x: (-x["count"], x["source"]),
    )
    top_tags = {}
    for section in out_sections:
        counts: dict = {}
        for s in section["stories"]:
            for tag in s.get("tags", []):
                counts[tag] = counts.get(tag, 0) + 1
        top_tags[section["id"]] = sorted(
            ({"tag": k, "count": v} for k, v in counts.items()),
            key=lambda x: (-x["count"], x["tag"]),
        )[:10]

    url_feed = {it["url"]: it.get("feed_id") for it in raw_items if it.get("url")}
    for section in out_sections:
        for st in section["stories"]:
            fid = url_feed.get(st["url"])
            if fid in funnel:
                funnel[fid]["printed"] += 1
    stats = {"by_source": by_source, "by_section": stats_by_section, "top_tags": top_tags,
             "funnel": funnel,
             "_candidates": {k: sorted(v, key=lambda r: -r["score"])[:120] for k, v in debug_rows.items()}}
    return out_sections, stats


def _main() -> None:
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "site.json")
    try:
        with open(path, encoding="utf-8") as fh:
            site_cfg = json.load(fh)
    except Exception as exc:  # noqa: BLE001
        print(f"could not read site.json: {exc}")
        return
    sections, stats = build_sections([], site_cfg, datetime.now(timezone.utc))
    print(f"process.py standalone run: sections={[s['id'] for s in sections]} "
          f"stories={sum(len(s['stories']) for s in sections)}")
    print("stats:", stats)


if __name__ == "__main__":
    _main()
