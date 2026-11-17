#!/usr/bin/env python3
"""Pull public RSS/Atom feeds, keep GTA 6 headlines, write data/news.json.
Copy of gta-hub/server/fetch_news.py for the GitHub Actions news refresh (.github/workflows/news.yml).

Stdlib only (urllib + xml.etree). No AI, no scraping of article pages.
We store title, source, url, date and a one-sentence summary (max ~160 chars)
taken from the feed's own description. Full articles are never copied.

Run:  python3 tools/fetch_news.py             (writes data/news.json)
      python3 tools/fetch_news.py --dry-run   (prints, writes nothing)
"""
from __future__ import annotations

import html
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "..", "data", "news.json")

USER_AGENT = "StillworksNewsIndex/0.1 (unofficial fan news index; headlines + links only)"
TIMEOUT = 15
MAX_ITEMS = 60
PER_SOURCE_CAP = {"reddit": 8, "outlet": 10}
SUMMARY_MAX = 160

# Verified 17 Nov 2026 from brain-server: each returned a valid RSS/Atom feed.
# Dropped after verification: IGN (feeds.ign.com redirects to an HTML page for bots),
# Rockstar Newswire (/newswire/rss is a 404), old.reddit.com (redirect loop).
FEEDS = [
    {"name": "GameSpot", "url": "https://www.gamespot.com/feeds/news/", "kind": "outlet"},
    {"name": "Eurogamer", "url": "https://www.eurogamer.net/feed", "kind": "outlet"},
    {"name": "PC Gamer", "url": "https://www.pcgamer.com/rss/", "kind": "outlet"},
    {"name": "Polygon", "url": "https://www.polygon.com/feed/", "kind": "outlet"},
    {"name": "VGC", "url": "https://www.videogameschronicle.com/feed/", "kind": "outlet"},
    {"name": "GamesRadar+", "url": "https://www.gamesradar.com/feeds.xml", "kind": "outlet"},
    {"name": "Kotaku", "url": "https://kotaku.com/feed", "kind": "outlet"},
    {"name": "Push Square", "url": "https://www.pushsquare.com/feeds/latest", "kind": "outlet"},
    {"name": "Pure Xbox", "url": "https://www.purexbox.com/feeds/latest", "kind": "outlet"},
    {"name": "Insider Gaming", "url": "https://insider-gaming.com/feed/", "kind": "outlet"},
    {"name": "Dexerto", "url": "https://www.dexerto.com/feed/", "kind": "outlet"},
    {"name": "r/GTA6", "url": "https://www.reddit.com/r/GTA6/.rss", "kind": "reddit"},
]

GTA6_RE = re.compile(
    r"\bGTA\s?(?:6|VI)\b|\bGTA6\b|\bGrand Theft Auto\s?(?:6|VI)\b|\bLeonida\b",
    re.IGNORECASE,
)
LEAK_RE = re.compile(r"\bleak(?:s|ed|er|ers|ing)?\b|\bdata-?min(?:e|ed|ing|er)\b|\binsider\b", re.I)
RUMOUR_RE = re.compile(
    r"\brumou?r(?:s|ed)?\b|\breportedly\b|\ballegedly\b|\bspeculat\w*|\btheory\b|\btheories\b"
    r"|\bcould\b|\bmight\b|\bclaims?\b|\bunconfirmed\b|\bhint(?:s|ed)?\b|\bfans think\b|\?",
    re.I,
)
OFFICIAL_SOURCE_RE = re.compile(r"\bRockstar\b|\bTake-?Two\b|\bStrauss Zelnick\b", re.I)
OFFICIAL_VERB_RE = re.compile(
    r"\bconfirm(?:s|ed)?\b|\bannounc(?:e|es|ed)\b|\breveal(?:s|ed)?\b|\bofficial(?:ly)?\b"
    r"|\btrailer\b|\bpre-?load\b|\bpatch notes\b|\bsays\b|\bsaid\b|\breleas(?:e|es|ed)\b"
    r"|\bteam(?:s|ed)? up\b|\bpartner(?:s|ed|ship)?\b|\bshow(?:s|ed|cases?)?\b|\bavailable\b|\bout now\b",
    re.I,
)

# News *about* a leaker (court cases, arrests) is reported fact, not a leak.
LEGAL_RE = re.compile(r"\bpleads? guilty\b|\bsentenc\w*|\bcourt\b|\barrest\w*|\bconvict\w*|\bcharged\b", re.I)

ATOM = "{http://www.w3.org/2005/Atom}"
TAG_RE = re.compile(r"<[^>]+>")
WS_RE = re.compile(r"\s+")


def fetch(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/rss+xml, application/atom+xml, application/xml;q=0.9, */*;q=0.5"})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
        return resp.read(5_000_000)


def strip_html(text: str) -> str:
    text = html.unescape(TAG_RE.sub(" ", text or ""))
    text = html.unescape(text)  # some feeds double-encode
    return WS_RE.sub(" ", text).strip()


def first_sentence(text: str, limit: int = SUMMARY_MAX) -> str:
    text = strip_html(text)
    # Reddit boilerplate: "submitted by /u/x [link] [comments]"
    text = re.sub(r"submitted by\s+/u/\S+.*$", "", text, flags=re.I).strip()
    if not text:
        return ""
    text = re.sub(r"\s+([.,!?;:])", r"\1", text)
    sentence = ""
    for m in re.finditer(r"[^.!?]+[.!?]+(?:\s|$)|[^.!?]+$", text):
        sentence = (sentence + " " + m.group(0).strip()).strip()
        if len(sentence) >= 40:
            break
    if len(sentence) > limit:
        cut = sentence[: limit - 1].rsplit(" ", 1)[0].rstrip(",;:-")
        sentence = cut + "…"
    return sentence


def parse_date(value: str | None) -> str | None:
    if not value:
        return None
    value = value.strip()
    try:
        dt = parsedate_to_datetime(value)
    except (TypeError, ValueError):
        try:
            dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def text_of(el, path: str) -> str:
    found = el.find(path)
    return (found.text or "") if found is not None and found.text else ""


def parse_feed(raw: bytes) -> list[dict]:
    root = ET.fromstring(raw)
    items = []
    if root.tag == ATOM + "feed":
        for e in root.findall(ATOM + "entry"):
            link = ""
            for l in e.findall(ATOM + "link"):
                if l.get("rel", "alternate") == "alternate":
                    link = l.get("href", "")
                    break
            items.append({
                "title": text_of(e, ATOM + "title"),
                "url": link,
                "date": text_of(e, ATOM + "published") or text_of(e, ATOM + "updated"),
                "desc": text_of(e, ATOM + "summary") or text_of(e, ATOM + "content"),
            })
    else:
        channel = root.find("channel")
        if channel is None:
            raise ValueError("not an RSS or Atom feed")
        for it in channel.findall("item"):
            items.append({
                "title": text_of(it, "title"),
                "url": text_of(it, "link").strip(),
                "date": text_of(it, "pubDate") or text_of(it, "{http://purl.org/dc/elements/1.1/}date"),
                "desc": text_of(it, "description"),
            })
    return items


def label_for(kind: str, title: str, summary: str) -> str:
    """Confirmed / Rumour / Leak. When unsure: Rumour."""
    blob = f"{title} {summary}"
    if LEAK_RE.search(blob) and not LEGAL_RE.search(title):
        return "Leak"
    if kind == "reddit":
        return "Rumour"
    if RUMOUR_RE.search(title):
        return "Rumour"
    if OFFICIAL_SOURCE_RE.search(blob) and OFFICIAL_VERB_RE.search(blob):
        return "Confirmed"
    return "Rumour"


def norm_key(title: str, url: str) -> str:
    t = re.sub(r"[^a-z0-9 ]", "", title.lower())
    t = " ".join(t.split()[:10])
    return t or url


def collect(feeds=FEEDS, fetcher=fetch):
    report, items, seen = [], [], set()
    for feed in feeds:
        entry = {"name": feed["name"], "url": feed["url"], "ok": False, "kept": 0, "error": None}
        try:
            raw = fetcher(feed["url"])
            parsed = parse_feed(raw)
            entry["ok"] = True
            for p in parsed:
                title = strip_html(p["title"])
                url = p["url"].strip()
                if not title or not url.startswith(("https://", "http://")):
                    continue
                summary = first_sentence(p["desc"])
                if feed["kind"] != "reddit" and not GTA6_RE.search(f"{title} {summary}"):
                    continue
                if feed["kind"] == "reddit" and p["title"].strip().lower().startswith(("daily", "weekly")):
                    continue  # megathreads are not news
                key = norm_key(title, url)
                if key in seen or url in seen:
                    continue
                seen.update({key, url})
                items.append({
                    "title": title[:200],
                    "source": feed["name"],
                    "url": url,
                    "date": parse_date(p["date"]),
                    "summary": summary,
                    "label": label_for(feed["kind"], title, summary),
                })
                entry["kept"] += 1
        except (urllib.error.URLError, ET.ParseError, ValueError, TimeoutError, OSError) as exc:
            entry["error"] = f"{type(exc).__name__}: {str(exc)[:120]}"
        report.append(entry)
    items.sort(key=lambda i: i["date"] or "", reverse=True)
    kinds = {f["name"]: f["kind"] for f in feeds}
    per, capped = {}, []
    for it in items:
        n = per.get(it["source"], 0)
        if n >= PER_SOURCE_CAP.get(kinds.get(it["source"], "outlet"), 10):
            continue
        per[it["source"]] = n + 1
        capped.append(it)
    return capped[:MAX_ITEMS], report


def main(argv):
    dry = "--dry-run" in argv
    started = time.time()
    items, report = collect()
    for r in report:
        status = f"ok, kept {r['kept']}" if r["ok"] else f"FAILED ({r['error']})"
        print(f"  {r['name']:<15} {status}", file=sys.stderr)
    out_path = os.path.abspath(OUT)
    if not items and os.path.exists(out_path):
        print("No items from any feed; keeping the previous news.json.", file=sys.stderr)
        return 1
    doc = {
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "note": "Headlines and one-line summaries from public feeds. Each links to the original article.",
        "sources": [{"name": r["name"], "ok": r["ok"], "kept": r["kept"]} for r in report],
        "items": items,
    }
    print(f"{len(items)} items in {time.time() - started:.1f}s", file=sys.stderr)
    if dry:
        print(json.dumps(doc, indent=2, ensure_ascii=False))
        return 0
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    tmp = out_path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(doc, fh, ensure_ascii=False, indent=1)
    os.replace(tmp, out_path)
    print(f"wrote {out_path}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
