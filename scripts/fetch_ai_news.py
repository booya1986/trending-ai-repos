#!/usr/bin/env python3
"""Collect the past week's generative-AI news from a curated source set.

Emits JSON on stdout with the shape summarize_news.py expects:

    {"week", "since", "generated_for", "count", "warnings", "items": [...]}

Design notes:
  * Four kinds of source, deliberately: editorial feeds and lab blogs say
    what shipped, Hacker News and Reddit say what landed, and X says what the
    field is arguing about today. Every feed here was checked to be alive;
    dead ones (VentureBeat's AI category, Ben's Bites) are left out rather
    than kept as decoration that silently returns nothing.
  * Reddit is read through the public .json endpoints, no key and no OAuth.
    The subreddit list is gen-AI only on purpose: r/singularity and
    r/MachineLearning were excluded as futurism and academia rather than the
    applied gen-AI beat this digest covers.
  * X/Twitter needs a scraper to reach and is read via FIRECRAWL_API_KEY.
    Without the key that source stays dormant and the digest is unaffected.
  * Because four sources overlap heavily (a TechCrunch story is also a Reddit
    link post and an HN submission), dedup runs on a canonicalised URL before
    it falls back to title similarity: see canonical_url and dedupe.
  * Nothing here may be fatal. A source that 403s, changes markup, or hangs
    records a warning and the run continues on the others.
"""
import datetime
import html as _html
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from email.utils import parsedate_to_datetime

NEWS_WINDOW_DAYS = 7
MAX_ITEMS = 80          # candidates handed to the summarizer
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
    # Import AI removed: importai.substack.com 403s from GitHub Actions runner
    # IPs, so it fetched fine locally and never in CI, warning every week.
    # Substack custom domains (Latent Space, Interconnects) are unaffected.
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

# Applied gen-AI subreddits. Deliberately no r/singularity (futurism) and no
# r/MachineLearning (academic ML): both drown the pool in material that is not
# this digest's beat.
SUBREDDITS = (
    "OpenAI", "ClaudeAI", "GeminiAI", "generativeAI", "LLMDevs",
    "AI_Agents", "StableDiffusion", "LocalLLaMA",
)
MAX_PER_SUBREDDIT = 6
REDDIT_LIMIT = 100        # entries in the single combined feed request
# Reddit's rate-limit window is long, so a 429 means waiting, not giving up.
REDDIT_RETRY_WAITS = (0, 20, 60)

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


def _first(raw, tag):
    m = re.search(rf"<{tag}[^>]*>(.*?)</{tag}>", raw, re.S)
    return _html.unescape(m.group(1)).strip() if m else ""


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
    rank = item.get("rank_hint")
    if rank is not None:
        # Reddit carries no score through its feed, but the feed is ordered
        # top-of-week, so position is the community signal. Deliberately worth
        # less than HN traction: a forum thread is weaker evidence than an
        # article the same crowd upvoted.
        score += max(0.0, 3.0 - 0.5 * rank)
    return score


def is_on_beat(item):
    """Keep gen-AI product/company news, drop the rest of the tech cycle."""
    text = _item_text(item)
    if any(a in text for a in NEWS_ANTI):
        return False
    if any(s in text for s in STRONG_SIGNALS):
        return True
    return sum(1 for s in SIGNALS if s in text) >= 2


# Query keys that identify a campaign, not a document. Two links that differ
# only by these are the same article.
_TRACKING = re.compile(
    r"^(utm_|ref$|ref_|refsrc$|src$|source$|fbclid$|gclid$|igshid$"
    r"|mc_cid$|mc_eid$|at_medium$|at_campaign$|__twitter)", re.I)


def canonical_url(url):
    """Reduce a URL to what identifies the document.

    Four sources reach the same article by four different links: the RSS copy
    carries utm parameters, Reddit links the AMP page, HN links the bare one.
    Dropping the scheme, the www/m/amp host prefix, the tracking query keys
    and the trailing slash makes those collide on a plain string compare,
    which is a far more reliable dedup signal than title similarity.
    """
    if not url:
        return ""
    try:
        parts = urllib.parse.urlsplit(url.strip())
    except ValueError:
        return url.strip().lower()
    host = (parts.hostname or "").lower()
    for prefix in ("www.", "m.", "amp."):
        if host.startswith(prefix):
            host = host[len(prefix):]
            break
    path = re.sub(r"/amp(\.html)?/?$", "", parts.path)
    path = path.rstrip("/")
    query = urllib.parse.urlencode([
        (k, v) for k, v in urllib.parse.parse_qsl(parts.query, keep_blank_values=True)
        if not _TRACKING.match(k)
    ])
    # No scheme, so an http and an https copy of one article are one item.
    return urllib.parse.urlunsplit(("", host, path, query, ""))


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

    Canonical-URL match first, then titles sharing enough distinctive words.
    The higher-scoring copy survives, so the version that reached HN or Reddit,
    or came from the primary source, is the one that goes forward.
    """
    kept = []
    seen_urls = set()
    for item in sorted(items, key=lambda i: i.get("_score", 0), reverse=True):
        url = canonical_url(item.get("url"))
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


# --- Firecrawl, the way past a block ----------------------------------------
#
# Reddit 403s its .json endpoints outright and 429s its RSS after the first
# request or two, and X refuses unauthenticated reads altogether. Firecrawl
# fetches from its own infrastructure, so both become reachable. It is optional
# throughout: without FIRECRAWL_API_KEY, Reddit falls back to whatever the
# direct RSS path manages and X contributes nothing.

FIRECRAWL_ENDPOINT = "https://api.firecrawl.dev/v2/scrape"


def firecrawl_fetch(url, api_key, fmt="rawHtml", timeout=90):
    """Scrape one URL through Firecrawl, returning the requested format."""
    payload = json.dumps({
        "url": url,
        "formats": [fmt],
        "onlyMainContent": False,
    }).encode()
    req = urllib.request.Request(
        FIRECRAWL_ENDPOINT,
        data=payload,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        body = json.loads(resp.read().decode("utf-8", "ignore"))
    data = body.get("data") or body
    # v2 returns {"data": {"rawHtml": ...}}; be forgiving about the shape so a
    # minor API change degrades to a warning instead of an exception.
    return data.get(fmt) or data.get("rawHtml") or data.get("html") or data.get("markdown") or ""


# Reddit is read through ONE combined multireddit feed, not one request per
# subreddit. Three things forced that shape, all measured 2026-08-22:
#   * every .json endpoint answers 403 Blocked, whatever the User-Agent
#   * the per-subreddit .rss feed works but 429s after the first request or
#     two, and worse from CI runner IPs
#   * Firecrawl cannot rescue it: it refuses reddit.com outright, with
#     "we do not support this site"
# Reddit does support /r/a+b+c/, so all eight subreddits arrive in a single
# request that nothing rate limits. Each entry names its own subreddit in
# <category term="...">, so per-sub caps still work.
_REDDIT_OUTBOUND = re.compile(r'<a href="([^"]+)">\s*\[link\]', re.I)
_REDDIT_INTERNAL = ("reddit.com", "redd.it", "i.redd.it", "v.redd.it", "preview.redd.it")


def _reddit_outbound(content_html, permalink):
    """The article a post points at, or the discussion if it points nowhere.

    Preferring the destination is what lets a Reddit link post collide with the
    press and HN copies of the same article in dedupe(). Self posts and Reddit
    hosted media have no destination, so those keep the permalink, and the
    caller drops them.
    """
    m = _REDDIT_OUTBOUND.search(_html.unescape(content_html or ""))
    if not m:
        return permalink
    dest = m.group(1)
    try:
        host = (urllib.parse.urlsplit(dest).hostname or "").lower()
    except ValueError:
        return permalink
    if any(host == d or host.endswith("." + d) for d in _REDDIT_INTERNAL):
        return permalink
    return dest


def fetch_reddit(subreddits=SUBREDDITS, per_sub=MAX_PER_SUBREDDIT, warnings=None,
                 now=None, retry_waits=REDDIT_RETRY_WAITS):
    """This week's top LINK posts from the applied gen-AI subreddits.

    Link posts only. A subreddit's top-of-week rewards jokes, not significance:
    the five highest scoring posts in r/OpenAI the week this was written were
    memes, "POV: you're born as an AI" among them, and every one passed the
    gen-AI keyword filter. Memes are self or image posts, so requiring an
    outbound article drops them, and it makes a post collide with the press
    copy of the same story in dedupe() instead of duplicating it.

    The feed is ordered top-of-week, so a post's position is the community
    signal the (blocked) JSON score would have carried. It travels as
    rank_hint rather than being written into `points`, which would have put a
    made-up number beside Hacker News's real one.
    """
    url = (f"https://www.reddit.com/r/{'+'.join(subreddits)}/top/.rss"
           f"?t=week&limit={REDDIT_LIMIT}")
    # This is one request per week: waiting a couple of minutes for it is
    # cheap, and losing the whole source to a transient 429 is not.
    xml_text = None
    last = None
    for wait in retry_waits:
        if wait:
            time.sleep(wait)
        try:
            xml_text = _get(url)
            break
        except Exception as e:
            last = e
    if xml_text is None:
        if warnings is not None:
            warnings.append(f"reddit failed after 3 attempts: {type(last).__name__}: {last}")
        return []

    entries = xml_text.split("<entry>")[1:]
    if not entries and warnings is not None:
        warnings.append("reddit parsed 0 entries (format change?)")

    out = []
    per_sub_count = {}
    for rank, raw in enumerate(entries):
        title = _clean(_first(raw, "title"))
        if not title:
            continue
        m_sub = re.search(r'<category term="([^"]+)"', raw)
        sub = m_sub.group(1) if m_sub else "reddit"
        if sub == "multi":          # the feed's own self-description
            continue
        if per_sub_count.get(sub, 0) >= per_sub:
            continue
        m_link = re.search(r'<link href="([^"]+)"', raw)
        permalink = m_link.group(1) if m_link else f"https://www.reddit.com/r/{sub}/"
        content = re.search(r"<content[^>]*>(.*?)</content>", raw, re.S)
        link = _reddit_outbound(content.group(1) if content else "", permalink)
        if link == permalink:
            continue                # self or image post, not an article
        published = _first(raw, "published") or _first(raw, "updated")
        out.append({
            "title": title,
            "url": link,
            "source": f"r/{sub}",
            "tier": 2,
            "published": published or "",
            "published_dt": parse_date(published),
            "summary": "",
            "points": 0,
            "rank_hint": rank,
        })
        per_sub_count[sub] = per_sub_count.get(sub, 0) + 1
    return out


# --- X/Twitter, via Firecrawl -----------------------------------------------
#
# X refuses unauthenticated reads and the API tier that could read timelines is
# ~$200/month, so this goes through Firecrawl like the blocked Reddit feeds.
# Two source shapes: the accounts that announce things, and a live search for
# high-engagement AI posts.
#
# What comes back is a rendered timeline as markdown, not a feed, so items have
# no reliable per-post timestamp and no link to anything but the account page.
# They are therefore treated as SIGNAL, not as stories to link to: a post here
# tells the summarizer what the field is talking about this week, and dedupe()
# collapses it against the press coverage of the same event.

X_SOURCES = [
    "https://x.com/OpenAI",
    "https://x.com/AnthropicAI",
    "https://x.com/GoogleDeepMind",
]
X_MIN_CHARS = 80
X_TITLE_CHARS = 220

# Firecrawl renders an X profile as structured markdown, not as a wall of
# timeline text: a "## Latest Posts" section, then one "### N. Post" block per
# post carrying "Posted: <ISO>", "URL: [..](..)", the body as a blockquote, and
# "Likes: n | Retweets: n". Verified against a live scrape 2026-08-22. That
# gives a real per-post URL, a real timestamp, and a real engagement number, so
# X items rank on the same axes as everything else and dedupe on URL like
# everything else.
_X_POST = re.compile(r"^###\s+\d+\.\s*Post\s*$", re.M)
_X_POSTED = re.compile(r"Posted:\s*([^\s]+)")
_X_URL = re.compile(r"URL:\s*\[[^\]]*\]\(([^)]+)\)")
_X_ENGAGEMENT = re.compile(r"Likes:\s*([\d,]+)\s*\|\s*Retweets:\s*([\d,]+)")


def _x_unescape(text):
    """Firecrawl backslash-escapes markdown punctuation, dates included."""
    return re.sub(r"\\([^A-Za-z0-9])", r"\1", text or "")


def _x_posts(markdown):
    """Structured posts out of a scraped X profile."""
    body = markdown.split("## Latest Posts", 1)
    if len(body) < 2:
        return []
    posts = []
    for chunk in _X_POST.split(body[1])[1:]:
        m_url = _X_URL.search(chunk)
        if not m_url:
            continue
        quote = " ".join(
            _x_unescape(line.lstrip("> ").strip())
            for line in chunk.splitlines()
            if line.lstrip().startswith(">")
        ).strip()
        quote = re.sub(r"\s+", " ", quote)
        if len(quote) < X_MIN_CHARS:
            continue
        m_posted = _X_POSTED.search(chunk)
        m_eng = _X_ENGAGEMENT.search(chunk)
        likes = retweets = 0
        if m_eng:
            likes = int(m_eng.group(1).replace(",", ""))
            retweets = int(m_eng.group(2).replace(",", ""))
        posts.append({
            "text": quote,
            "url": _x_unescape(m_url.group(1)),
            "posted": _x_unescape(m_posted.group(1)) if m_posted else "",
            "points": likes + retweets,
        })
    return posts


def fetch_x_via_firecrawl(api_key, urls=X_SOURCES, limit_per_url=5,
                          warnings=None, now=None):
    now = now or datetime.datetime.now(datetime.timezone.utc)
    items = []
    for target in urls:
        handle = target.rstrip("/").split("/")[-1]
        try:
            markdown = firecrawl_fetch(target, api_key, fmt="markdown")
        except Exception as e:
            if warnings is not None:
                warnings.append(f"x @{handle} failed: {type(e).__name__}: {e}")
            continue
        posts = _x_posts(markdown)
        if not posts and warnings is not None:
            warnings.append(f"x @{handle} parsed 0 posts (page shape changed?)")
        for post in posts[:limit_per_url]:
            text = post["text"]
            items.append({
                # An announcement post has no headline, so the opening of the
                # post is the title and the whole post is the summary.
                "title": text[:X_TITLE_CHARS],
                "url": post["url"],
                "source": f"X @{handle}",
                "tier": 3,      # the lab announcing its own thing
                "published": post["posted"],
                "published_dt": parse_date(post["posted"]),
                "summary": text,
                "points": post["points"],
            })
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

    try:
        reddit_items = fetch_reddit(warnings=warnings, now=now)
        if not reddit_items:
            warnings.append("reddit returned 0 link posts this week")
        collected.extend(reddit_items)
    except Exception as e:
        warnings.append(f"reddit failed: {type(e).__name__}: {e}")

    if firecrawl_key:
        try:
            collected.extend(fetch_x_via_firecrawl(
                firecrawl_key, warnings=warnings, now=now))
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
