#!/usr/bin/env python3
"""Pole Position — feeds.py (Agent A)

Fetches every enabled feed in ``feeds.json`` in parallel, parses RSS 2.0 / RDF (RSS 1.0) /
Atom with ``xml.etree`` (namespace-aware, with a sanitizing retry and a regex last resort for
broken XML), normalizes dates to aware UTC datetimes and cleans up text/images. Never raises:
every feed failure becomes a ``health`` entry instead of an exception.

Standard library only. Run standalone with ``python feeds.py`` for a quick health report
(fails gracefully with no internet).
"""
from __future__ import annotations

import gzip
import html
import json
import os
import re
import socket
import threading
import time
import urllib.error
import urllib.request
import zlib
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from xml.etree import ElementTree as ET

# ---------------------------------------------------------------- constants

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
)
HEADERS = {
    "User-Agent": USER_AGENT,
    "Accept": "application/rss+xml, application/atom+xml, application/xml;q=0.9, */*;q=0.8",
    "Accept-Language": "en-IN,en;q=0.9",
    "Accept-Encoding": "gzip, deflate",
}
RETRY_DELAY_S = 1.5
IST = timezone(timedelta(hours=5, minutes=30))

# XML namespace URIs we care about (local-name matching is used for the plain RSS/RDF
# fields since RDF/RSS1.0 puts everything, including <title>/<link>, in a default namespace).
NS_ATOM = "http://www.w3.org/2005/Atom"
NS_CONTENT = "http://purl.org/rss/1.0/modules/content/"
NS_DC = "http://purl.org/dc/elements/1.1/"
NS_MEDIA = "http://search.yahoo.com/mrss/"

# Canonical source_id -> display name for every publisher we know about.
DISPLAY = {
    "et": "Economic Times", "mint": "Mint", "bs": "Business Standard",
    "moneycontrol": "Moneycontrol", "hbl": "Hindu BusinessLine", "fe": "Financial Express",
    "ndtvprofit": "NDTV Profit", "cnbctv18": "CNBC-TV18", "bloomberg": "Bloomberg",
    "reuters": "Reuters", "wsj": "The Wall Street Journal", "ft": "Financial Times",
    "cnbc": "CNBC", "marketwatch": "MarketWatch", "economist": "The Economist",
    "nikkei": "Nikkei Asia", "bbc": "BBC", "yahoo": "Yahoo Finance",
    "techcrunch": "TechCrunch", "verge": "The Verge", "ars": "Ars Technica",
    "mittr": "MIT Technology Review", "venturebeat": "VentureBeat", "wired": "Wired",
    "hn": "Hacker News", "openai": "OpenAI", "google": "Google", "inc42": "Inc42",
}
# lowercase publisher-name variants (as seen in a Google News <source> element) -> source_id
SOURCE_NAME_MAP = {
    "reuters": "reuters",
    "bloomberg": "bloomberg", "bloomberg.com": "bloomberg", "bloomberg l.p.": "bloomberg",
    "the economic times": "et", "economic times": "et", "et markets": "et",
    "livemint": "mint", "mint": "mint",
    "business standard": "bs",
    "moneycontrol": "moneycontrol", "moneycontrol.com": "moneycontrol",
    "the hindu businessline": "hbl", "hindu businessline": "hbl", "businessline": "hbl",
    "financial express": "fe", "the financial express": "fe", "financialexpress.com": "fe",
    "ndtv profit": "ndtvprofit", "ndtv": "ndtvprofit",
    "cnbc-tv18": "cnbctv18", "cnbctv18": "cnbctv18", "cnbc tv18": "cnbctv18",
    "the wall street journal": "wsj", "wsj": "wsj", "wall street journal": "wsj", "wsj.com": "wsj",
    "financial times": "ft", "ft.com": "ft",
    "cnbc": "cnbc",
    "marketwatch": "marketwatch",
    "the economist": "economist", "economist": "economist",
    "nikkei asia": "nikkei", "nikkei": "nikkei",
    "bbc": "bbc", "bbc news": "bbc",
    "yahoo finance": "yahoo", "yahoo": "yahoo",
    "techcrunch": "techcrunch",
    "the verge": "verge", "verge": "verge",
    "ars technica": "ars",
    "mit technology review": "mittr",
    "venturebeat": "venturebeat",
    "wired": "wired",
    "hacker news": "hn",
    "openai": "openai",
    "google": "google",
    "inc42": "inc42",
}

_BOILERPLATE_RES = [
    re.compile(r"the post .*? appeared first on .*", re.I | re.S),
    re.compile(r"continue reading(\s+the\s+(full|main)\s+(story|article))?\s*\.*\s*$", re.I),
    re.compile(r"\bread more\s*\.*\s*$", re.I),
]
_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")
_TRAILING_SOURCE_RE = re.compile(r"\s+-\s+[^-]{2,40}$")  # " - Publisher" suffix (Google News)
_CONTROL_CHARS_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")
_BARE_AMP_RE = re.compile(r"&(?!#\d+;|#x[0-9a-fA-F]+;|[a-zA-Z][a-zA-Z0-9]*;)")
_CDATA_RE = re.compile(r"<!\[CDATA\[(.*?)\]\]>", re.S)
_ITEM_BLOCK_RE = re.compile(r"<item\b.*?</item>", re.I | re.S)
_ENTRY_BLOCK_RE = re.compile(r"<entry\b.*?</entry>", re.I | re.S)
_IMG_SRC_RE = re.compile(r'<img[^>]+src=["\']([^"\']+)["\']', re.I)


# ---------------------------------------------------------------- HTTP

def _decompress(raw: bytes, content_encoding: str) -> bytes:
    ce = (content_encoding or "").lower()
    try:
        if "gzip" in ce:
            return gzip.decompress(raw)
        if "deflate" in ce:
            try:
                return zlib.decompress(raw)
            except zlib.error:
                return zlib.decompress(raw, -zlib.MAX_WBITS)  # raw deflate, no zlib header
    except Exception:
        pass
    if raw[:2] == b"\x1f\x8b":  # some servers mislabel; sniff the gzip magic anyway
        try:
            return gzip.decompress(raw)
        except Exception:
            pass
    return raw


REDDIT_UA = "pole-position-news/1.0 (personal morning-paper RSS reader; +https://github.com)"
REDDIT_MIN_INTERVAL_S = 8.0          # Reddit rate-limits bursts (HTTP 429): one request at a time
_REDDIT_LOCK = threading.Lock()
_REDDIT_LAST = [0.0]


def _throttle(url: str) -> None:
    if "reddit.com" not in url:
        return
    with _REDDIT_LOCK:                # serialises Reddit calls while other feeds run in parallel
        wait = _REDDIT_LAST[0] + REDDIT_MIN_INTERVAL_S - time.time()
        if wait > 0:
            time.sleep(wait)
        _REDDIT_LAST[0] = time.time()


def _http_get(url: str, timeout: int) -> tuple[bytes, str]:
    _throttle(url)
    headers = dict(HEADERS)
    if "reddit.com" in url:
        headers["User-Agent"] = REDDIT_UA  # Reddit blocks spoofed browser UAs; be honest
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read()
        raw = _decompress(raw, resp.headers.get("Content-Encoding", ""))
        return raw, resp.headers.get("Content-Type", "")


def _fetch_with_retry(url: str, timeout: int) -> tuple[bytes, str]:
    """One retry after RETRY_DELAY_S on timeout / 5xx / 429. Anything else raises immediately."""
    try:
        return _http_get(url, timeout)
    except urllib.error.HTTPError as e:
        if e.code == 429 or 500 <= e.code < 600:
            time.sleep(12 if (e.code == 429 and "reddit.com" in url) else RETRY_DELAY_S)
            return _http_get(url, timeout)
        raise
    except (urllib.error.URLError, TimeoutError, socket.timeout, OSError):
        time.sleep(RETRY_DELAY_S)
        return _http_get(url, timeout)


def _decode(raw: bytes, content_type: str) -> str:
    enc = None
    m = re.search(r"charset=([\w-]+)", content_type or "", re.I)
    if m:
        enc = m.group(1)
    if not enc:
        m2 = re.search(rb'encoding=["\']([\w-]+)["\']', raw[:200])
        if m2:
            enc = m2.group(1).decode("ascii", "ignore")
    enc = enc or "utf-8"
    try:
        text = raw.decode(enc, errors="replace")
    except (LookupError, UnicodeDecodeError):
        text = raw.decode("utf-8", errors="replace")
    return text.lstrip("﻿")


# ---------------------------------------------------------------- XML parsing helpers

def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1] if "}" in tag else tag


def _ns_of(tag: str) -> str:
    return tag.split("}")[0][1:] if tag.startswith("{") else ""


def _sanitize_xml(text: str) -> str:
    idx = text.find("<")
    if idx > 0:
        text = text[idx:]  # drop HTML junk / stray text before the declaration
    text = _CONTROL_CHARS_RE.sub("", text)
    text = _BARE_AMP_RE.sub("&amp;", text)  # stray '&' not part of a valid entity
    return text


def _group_children(elem) -> dict:
    groups: dict = {}
    for c in list(elem):
        groups.setdefault(_local(c.tag), []).append(c)
    return groups


def _text_of(el) -> str | None:
    if el is None:
        return None
    if el.text and el.text.strip():
        return el.text
    txt = "".join(el.itertext())
    return txt or None


def _collect_entries(root) -> tuple[list, bool]:
    entries = [e for e in root.iter() if _local(e.tag) == "item"]
    if entries:
        return entries, False
    entries = [e for e in root.iter() if _local(e.tag) == "entry"]
    return entries, True


def _fields_from_element(item, is_atom: bool) -> dict:
    """Pull out the fields we care about from one <item>/<entry>, namespace-aware where it
    matters (media:content vs atom:content share the local name 'content')."""
    g = _group_children(item)

    title = _text_of((g.get("title") or [None])[0])

    link = None
    for l in g.get("link", []):
        href = l.get("href")
        if href:
            rel = l.get("rel")
            if rel in (None, "alternate"):
                link = href
                break
            link = link or href
        elif l.text and l.text.strip():
            link = link or l.text.strip()
    if not link:
        for guid in g.get("guid", []):
            if guid.get("isPermaLink", "true").lower() != "false":
                t = _text_of(guid)
                if t:
                    link = t.strip()
                    break
    if not link:
        for id_el in g.get("id", []):
            t = _text_of(id_el)
            if t and t.startswith("http"):
                link = t.strip()
                break

    desc_raw = _text_of((g.get("description") or [None])[0]) or _text_of((g.get("summary") or [None])[0]) or ""

    content_raw = ""
    for c in g.get("encoded", []):
        if _ns_of(c.tag) == NS_CONTENT:
            content_raw = _text_of(c) or ""
            break
    if not content_raw:
        for c in g.get("content", []):
            if _ns_of(c.tag) in (NS_ATOM, ""):
                content_raw = _text_of(c) or ""
                break

    date_raw = None
    for key in ("pubDate", "published", "issued"):
        els = g.get(key, [])
        if els:
            date_raw = _text_of(els[0])
            if date_raw:
                break
    if not date_raw:
        for c in g.get("date", []):  # dc:date only (avoid clashing with unrelated tags)
            if _ns_of(c.tag) == NS_DC:
                date_raw = _text_of(c)
                break
    if not date_raw:
        els = g.get("updated", [])
        if els:
            date_raw = _text_of(els[0])

    image = None
    for key in ("content", "thumbnail"):
        for c in g.get(key, []):
            if _ns_of(c.tag) != NS_MEDIA:
                continue
            t = c.get("type") or c.get("medium") or ""
            if c.get("url") and (not t or t.startswith("image")):
                image = c.get("url")
                break
        if image:
            break
    if not image:
        for e in g.get("enclosure", []):
            if (e.get("type") or "").startswith("image") and e.get("url"):
                image = e.get("url")
                break
    if not image:
        for blob in (desc_raw, content_raw):
            m = _IMG_SRC_RE.search(blob or "")
            if m:
                image = m.group(1)
                break

    source_name = _text_of((g.get("source") or [None])[0])

    # --- video / social extras (YouTube Atom, Reddit Atom, Bluesky RSS) ---
    extras: dict = {}
    for v in g.get("videoId", []):                       # yt:videoId
        extras["video_id"] = (_text_of(v) or "").strip()
    for grp in g.get("group", []):                       # media:group
        if _ns_of(grp.tag) != NS_MEDIA:
            continue
        for el in grp.iter():
            name = _local(el.tag)
            if name == "thumbnail" and el.get("url"):
                extras.setdefault("thumbnail", el.get("url"))
            elif name == "statistics" and el.get("views"):
                try:
                    extras["views"] = int(el.get("views"))
                except ValueError:
                    pass
            elif name == "description" and not desc_raw:
                desc_raw = _text_of(el) or ""
    for a in g.get("author", []):                        # atom:author/name (Reddit, YouTube)
        nm = next((_text_of(c) for c in a if _local(c.tag) == "name"), None) or _text_of(a)
        if nm and nm.strip():
            extras["author"] = nm.strip()
            break
    for c in g.get("category", []):                      # Reddit: <category term="sub" label="r/sub"/>
        if c.get("label", "").startswith("r/"):
            extras["community"] = c.get("label")
            break

    return {
        "title": title, "link": link, "desc_raw": desc_raw, "content_raw": content_raw,
        "date_raw": date_raw, "image": image, "source_name": source_name, "extras": extras,
    }


def _regex_field(block: str, name: str) -> str | None:
    m = re.search(rf"<{re.escape(name)}\b[^>]*>(.*?)</{re.escape(name)}>", block, re.I | re.S)
    if not m:
        return None
    val = m.group(1)
    cm = _CDATA_RE.search(val)
    return (cm.group(1) if cm else val).strip()


def _regex_link(block: str) -> str | None:
    v = _regex_field(block, "link")
    if v:
        return v
    m = re.search(r'<link[^>]*\bhref=["\']([^"\']+)["\']', block, re.I)
    return m.group(1) if m else None


def _regex_extract_items(text: str) -> list[dict]:
    """Last-resort item extraction for XML that ElementTree simply refuses to parse."""
    blocks = _ITEM_BLOCK_RE.findall(text) or _ENTRY_BLOCK_RE.findall(text)
    out = []
    for b in blocks:
        img = None
        m = re.search(r'<media:(?:content|thumbnail)[^>]*\burl=["\']([^"\']+)["\']', b, re.I)
        if m:
            img = m.group(1)
        if not img:
            m = re.search(r'<enclosure[^>]*\burl=["\']([^"\']+)["\'][^>]*\btype=["\']image', b, re.I)
            if m:
                img = m.group(1)
        out.append({
            "title": _regex_field(b, "title"),
            "link": _regex_link(b),
            "desc_raw": _regex_field(b, "description") or _regex_field(b, "summary") or "",
            "content_raw": _regex_field(b, "content:encoded") or "",
            "date_raw": (_regex_field(b, "pubDate") or _regex_field(b, "published")
                         or _regex_field(b, "dc:date") or _regex_field(b, "updated")),
            "image": img,
            "source_name": _regex_field(b, "source"),
        })
    return out


def _parse_feed_text(text: str) -> list[dict]:
    """Parse -> sanitize+reparse -> regex fallback. Always returns a (possibly empty) list."""
    root = None
    sanitized = None
    try:
        root = ET.fromstring(text)
    except ET.ParseError:
        sanitized = _sanitize_xml(text)
        try:
            root = ET.fromstring(sanitized)
        except ET.ParseError:
            root = None
    if root is not None:
        entries, is_atom = _collect_entries(root)
        return [_fields_from_element(e, is_atom) for e in entries]
    # even the regex last-resort should work off control-char-free, junk-trimmed text
    return _regex_extract_items(sanitized if sanitized is not None else text)


# ---------------------------------------------------------------- dates

def _parse_date(raw: str | None, default_tz) -> datetime | None:
    if not raw or not raw.strip():
        return None
    raw = raw.strip()
    dt = None
    try:
        dt = parsedate_to_datetime(raw)
    except (TypeError, ValueError, IndexError):
        dt = None
    if dt is None:
        iso = raw.replace("Z", "+00:00")
        try:
            dt = datetime.fromisoformat(iso)
        except ValueError:
            m = re.match(
                r"(\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2})(\.\d+)?\s*(Z|[+-]\d{2}:?\d{2})?", raw
            )
            if m:
                base, _frac, tz_ = m.groups()
                suffix = ""
                if tz_:
                    suffix = "+00:00" if tz_ == "Z" else (tz_ if ":" in tz_ else f"{tz_[:3]}:{tz_[3:]}")
                try:
                    dt = datetime.fromisoformat(base + suffix)
                except ValueError:
                    dt = None
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=default_tz)
    return dt.astimezone(timezone.utc)


# ---------------------------------------------------------------- text cleanup

def clean_text(raw: str | None) -> str:
    if not raw:
        return ""
    text = _TAG_RE.sub(" ", raw)
    text = html.unescape(text)
    text = html.unescape(text)  # double-escaped entities (&amp;amp;hellip; etc.)
    text = text.replace("\xa0", " ")
    text = _WS_RE.sub(" ", text).strip()
    for pat in _BOILERPLATE_RES:
        text = pat.sub("", text).strip()
    return text


def _strip_trailing_source(title: str, aggregator: bool) -> str:
    if not aggregator or not title:
        return title
    return _TRAILING_SOURCE_RE.sub("", title).strip()


def _slugify(name: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")
    return s or "unknown"


def _resolve_source(source_name: str | None, feed: dict) -> tuple[str, str]:
    if feed.get("aggregator") and source_name:
        key = re.sub(r"\.com$", "", source_name.strip().lower())
        sid = SOURCE_NAME_MAP.get(key)
        if sid:
            return sid, DISPLAY.get(sid, source_name.strip())
        return _slugify(source_name), source_name.strip()
    return feed.get("source_id", "unknown"), feed.get("source", "Unknown")


# ---------------------------------------------------------------- item building

_REDDIT_TAIL_RE = re.compile(r"\s*submitted by\s+/?u/.*$", re.I | re.S)


def _build_item(fields: dict, feed: dict, default_tz, now_utc: datetime) -> dict | None:
    kind = feed.get("kind", "news")
    raw_title = fields.get("title")
    link = fields.get("link")
    if kind == "social" and not raw_title:
        # Bluesky posts have no title: use the post text itself
        raw_title = (fields.get("desc_raw") or fields.get("content_raw") or "")[:200]
    if not raw_title or not link:
        return None
    title = clean_text(raw_title)
    title = _strip_trailing_source(title, feed.get("aggregator", False))
    if not title:
        return None

    if feed.get("aggregator"):
        summary = ""  # Google News descriptions are just link lists
    else:
        summary = clean_text(fields.get("desc_raw") or fields.get("content_raw") or "")

    published = _parse_date(fields.get("date_raw"), default_tz)
    if published is not None and published > now_utc + timedelta(hours=1):
        published = now_utc  # feeds occasionally publish slightly into the future

    image = fields.get("image")
    if image:
        image = html.unescape(image.strip())
        if not image.lower().startswith(("http://", "https://")):
            image = None

    source_id, source = _resolve_source(fields.get("source_name"), feed)

    item = {
        "title": title,
        "url": link.strip(),
        "summary": summary,
        "published": published,
        "image": image,
        "source_id": source_id,
        "source": source,
        "feed_id": feed["id"],
        "section": feed["section"],
        "subsection": feed.get("subsection"),
        "tier": feed.get("tier", 3),
        "kind": kind,
        "aggregator": bool(feed.get("aggregator")),
    }
    if kind == "video":
        ex = fields.get("extras") or {}
        vid = ex.get("video_id") or ""
        if not vid:
            m = re.search(r"[?&]v=([\w-]{6,})", link)
            vid = m.group(1) if m else ""
        if not vid:
            return None
        item.update(video_id=vid, channel=feed.get("source"), views=ex.get("views"),
                    thumbnail=f"https://i.ytimg.com/vi/{vid}/hqdefault.jpg",
                    url=f"https://www.youtube.com/watch?v={vid}")
    elif kind == "social":
        ex = fields.get("extras") or {}
        text = _REDDIT_TAIL_RE.sub("", summary or "").strip()
        if feed.get("platform") == "reddit":
            text = text if len(text) > 20 else ""        # link posts: title carries the content
        item.update(platform=feed.get("platform"),
                    author=(ex.get("author") or feed.get("source") or "").lstrip("/"),
                    community=ex.get("community") or feed.get("community"), text=text,
                    score=None, official=False)
    return item


def _fetch_one(feed: dict, timeout: int) -> tuple[list[dict], dict]:
    t0 = time.time()
    health = {"id": feed.get("id"), "name": feed.get("source"), "section": feed.get("section"),
              "ok": False, "items": 0, "ms": 0, "error": None}
    items: list[dict] = []
    try:
        raw, content_type = _fetch_with_retry(feed["url"], timeout)
        text = _decode(raw, content_type)
        entries = _parse_feed_text(text)
        default_tz = IST if feed.get("section") == "india" else timezone.utc
        now_utc = datetime.now(timezone.utc)
        for fields in entries:
            try:
                item = _build_item(fields, feed, default_tz, now_utc)
            except Exception:  # noqa: BLE001 - one bad item must never sink the feed
                item = None
            if item:
                items.append(item)
        health["ok"] = True
        health["items"] = len(items)
    except Exception as exc:  # noqa: BLE001 - a feed must never crash the whole fetch
        health["error"] = f"{type(exc).__name__}: {exc}"[:200]
    health["ms"] = int((time.time() - t0) * 1000)
    return items, health


# ---------------------------------------------------------------- public API

def fetch_all(feeds: list[dict], timeout: int = 15, max_workers: int = 32) -> tuple[list[dict], list[dict]]:
    """Fetch every feed in parallel. Never raises; a broken feed just shows up in `health`."""
    raw_items: list[dict] = []
    health: list[dict] = []
    if not feeds:
        return raw_items, health
    workers = max(1, min(max_workers, len(feeds)))
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futures = {ex.submit(_fetch_one, feed, timeout): feed for feed in feeds}
        for fut in as_completed(futures):
            feed = futures[fut]
            try:
                items, h = fut.result()
            except Exception as exc:  # pragma: no cover - _fetch_one already catches everything
                items, h = [], {"id": feed.get("id"), "name": feed.get("source"),
                                 "section": feed.get("section"), "ok": False, "items": 0,
                                 "ms": 0, "error": f"{type(exc).__name__}: {exc}"[:200]}
            raw_items.extend(items)
            health.append(h)
    order = {f["id"]: i for i, f in enumerate(feeds)}
    health.sort(key=lambda h: order.get(h["id"], 0))
    return raw_items, health


def _main() -> None:
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "feeds.json")
    feeds = []
    try:
        with open(path, encoding="utf-8") as fh:
            cfg = json.load(fh)
        feeds = [f for f in cfg.get("feeds", []) if f.get("enabled", True)]
    except Exception as exc:  # noqa: BLE001
        print(f"could not read feeds.json: {exc}")
    items, health = fetch_all(feeds)
    ok = sum(1 for h in health if h["ok"])
    print(f"feeds.py standalone run: {ok}/{len(health)} feeds ok, {len(items)} items")
    for h in health:
        if not h["ok"]:
            print(f"  DOWN {h['id']}: {h['error']}")


if __name__ == "__main__":
    _main()
