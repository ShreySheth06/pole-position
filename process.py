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
    if len(word) > 5 and word.endswith("ly"):
        return word[:-2]  # weekly -> week, quarterly -> quarter
    return word


_NUM_TOKEN_RE = re.compile(r"\d[\d,]*(?:\.\d+)?")


def _num_token(raw: str):
    """Numbers become fuzzy tokens (2 significant figures) so '$14.88 billion' ~ '$14.9 billion'."""
    try:
        v = float(raw.replace(",", ""))
    except ValueError:
        return None
    if v < 10 or (v.is_integer() and 1990 <= v <= 2035):
        return None
    mag = 10 ** (math.floor(math.log10(v)) - 1)
    return "n" + str(int(round(v / mag) * mag))


def _tokenize(title: str) -> set:
    t = (title or "").lower()
    nums = {n for n in (_num_token(m.group(0)) for m in _NUM_TOKEN_RE.finditer(t)) if n}
    words = re.sub(r"[^a-z0-9\s]", " ", _NUM_TOKEN_RE.sub(" ", t)).split()
    return {_stem(w) for w in words if len(w) >= 3 and w not in _STOPWORDS} | nums


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
    if any(pat in hay for pat in low_value_patterns):
        return False
    return not ANALYST.dropped(title)


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

    size = [1] * n

    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb and size[ra] + size[rb] <= 8:  # guard against chained mega-clusters
            parent[ra] = rb
            size[rb] += size[ra]

    tokens = [_tokenize(it["title"]) for it in items]
    # rarity weighting: words every market story shares (nifty, india, market) count for little,
    # distinctive ones (forex, reserves, a company name, a figure) count for a lot
    df: dict = {}
    for ts in tokens:
        for t in ts:
            df[t] = df.get(t, 0) + 1
    idf = {t: math.log((n + 1) / (c + 0.5)) for t, c in df.items()}
    wsum = [sum(idf[t] for t in ts) or 1.0 for ts in tokens]
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
            w_overlap = sum(idf[t] for t in shared) / min(wsum[i], wsum[j])
            if jaccard >= 0.45 or overlap >= 0.6 or (len(shared) >= 4 and w_overlap >= 0.55):
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


# ---------------------------------------------------------------- analyst relevance model (analyst.json)
# What matters to an equity analyst: earnings, broker calls, deals, orders, policy, macro prints, rates,
# flows, index events, credit, market-wide moves, and large-cap names. Noise (SME IPO chatter, stock
# tips, live blogs, consumer tech) is pushed down; personal-finance/lifestyle items are dropped.

class _Analyst:
    def __init__(self):
        path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "analyst.json")
        try:
            with open(path, encoding="utf-8") as f:
                cfg = json.load(f)
        except Exception:  # noqa: BLE001 - model is optional
            cfg = {}
        comp = lambda ps: [re.compile(p, re.I) for p in ps]  # noqa: E731
        self.signals = [(s["name"], s.get("weight", 5), set(s.get("sections", ["india", "world", "tech"])),
                         comp(s.get("patterns", []))) for s in cfg.get("signals", [])]
        self.noise = [(n["name"], n.get("weight", -10), set(n.get("sections", ["india", "world", "tech"])),
                       comp(n.get("patterns", []))) for n in cfg.get("noise", [])]
        self.drop = comp(cfg.get("drop", []))

        def uni(entries):
            out = []
            for e in entries:
                pats = comp(e.get("aliases", []))
                pats += [re.compile(r"\b" + re.escape(a) + r"\b") for a in e.get("acronyms", [])]  # case-sensitive
                out.append((e["name"], pats))
            return out
        self.universe = uni(cfg.get("universe", []))
        self.global_universe = uni(cfg.get("global_universe", []))

    def dropped(self, text: str) -> bool:
        return any(p.search(text) for p in self.drop)

    def companies(self, text: str, sid: str) -> list:
        pool = self.universe if sid == "india" else self.global_universe + self.universe
        names = [n for n, pats in pool if any(p.search(text) for p in pats)]
        if len(names) > 1 and "Tata Group" in names:
            names.remove("Tata Group")
        return names

    def score(self, text: str, sid: str) -> tuple[float, list, float]:
        hits = [n for n, w, secs, pats in self.signals if sid in secs and any(p.search(text) for p in pats)]
        pos = min(sum(w for n, w, secs, pats in self.signals if n in hits), 30)
        neg = max(sum(w for n, w, secs, pats in self.noise if sid in secs and any(p.search(text) for p in pats)), -30)
        return pos, hits, neg


ANALYST = _Analyst()
_DIGIT_RE = re.compile(r"[0-9]")

_DEBUG_PARTS: dict = {}
_STORY_EXTRA: dict = {}


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
    sid = section_cfg.get("id")
    importance = min(_importance_score(text_lower, high_res, medium_res), 10)
    signal_pts, signal_hits, noise_pts = ANALYST.score(text_lower, sid)
    # wide syndication of IPO/tip chatter isn't significance: damp coverage for noisy stories
    coverage_bonus = min(len(coverage) * 12, 48) * (0.25 if noise_pts <= -12 else 1.0)
    companies = ANALYST.companies(f"{title} {summary}", sid)
    largecap = (8 if sid == "india" else 6) if companies else 0
    specific = 3 if _DIGIT_RE.search(title) else 0
    recency = _recency_score(primary.get("published"), now_utc)
    summary_bonus = 4 if (summary or "news.google.com" in (primary.get("url") or "")) else 0
    image_bonus = 2 if primary.get("image") else 0
    penalty = -10 if any(p.search(title) for p in _LOWQ_RES) else 0
    score = round(_TIER_WEIGHT.get(tier, 12) + coverage_bonus + importance + recency
                  + summary_bonus + image_bonus + penalty
                  + signal_pts + largecap + specific + noise_pts, 1)
    # company-specific news (earnings / deals / orders / broker calls) belongs under Companies
    if sid == "india" and companies and subsection == "markets" and \
            {"Earnings", "Deals & capital", "Orders & capex", "Broker call"} & set(signal_hits) and \
            not {"Macro data", "Rates & central banks", "Flows & positioning"} & set(signal_hits):
        subsection = "corporate"
    tags = (companies[:2] + [t for t in tags if t not in companies[:2]])[:4]
    _STORY_EXTRA[id(primary)] = {"companies": companies[:3], "hits": signal_hits}
    _DEBUG_PARTS[id(primary)] = {"feed": primary.get("feed_id"), "tier": tier, "cov": len(coverage),
                                 "imp": importance, "rec": round(recency, 1), "sum": summary_bonus,
                                 "img": image_bonus, "pen": penalty, "n": len(cluster),
                                 "sig": signal_pts, "hits": signal_hits, "noise": noise_pts,
                                 "cos": companies[:3]}

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
                            min_sub_share: float = 0.2, sub_share: dict | None = None) -> list:
    """Score-ranked pick up to `limit`, keeping any one source under `per_source_share` of
    the section where candidates allow; if that leaves the quota unfilled (too few diverse
    sources), the cap is relaxed just enough to fill it. Each subsection is first guaranteed
    ~`min_sub_share` of the slots (when it has candidates) so one busy desk can't crowd out
    Economy/Companies or Geopolitics."""
    if limit <= 0:
        return []
    ordered = sorted(stories, key=lambda s: -s["score"])
    cap = max(1, math.ceil(limit * per_source_share))
    sub_share = sub_share or {}
    reserved, taken, rsrc = [], set(), {}
    for sub in dict.fromkeys(s["subsection"] for s in ordered):
        n = 0
        for s in ordered:
            if n >= max(1, int(limit * sub_share.get(sub, min_sub_share))):
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
    per_src: dict = {}
    for s in top_pool:
        per_src[s["source_id"]] = per_src.get(s["source_id"], 0) + 1
    for relax in (False, True):  # first pass: max 3 per outlet on the front of the section
        for s in stories_sorted:
            if len(top_pool) >= top_n:
                break
            if s["id"] in chosen_ids or (not relax and per_src.get(s["source_id"], 0) >= 3):
                continue
            top_pool.append(s)
            chosen_ids.add(s["id"])
            per_src[s["source_id"]] = per_src.get(s["source_id"], 0) + 1
    top_pool = sorted(top_pool[:top_n], key=lambda s: -s["score"])

    rest = [s for s in stories_sorted if s["id"] not in {t["id"] for t in top_pool}]
    return top_pool + rest



# ---------------------------------------------------------------- videos & social (kept apart from news)

_CLICKBAIT_RE = re.compile(
    r"world war|\bww3\b|nuclear warning|shock(?:ed|ing|s)?\b|you won'?t believe|exposed|destroy(?:ed|s)?\b|"
    r"slams?\b|epic\b|insane\b|must watch|breaking!!|#shorts|\bshorts\b", re.I)
_SOCIAL_JUNK_RE = re.compile(r"\[(?:removed|deleted)\]|\bnsfw\b|\bmeme\b|shitpost", re.I)
_EMOJI_RE = re.compile("[\U0001F300-\U0001FAFF☀-➿]")
VIDEO_LIMIT, SOCIAL_LIMIT = 8, 10


def _age_h(pub, now_utc):
    return None if pub is None else max(0.0, (now_utc - pub).total_seconds() / 3600.0)


def _ist(pub):
    return pub.astimezone(IST).isoformat(timespec="seconds") if pub else None


def _is_clickbait(title: str) -> bool:
    letters = [c for c in title if c.isalpha()]
    caps = sum(1 for c in letters if c.isupper()) / max(1, len(letters))
    return bool(_CLICKBAIT_RE.search(title)) or caps > 0.6 or len(_EMOJI_RE.findall(title)) > 1


def _pick_videos(items: list, sid: str, relevance_res: list, now_utc) -> list:
    window = 72 if sid == "tech" else 36
    scored = []
    for it in items:
        if it.get("section") != sid:
            continue
        age = _age_h(it.get("published"), now_utc)
        if age is None or age > window:
            continue
        title = it.get("title") or ""
        if _is_clickbait(title) or "#shorts" in (it.get("summary") or "").lower():
            continue
        hits = sum(1 for r in relevance_res if r.search(title.lower()))
        if sid != "tech" and hits == 0:
            continue  # broad news channels: only market/economy-relevant videos
        views = it.get("views") or 0
        score = min(hits, 5) * 3 + math.log10(views + 1) * 2 + 10 * 0.5 ** (age / 12)
        scored.append((score, it))
    scored.sort(key=lambda x: -x[0])
    out, per_ch = [], {}
    for _, it in scored:
        ch = it.get("channel") or it.get("source")
        if per_ch.get(ch, 0) >= 2:
            continue
        per_ch[ch] = per_ch.get(ch, 0) + 1
        out.append({"id": f"yt:{it['video_id']}", "title": it["title"], "url": it["url"],
                    "channel": ch, "published": _ist(it.get("published")),
                    "thumbnail": it.get("thumbnail"), "views": it.get("views")})
        if len(out) >= VIDEO_LIMIT:
            break
    return out


def _pick_social(items: list, sid: str, low_value: list, now_utc) -> list:
    pos: dict = {}
    by_platform: dict = {}
    for it in items:
        if it.get("section") != sid:
            continue
        fid = it.get("feed_id") or "?"
        pos[fid] = pos.get(fid, -1) + 1
        age = _age_h(it.get("published"), now_utc)
        if age is not None and age > (30 if it.get("platform") == "reddit" else 48):
            continue
        title, body = (it.get("title") or "").strip(), (it.get("text") or "").strip()
        hay = f"{title} {body}".lower()
        if _SOCIAL_JUNK_RE.search(hay) or any(p in hay for p in low_value):
            continue
        if it.get("platform") == "reddit":
            text = title if not body else f"{title} — {body}"
        else:
            text = body or title
        text = text if len(text) <= 280 else text[:279].rsplit(" ", 1)[0] + "…"
        if len(text) < 15:
            continue
        # ranking inside a platform: X by engagement, Reddit by its own top-of-day order, else recency
        if it.get("score") is not None:
            rank = -math.log10((it.get("score") or 0) + 1)
        elif it.get("platform") == "reddit":
            rank = pos[fid]
        else:
            rank = age if age is not None else 99
        by_platform.setdefault(it.get("platform"), []).append((rank, it, text))
    for lst in by_platform.values():
        lst.sort(key=lambda x: x[0])
    # interleave platforms so no single network dominates; ≤ 4 per community/author
    out, per_src, queues = [], {}, [list(v) for v in by_platform.values()]
    while queues and len(out) < SOCIAL_LIMIT:
        for q in list(queues):
            if not q:
                queues.remove(q)
                continue
            _, it, text = q.pop(0)
            key = it.get("community") or it.get("author")
            if per_src.get(key, 0) >= 4:
                continue
            per_src[key] = per_src.get(key, 0) + 1
            out.append({"id": hashlib.sha1(it["url"].encode()).hexdigest()[:10],
                        "platform": it.get("platform"), "author": it.get("author"),
                        "community": it.get("community"), "text": text, "url": it["url"],
                        "published": _ist(it.get("published")), "score": it.get("score"),
                        "official": bool(it.get("official"))})
            if len(out) >= SOCIAL_LIMIT:
                break
    return out


# ---------------------------------------------------------------- exchange filings (NSE archive RSS)
# Primary-source disclosures: material announcements, the results/board-meeting calendar and
# corporate actions. Routine compliance filings are dropped; large caps float to the top.

_ROUTINE_RE = re.compile(
    r"trading window|newspaper publication|loss of share cert|duplicate share|certificate under|reg(ulation)?\.? ?74|"
    r"74\(5\)|statement of investor complaints|shareholders meeting|book closure|compliance certificate|"
    r"change in registered office|record date|postal ballot|e-voting|updates?-xbrl|annual report|"
    r"secretarial compliance|reconciliation of share capital|closure of trading", re.I)
_SUBJECT_WEIGHT = [
    (re.compile(r"financial result|outcome of board meeting", re.I), 30),
    (re.compile(r"acquisition|amalgamation|merger|scheme of arrangement|demerger|takeover|open offer", re.I), 28),
    (re.compile(r"order|contract|bagging|award", re.I), 24),
    (re.compile(r"credit rating", re.I), 20),
    (re.compile(r"fund raising|qip|preferential|rights issue|allotment of securities|buyback|dividend|bonus|split", re.I), 18),
    (re.compile(r"resignation|appointment|change in (director|management|kmp)|cessation", re.I), 16),
    (re.compile(r"investor presentation|analysts?/institutional|analyst.*meet|con\.? ?call|earnings call|press release", re.I), 14),
    (re.compile(r"clarification|action\(s\) taken|order passed|litigation|regulatory|penalt|search|raid|fraud|default", re.I), 18),
]
_SUBJ_RE = re.compile(r"\|\s*SUBJECT:\s*(.+)$", re.I)
_MEET_RE = re.compile(r"\|\s*Meeting Date:\s*(\d{1,2}-[A-Za-z]{3}-\d{4})", re.I)
_PURPOSE_RE = re.compile(r"PURPOSE:\s*([^|]+)", re.I)
_RECORD_RE = re.compile(r"RECORD DATE:\s*(\d{1,2}-[A-Za-z]{3}-\d{4})", re.I)
_EXDATE_RE = re.compile(r"Ex-Date:\s*(\d{1,2}-[A-Za-z]{3}-\d{4})", re.I)


def _nse_date(s):
    try:
        return datetime.strptime(s, "%d-%b-%Y").date()
    except (TypeError, ValueError):
        return None


def _clip(t: str, n: int) -> str:
    t = " ".join((t or "").split())
    return t if len(t) <= n else t[: n - 1].rsplit(" ", 1)[0] + "…"


def _tidy(t: str) -> str:
    t = re.sub(r"^(the|a|an)\s+", "", t.strip(), flags=re.I)
    return t[:1].upper() + t[1:] if t else t


def _pick_filings(items: list, now_utc: datetime) -> dict:
    today = now_utc.astimezone(IST).date()
    ann, cal, acts, seen = [], [], [], set()
    for it in items:
        ft, company = it.get("filing_type"), (it.get("company") or "").strip()
        text = it.get("text") or ""
        if not company:
            continue
        big = bool(ANALYST.companies(company, "india"))
        if ft in ("announcement", "results"):
            age = _age_h(it.get("published"), now_utc)
            if age is not None and age > 30:
                continue
            m = _SUBJ_RE.search(text)
            subject = (m.group(1) if m else ("Financial Results" if ft == "results" else "")).strip()
            detail = _SUBJ_RE.sub("", text).strip()
            if ft == "results":
                detail = detail.replace("|", " · ")
            if _ROUTINE_RE.search(subject) or _ROUTINE_RE.search(detail[:120]):
                continue
            w = max([wt for rx, wt in _SUBJECT_WEIGHT if rx.search(subject) or rx.search(detail[:160])] or [0])
            if w == 0 and not big:
                continue
            key = (company.lower(), subject.lower())
            if key in seen:
                continue
            seen.add(key)
            score = w + (40 if big else 0) + 10 * 0.5 ** ((age or 0) / 12)
            ann.append((score, {"company": company, "subject": subject or "Announcement",
                                "detail": _tidy(_clip(re.sub(rf"^{re.escape(company)}\s+has informed the Exchange (about|regarding)\s*", "", detail, flags=re.I), 220)),
                                "url": it["url"], "published": _ist(it.get("published")), "largecap": big}))
        elif ft == "board_meeting":
            m = _MEET_RE.search(text)
            d = _nse_date(m.group(1)) if m else None
            if not d or not (today <= d <= today + timedelta(days=7)):
                continue
            purpose = _clip(_MEET_RE.sub("", text).strip(" |"), 140)
            purpose = re.sub(rf"^{re.escape(company)}\s+has informed the Exchange about Board Meeting to be held on \S+ to consider\s*", "", purpose, flags=re.I)
            key = (company.lower(), d)
            if key in seen:
                continue
            seen.add(key)
            results = bool(re.search(r"financial results?|results", purpose, re.I))
            if not (big or results):
                continue  # small-company fund-raising / rescheduling notices are noise for an analyst
            score = (40 if big else 0) + (20 if results else 0) - (d - today).days
            cal.append((score, {"company": company, "date": d.isoformat(), "purpose": purpose or "Board meeting",
                                "results": results, "url": it["url"], "largecap": big}))
        elif ft == "corporate_action":
            pm, rd, ex = _PURPOSE_RE.search(text), _RECORD_RE.search(text), _EXDATE_RE.search(it.get("raw_title") or "")
            d = _nse_date(ex.group(1)) if ex else (_nse_date(rd.group(1)) if rd else None)
            if not d or not (today <= d <= today + timedelta(days=7)) or not big:
                continue
            acts.append(((40 if big else 0) - (d - today).days,
                         {"company": company, "ex_date": d.isoformat(),
                          "purpose": _clip(pm.group(1).strip() if pm else text, 80), "largecap": big}))
    top = lambda lst, n: [x for _, x in sorted(lst, key=lambda t: -t[0])[:n]]  # noqa: E731
    cal_sorted = sorted(top(cal, 14), key=lambda x: (x["date"], not x["largecap"]))
    return {"announcements": top(ann, 12), "calendar": cal_sorted, "actions": top(acts, 8)}


# ---------------------------------------------------------------- Corporate India desk
# The day's most important company stories, one per company, large caps first: results, deals,
# broker calls, orders, management and regulatory news about listed companies.
_COMPANY_SIGNALS = {"Earnings", "Deals & capital", "Orders & capex", "Broker call", "Stock move", "Credit"}
_MARKETWIDE = {"Market-wide move", "Macro data", "Rates & central banks", "Flows & positioning"}
_IPO_NOISE_RE = re.compile(r"\b(ipo|drhp|draft papers|gmp|subscribed|sme|listing)\b", re.I)
_MARKET_TITLE_RE = re.compile(r"\b(nifty|sensex|stock market|share market|markets?|dalal street|stocks to|midcaps?|smallcaps?)\b", re.I)


def _company_desk(stories: list, n: int = 10) -> list:
    picks, seen = [], set()
    ranked = sorted(stories, key=lambda s: -s["score"])
    for pass_ in (1, 2):
        for st in ranked:
            if len(picks) >= n:
                break
            if st["id"] in {p["id"] for p in picks}:
                continue
            hits = set(st.get("_hits") or [])
            cos = ANALYST.companies(st["title"], "india")  # the company must be the headline's subject
            if not cos and _MARKET_TITLE_RE.search(st["title"]):
                continue  # market wraps that merely mention a stock in the summary
            company_story = (st.get("subsection") == "corporate" or hits & _COMPANY_SIGNALS) and not (
                hits & _MARKETWIDE and not hits & _COMPANY_SIGNALS)
            if not company_story:
                continue
            if pass_ == 1 and not cos:
                continue  # first pass: named large/mid caps only
            if not cos and (_IPO_NOISE_RE.search(st["title"]) or not hits & (_COMPANY_SIGNALS - {"Stock move"})):
                continue  # second pass: only substantive company news (results, deals, orders, calls)
            key = (cos[0] if cos else st["title"][:40]).lower()
            if key in seen:
                continue
            seen.add(key)
            picks.append({"id": st["id"], "company": cos[0] if cos else None})
    return picks

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

    # 0) split by kind: only news is ranked/clustered; videos & social stay separate and never
    #    count as confirmation of a story (accuracy). Unknown Google-News publishers are dropped.
    try:
        from feeds import SOURCE_NAME_MAP
        trusted = set(SOURCE_NAME_MAP.values())
    except Exception:  # noqa: BLE001
        trusted = None
    video_items = [it for it in raw_items if it.get("kind") == "video"]
    social_items = [it for it in raw_items if it.get("kind") == "social"]
    filing_items = [it for it in raw_items if it.get("kind") == "filing"]
    news_items, untrusted = [], 0
    for it in raw_items:
        if it.get("kind", "news") != "news":
            continue
        if trusted is not None and it.get("aggregator") and it.get("source_id") not in trusted:
            untrusted += 1
            continue
        news_items.append(it)
    if untrusted:
        print(f"[process] dropped {untrusted} aggregator items from unrecognised publishers", flush=True)
    raw_items = news_items
    video_res = [_wb(k) for sec in sections_cfg for sub in sec.get("subsections", [])
                 if sub["id"] in ("markets", "economy", "corporate") for k in sub.get("keywords", [])]
    video_res += high_res

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
                extra = _STORY_EXTRA.pop(id(_best_of(cluster)), {})
                story["companies"] = extra.get("companies", [])
                story["_hits"] = extra.get("hits", [])
                stories.append(story)
                parts = _DEBUG_PARTS.pop(id(_best_of(cluster)), {})
                debug_rows.setdefault(sid, []).append(
                    dict(parts, score=story["score"], sub=story["subsection"], src=story["source"],
                         title=story["title"][:90]))

        limit = limits.get(sid, len(stories))
        selected = _select_section_stories(stories, limit, per_source_share,
                                           sub_share=(limits.get("sub_share") or {}).get(sid))
        ordered = _build_top12_order(selected)
        for i, s in enumerate(ordered, start=1):
            s["rank"] = i
            s["is_lead"] = i == 1
            s["verified_by"] = 1 + len(s.get("coverage") or [])  # distinct outlets carrying it

        out_sections.append({
            "id": sid, "number": section_cfg["number"], "title": section_cfg["title"],
            "kicker": section_cfg["kicker"],
            "subsections": [{"id": sub["id"], "label": sub["label"]}
                             for sub in section_cfg["subsections"]],
            "stories": ordered,
            "videos": _pick_videos(video_items, sid,
                                   video_res if sid != "tech" else keyword_patterns[sid].get("ai", []),
                                   now_utc),
            "social": _pick_social(social_items, sid, low_value_patterns, now_utc),
        })
        if sid == "india":
            out_sections[-1]["company_desk"] = _company_desk(ordered)
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
    for section in out_sections:
        for st in section["stories"]:
            st.pop("_hits", None)
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
