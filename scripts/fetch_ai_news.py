#!/usr/bin/env python3
"""Collect the past week's generative-AI news from a curated source set.

Emits JSON on stdout with the shape summarize_news.py expects:

    {"week", "since", "generated_for", "count", "warnings", "items": [...]}

Design notes:
  * Feeds only, no API keys. Every source here was checked to be alive and
    publishing; dead feeds (VentureBeat's AI category, Ben's Bites) are left
    out rather than kept as decoration that silently returns nothing.
  * Hacker News supplies the community signal that editorial feeds miss, via
    the free Algolia endpoint (no key, no rate limit worth worrying about).
  * X/Twitter needs a scraper to reach. That source is opt-in through
    FIRECRAWL_API_KEY and stays dormant otherwise (see fetch_x_via_firecrawl).
  * Nothing here may be fatal. A source that 403s, changes markup, or hangs
    records a warning and the run continues on the others.
"""
import datetime
import html as _html
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from email.utils import parsedate_to_datetime

NEWS_WINDOW_DAYS = 7
MAX_ITEMS = 40          # candidates handed to the summarizer
MAX_PER_SOURCE = 8      # keeps a high-volume feed from swamping the pool
TIMEOUT = 25

# (name, url, tier). Tier 1 breaks news, tier 2 analyses it, tier 3 is a
# primary source (a lab announcing its own thing). Tier only breaks ties.
FEEDS = [
    ("TechCrunch AI", "https://techcrunch.com/category/artificial-intelligence/feed/", 1),
    ("The Verge AI", "https://www.theverge.com/rss/ai-artificial-intelligence/index.xml", 1),
    ("Ars Technica AI", "https://arstechnica.com/ai/feed/", 1),
    ("Wired AI", "https://www.wired.com/feed/tag/ai/latest/rss", 1),
    ("MIT Tech Review", "https://www.technologyreview.com/topic/artificial-intelligence/feed", 1),
    ("The Decoder", "https://the-decoder.com/feed/", 1),
    ("AI News", "https://www.artificialintelligence-news.com/feed/", 1),
    ("TLDR AI", "https://tldr.tech/api/rss/ai", 2),
    ("Latent Space", "https://www.latent.space/feed", 2),
    ("Simon Willison", "https://simonwillison.net/atom/everything/", 2),
    ("Interconnects", "https://www.interconnects.ai/feed", 2),
    ("Import AI", "https://importai.substack.com/feed", 2),
    ("OpenAI", "https://openai.com/blog/rss.xml", 3),
    ("Hugging Face", "https://huggingface.co/blog/feed.xml", 3),
    ("Google DeepMind", "https://deepmind.google/blog/rss.xml", 3),
]

# What counts as this digest's beat: generative and agentic AI as products,
# services and companies. Model/lab names carry the most weight because a
# headline naming one is almost always on-topic.
STRONG_SIGNALS = (
    "openai", "anthropic", "claude", "chatgpt", "gpt-5", "gpt-6", "gemini",
    "deepmind", "llama", "mistral", "deepseek", "qwen", "grok", "xai",
    "perplexity", "midjourney", "runway", "elevenlabs", "hugging face",
    "cursor", "copilot", "sora", "veo", "nano banana", "kimi", "minimax",
    "agentic", "ai agent", "agents", "mcp", "model context protocol",
    "generative ai", "genai", "llm", "foundation model", "frontier model",
)
SIGNALS = (
    "ai", "model", "launch", "release", "announce", "funding", "raise",
    "valuation", "acquire", "startup", "product", "api", "open-source",
    "open source", "benchmark", "multimodal", "image generation",
    "video generation", "text-to-video", "text-to-image", "voice", "speech",
    "reasoning", "context window", "fine-tun", "inference", "assistant",
    "copilot", "automation", "enterprise",
)
# Off-beat for a gen-AI product digest.
NEWS_ANTI = (
    "crypto", "bitcoin", "blockchain", "nft", "sports", "election",
    "horoscope", "recipe", "dating app",
)

_UA = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/122.0 Safari/537.36"
    ),
    "Accept": "application/rss+xml, application/atom+xml, application/xml, text/xml, */*",
}


def iso_week_string(d):
    iso = d.isocalendar()
    return f"{iso[0]}-W{iso[1]:02d}"


def _get(url, timeout=TIMEOUT):
    req = urllib.request.Request(url, headers=_UA)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read().decode("utf-8", "ignore")


def _clean(text):
    """Strip tags and entities out of a feed summary."""
    if not text:
        return ""
    text = re.sub(r"<script.*?</script>", " ", text, flags=re.S | re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    text = _html.unescape(text)
    return re.sub(r"\s+", " ", text).strip()


def parse_date(raw):
    """RFC-822 (RSS) or ISO-8601 (Atom) to an aware UTC datetime, or None."""
    if not raw:
        return None
    raw = raw.strip()
    try:
        dt = parsedate_to_datetime(raw)
    except (TypeError, ValueError, IndexError):
        dt = None
    if dt is None:
        try:
            dt = datetime.datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            return None
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=datetime.timezone.utc)
    return dt.astimezone(datetime.timezone.utc)


def _text(node):
    return (node.text or "").strip() if node is not None else ""


def parse_feed(xml_text, source, tier=2):
    """Read RSS <item>s or Atom <entry>s into a common item shape."""
    try:
        root = ET.fromstring(xml_text.strip())
    except ET.ParseError:
        return []
    ns = {"atom": "http://www.w3.org/2005/Atom"}
    items = []

    for node in root.iter():
        tag = node.tag.split("}")[-1]
        if tag not in ("item", "entry"):
            continue
        title = _text(node.find("title")) or _text(node.find("atom:title", ns))
        link = _text(node.find("link")) or _text(node.find("atom:link", ns))
        if not link:
            for ln in list(node.findall("link")) + list(node.findall("atom:link", ns)):
                href = ln.get("href")
                if href and ln.get("rel", "alternate") == "alternate":
                    link = href
                    break
        raw_date = (
            _text(node.find("pubDate"))
            or _text(node.find("published"))
            or _text(node.find("updated"))
            or _text(node.find("atom:published", ns))
            or _text(node.find("atom:updated", ns))
        )
        summary = (
            _text(node.find("description"))
            or _text(node.find("summary"))
            or _text(node.find("atom:summary", ns))
            or _text(node.find("content"))
        )
        if not title or not link:
            continue
        items.append({
            "title": _clean(title),
            "url": link.strip(),
            "source": source,
            "tier": tier,
            "published": raw_date,
            "published_dt": parse_date(raw_date),
            "summary": _clean(summary)[:600],
            "points": 0,
        })
    return items


def is_recent(item, now=None, window_days=NEWS_WINDOW_DAYS):
    """Within the window. An item with no parseable date is not assumed fresh."""
    dt = item.get("published_dt")
    if dt is None:
        return False
    now = now or datetime.datetime.now(datetime.timezone.utc)
    age = (now - dt).total_seconds() / 86400.0
    return -1 <= age <= window_days


def _item_text(item):
    return f"{item.get('title', '')} {item.get('summary', '')}".lower()


def news_score(item, now=None):
    """Rank candidates: on-beat first, then fresh, then community traction."""
    text = _item_text(item)
    score = 0.0
    score += 3.0 * sum(1 for s in STRONG_SIGNALS if s in text)
    score += 1.0 * sum(1 for s in SIGNALS if s in text)
    score -= 5.0 * sum(1 for s in NEWS_ANTI if s in text)
    score += {1: 2.0, 2: 1.0, 3: 1.5}.get(item.get("tier", 2), 0.0)
    dt = item.get("published_dt")
    if dt is not None:
        now = now or datetime.datetime.now(datetime.timezone.utc)
        age = max((now - dt).total_seconds() / 86400.0, 0.0)
        score += max(0.0, 4.0 - age * 0.5)      # today beats last Monday
    points = item.get("points") or 0
    if points:
        score += min(points / 100.0, 5.0)       # HN traction, capped
    return score


def is_on_beat(item):
    """Keep gen-AI product/company news, drop the rest of the tech cycle."""
    text = _item_text(item)
    if any(a in text for a in NEWS_ANTI):
        return False
    if any(s in text for s in STRONG_SIGNALS):
        return True
    return sum(1 for s in SIGNALS if s in text) >= 2


_WORD = re.compile(r"[a-z0-9]+")
_STOP = {
    "the", "a", "an", "and", "or", "for", "to", "of", "in", "on", "with",
    "is", "are", "its", "it", "as", "at", "by", "from", "new", "now", "says",
    "this", "that", "will", "has", "have", "you", "your", "how", "why",
}


def _title_key(title):
    return frozenset(w for w in _WORD.findall(title.lower())
                     if w not in _STOP and len(w) > 2)


def dedupe(items, overlap=0.6):
    """Collapse the same story reported by several outlets.

    Exact URL match, or titles sharing enough distinctive words. The
    higher-scoring copy survives, so the version that reached HN or came from
    the primary source is the one that goes forward.
    """
    kept = []
    seen_urls = set()
    for item in sorted(items, key=lambda i: i.get("_score", 0), reverse=True):
        url = (item.get("url") or "").split("?")[0].rstrip("/")
        if url and url in seen_urls:
            continue
        key = _title_key(item.get("title", ""))
        dup = False
        for other in kept:
            other_key = other["_key"]
            if not key or not other_key:
                continue
            shared = len(key & other_key)
            if shared / max(1, min(len(key), len(other_key))) >= overlap:
                dup = True
                break
        if dup:
            continue
        item["_key"] = key
        kept.append(item)
        if url:
            seen_urls.add(url)
    return kept


def fetch_hacker_news(since_ts, min_points=80, limit=30):
    """Top AI stories the HN crowd actually upvoted this week. No key needed."""
    query = urllib.parse.quote("AI")
    url = (
        "https://hn.algolia.com/api/v1/search_by_date?tags=story"
        f"&hitsPerPage={limit}&query={query}"
        f"&numericFilters=created_at_i>{since_ts},points>{min_points}"
    )
    data = json.loads(_get(url))
    out = []
    for hit in data.get("hits", []):
        link = hit.get("url") or f"https://news.ycombinator.com/item?id={hit.get('objectID')}"
        out.append({
            "title": _clean(hit.get("title") or ""),
            "url": link,
            "source": "Hacker News",
            "tier": 2,
            "published": hit.get("created_at") or "",
            "published_dt": parse_date(hit.get("created_at") or ""),
            "summary": "",
            "points": hit.get("points") or 0,
        })
    return [i for i in out if i["title"]]


# --- optional X/Twitter source ----------------------------------------------
#
# Off by default. X blocks unauthenticated reads, so reaching it needs a
# scraping service. Set FIRECRAWL_API_KEY (repo secret) to switch this on;
# with no key the function returns nothing and the digest is unaffected.
# UNVERIFIED: written against the Firecrawl v2 scrape API but never executed,
# since no key exists yet. Expect to adjust the response path on first run.

X_SOURCES = [
    "https://x.com/search?q=%23AI%20OR%20%22AI%20agents%22%20min_faves%3A500&f=live",
    "https://x.com/OpenAI",
    "https://x.com/AnthropicAI",
]


def fetch_x_via_firecrawl(api_key, urls=X_SOURCES, limit_per_url=6):
    items = []
    for target in urls:
        payload = json.dumps({
            "url": target,
            "formats": ["markdown"],
            "onlyMainContent": True,
        }).encode()
        req = urllib.request.Request(
            "https://api.firecrawl.dev/v2/scrape",
            data=payload,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
                "User-Agent": _UA["User-Agent"],
            },
        )
        with urllib.request.urlopen(req, timeout=60) as resp:
            body = json.loads(resp.read().decode("utf-8", "ignore"))
        markdown = (body.get("data") or {}).get("markdown") or body.get("markdown") or ""
        for line in markdown.splitlines():
            line = line.strip("*_ \t")
            if len(line) < 60 or line.startswith(("#", "!", "[", "|")):
                continue
            items.append({
                "title": _clean(line)[:240],
                "url": target,
                "source": "X",
                "tier": 2,
                "published": "",
                # X posts carry no reliable timestamp through the scrape, so
                # stamp them as now: they came off a live feed this minute.
                "published_dt": datetime.datetime.now(datetime.timezone.utc),
                "summary": "",
                "points": 0,
            })
            if len(items) % limit_per_url == 0:
                break
    return items


def collect(now=None, warnings=None):
    now = now or datetime.datetime.now(datetime.timezone.utc)
    warnings = warnings if warnings is not None else []
    collected = []

    for name, url, tier in FEEDS:
        try:
            items = parse_feed(_get(url), name, tier)
        except Exception as e:
            warnings.append(f"feed failed for '{name}': {type(e).__name__}: {e}")
            continue
        if not items:
            warnings.append(f"feed '{name}' parsed 0 items (format change?)")
            continue
        fresh = [i for i in items if is_recent(i, now)]
        collected.extend(fresh[:MAX_PER_SOURCE])

    try:
        since_ts = int((now - datetime.timedelta(days=NEWS_WINDOW_DAYS)).timestamp())
        collected.extend(fetch_hacker_news(since_ts))
    except Exception as e:
        warnings.append(f"hacker news failed: {type(e).__name__}: {e}")

    firecrawl_key = os.environ.get("FIRECRAWL_API_KEY")
    if firecrawl_key:
        try:
            collected.extend(fetch_x_via_firecrawl(firecrawl_key))
        except Exception as e:
            warnings.append(f"x/firecrawl failed: {type(e).__name__}: {e}")

    on_beat = [i for i in collected if is_on_beat(i)]
    if collected and not on_beat:
        warnings.append(
            f"{len(collected)} news items fetched but none passed the gen-AI filter"
        )
    for item in on_beat:
        item["_score"] = news_score(item, now)
    ranked = sorted(dedupe(on_beat), key=lambda i: i["_score"], reverse=True)
    return ranked[:MAX_ITEMS], warnings


def main():
    now = datetime.datetime.now(datetime.timezone.utc)
    today = now.date()
    warnings = []
    items, warnings = collect(now, warnings)

    if len(items) < 5:
        warnings.append(f"only {len(items)} news items survived filtering (wanted 5+)")

    clean_items = []
    for i in items:
        clean_items.append({
            "title": i["title"],
            "url": i["url"],
            "source": i["source"],
            "published": (i["published_dt"].isoformat() if i.get("published_dt") else ""),
            "summary": i.get("summary", ""),
            "points": i.get("points", 0),
            "score": round(i.get("_score", 0), 2),
        })

    out = {
        "week": iso_week_string(today),
        "since": (today - datetime.timedelta(days=NEWS_WINDOW_DAYS)).isoformat(),
        "generated_for": today.isoformat(),
        "count": len(clean_items),
        "sources_tried": len(FEEDS) + 1,
        "warnings": warnings,
        "items": clean_items,
    }
    json.dump(out, sys.stdout, ensure_ascii=False, indent=2)


if __name__ == "__main__":
    main()
