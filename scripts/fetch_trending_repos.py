import base64
import datetime
import html as _html
import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request


def iso_week_string(d):
    iso = d.isocalendar()
    return f"{iso[0]}-W{iso[1]:02d}"


def since_date(d):
    return (d - datetime.timedelta(days=7)).isoformat()


# How far back a repo may have been created and still count as "emerging".
# Anything older has to prove itself through real weekly star velocity instead
# (see MIN_MOMENTUM).
EMERGENCE_WINDOW_DAYS = 180


def emergence_since(d):
    return (d - datetime.timedelta(days=EMERGENCE_WINDOW_DAYS)).isoformat()


AI_TOPICS = {
    "llm", "agents", "ai-agents", "rag", "machine-learning",
    "generative-ai", "llmops", "mcp", "prompt-engineering",
    "ai", "deep-learning", "transformers", "agent",
}
AI_KEYWORDS = (
    "llm", "agent", "rag", "machine learning", "generative",
    "gpt", "transformer", "neural", "prompt", "inference",
    "fine-tun", "embedding", "diffusion", "ai ",
)

# Avi's interest signals, derived from a vault review. Repos matching more of
# these rank higher (see interest_score). Grouped only for readability.
INTEREST_SIGNALS = (
    # Agent skills, workflows, multi-agent, MCP
    "agent", "agents", "agentic", "multi-agent", "orchestrat", "mcp",
    "claude", "claude-code", "skill", "skills", "workflow", "autonomous",
    "agent-memory", "human-in-the-loop", "tool-use", "harness",
    # Prompt / context engineering
    "prompt", "context engineering", "context-engineering", "system prompt",
    "instruction",
    # RAG, knowledge, memory, Obsidian, PKM
    "rag", "retrieval", "vector", "semantic search", "semantic-search",
    "knowledge base", "knowledge-base", "knowledge management", "memory",
    "persistent memory", "second brain", "obsidian", "pkm", "note", "notes",
    "embedding",
    # Gen AI for content, video, audio, TTS
    "text-to-speech", "text to speech", "tts", "speech", "voice",
    "transcription", "diarization", "video", "audio", "subtitle", "caption",
    "content", "generative",
    # Learning / education / L&D
    "edtech", "instructional", "learning", "education", "course",
    "training", "teach", "tutor", "curriculum", "learn",
    # Vibe coding / build-with-AI / learn-to-build
    "vibe", "no-code", "low-code", "code generation", "code-gen", "scaffold",
    "from scratch", "nanogpt", "build your own", "minimal", "explained",
    # Local-first / privacy / governance
    "local-first", "on-device", "privacy", "self-host", "governance",
)

# Topics/keywords that look AI-adjacent but are NOT Avi's interest. A repo whose
# signal is dominated by these is dropped (see is_relevant).
ANTI_SIGNALS = (
    "crypto", "blockchain", "web3", "trading-bot", "robot", "robotics",
    "game", "gaming", "anti-detect", "antidetect", "scraper", "scraping",
    "deepfake", "face-swap", "faceswap",
)


def _repo_text(repo):
    topics = " ".join(repo.get("topics") or [])
    return (
        (repo.get("name") or "") + " "
        + (repo.get("description") or "") + " "
        + topics
    ).lower()


def is_ai_relevant(repo):
    topics = {t.lower() for t in (repo.get("topics") or [])}
    if topics & AI_TOPICS:
        return True
    text = ((repo.get("description") or "") + " " + (repo.get("name") or "")).lower()
    return any(k in text for k in AI_KEYWORDS)


def interest_score(repo):
    """Count distinct interest signals present in the repo's text."""
    text = _repo_text(repo)
    return sum(1 for sig in INTEREST_SIGNALS if sig in text)


def anti_score(repo):
    text = _repo_text(repo)
    return sum(1 for sig in ANTI_SIGNALS if sig in text)


def is_relevant(repo):
    """Keep AI repos, but drop ones the anti-signals dominate.

    A single stray anti-signal alongside a strong interest match is fine; a repo
    that trips anti-signals harder than it matches interests is not.
    """
    if not is_ai_relevant(repo):
        return False
    interest = interest_score(repo)
    anti = anti_score(repo)
    if anti > 0 and interest == 0:
        return False
    if anti >= 2 and interest < 3:
        return False
    return True


# --- emergence signal -------------------------------------------------------
#
# "Trending" on GitHub means star velocity, not star total and not recency of
# creation. We approximate velocity two ways:
#   * repos scraped from github.com/trending carry a real "stars this week"
#   * repos from the Search API get their lifetime average weekly rate
# Momentum (share of total stars earned this week) separates a repo still
# climbing from one that arrived years ago and merely stayed popular.

MIN_MOMENTUM = 0.03   # must have earned >=3% of its stars in the last week
STAR_FLOOR = 150      # below this, weekly velocity is noise
INTEREST_WEIGHT = 0.12
INTEREST_CAP = 8


def age_days(repo, today=None):
    """Days since creation, or None when the repo carries no creation date."""
    created = (repo.get("created_at") or "")[:10]
    if not created:
        return None
    try:
        born = datetime.date.fromisoformat(created)
    except ValueError:
        return None
    today = today or datetime.date.today()
    return max((today - born).days, 1)


def _stars(repo):
    return repo.get("stargazers_count", 0) or 0


def weekly_velocity(repo, today=None):
    """Stars gained in the last week.

    Real figure when we scraped it off the trending page, otherwise the repo's
    lifetime average weekly rate, which for a young repo is a good proxy.
    """
    scraped = repo.get("weekly_stars") or 0
    if scraped:
        return float(scraped)
    age = age_days(repo, today)
    if age is None:
        return float(_stars(repo))
    return _stars(repo) * 7.0 / age


def momentum(repo, today=None):
    """Share of the repo's total stars earned in the last week."""
    total = _stars(repo)
    if total <= 0:
        return 0.0
    return weekly_velocity(repo, today) / total


def emergence_score(repo, today=None):
    """Velocity leads; Avi's interest signals tune it within a bounded range.

    Interest can lift a repo by at most ~2x, so a genuinely fast-moving repo
    always outranks a slow one that merely name-drops more keywords.
    """
    interest = min(interest_score(repo), INTEREST_CAP)
    return weekly_velocity(repo, today) * (1 + INTEREST_WEIGHT * interest)


_GH_LINK = re.compile(r"github\.com/([^/\s)]+/[^/\s)]+)")


def previously_seen_repos(notes_dir, limit=3):
    if not os.path.isdir(notes_dir):
        return set()
    files = sorted(
        (f for f in os.listdir(notes_dir) if f.endswith(".md")),
        reverse=True,
    )[:limit]
    seen = set()
    for fname in files:
        try:
            with open(os.path.join(notes_dir, fname), encoding="utf-8") as fh:
                for m in _GH_LINK.finditer(fh.read()):
                    seen.add(m.group(1).rstrip(")"))
        except OSError:
            continue
    return seen


_API = "https://api.github.com"
_HEADERS = {
    "Accept": "application/vnd.github+json",
    "User-Agent": "trending-ai-repos-script",
}


def _build_headers():
    headers = dict(_HEADERS)
    token = os.environ.get("GITHUB_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def _get(url):
    req = urllib.request.Request(url, headers=_build_headers())
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8"))


def search_trending(since, topic, per_page=30, min_stars=STAR_FLOOR):
    """Young repos that already have real traction.

    `since` is the creation-window start, not last week: a repo born seven days
    ago cannot have accumulated the stars that make it worth reading about.
    """
    q = urllib.parse.quote(f"topic:{topic} created:>={since} stars:>={min_stars}")
    url = f"{_API}/search/repositories?q={q}&sort=stars&order=desc&per_page={per_page}"
    return _get(url).get("items", [])


# --- github.com/trending scrape ---------------------------------------------

_TRENDING_URL = "https://github.com/trending"
_ROW_SPLIT = '<article class="Box-row">'
_BROWSER_UA = {"User-Agent": "Mozilla/5.0 (compatible; trending-ai-repos-script)"}


def _strip_tags(s):
    return _html.unescape(re.sub(r"<[^>]+>", "", s)).strip()


def _to_int(s):
    digits = re.sub(r"[^\d]", "", s or "")
    return int(digits) if digits else 0


def parse_trending_html(page):
    """Pull repo rows off a github.com/trending page.

    Returns raw-API-shaped dicts so they flow through the same filters as
    Search API results, plus a real `weekly_stars` figure.
    """
    out = []
    for row in page.split(_ROW_SPLIT)[1:]:
        m = re.search(r'<h2 class="h3 lh-condensed">.*?href="/([^"]+)"', row, re.S)
        if not m:
            continue
        full_name = m.group(1).strip("/")
        if full_name.count("/") != 1:
            continue
        desc = re.search(r'<p class="col-9[^"]*">(.*?)</p>', row, re.S)
        lang = re.search(r'<span itemprop="programmingLanguage">(.*?)</span>', row, re.S)
        stars = re.search(r'href="/[^"]+/stargazers"[^>]*>(.*?)</a>', row, re.S)
        weekly = re.search(r"([\d,]+)\s*stars this week", row)
        out.append({
            "full_name": full_name,
            "name": full_name.split("/")[-1],
            "html_url": f"https://github.com/{full_name}",
            "description": _strip_tags(desc.group(1)) if desc else "",
            "language": _strip_tags(lang.group(1)) if lang else None,
            "stargazers_count": _to_int(_strip_tags(stars.group(1))) if stars else 0,
            "weekly_stars": _to_int(weekly.group(1)) if weekly else 0,
            "topics": [],
            "source": "trending",
        })
    return out


def fetch_trending(language=""):
    url = f"{_TRENDING_URL}?since=weekly"
    if language:
        url = f"{_TRENDING_URL}/{urllib.parse.quote(language)}?since=weekly"
    req = urllib.request.Request(url, headers=_BROWSER_UA)
    with urllib.request.urlopen(req, timeout=30) as resp:
        return parse_trending_html(resp.read().decode("utf-8", "ignore"))


def enrich_repo(repo):
    """Fill in topics and creation date for a scraped trending row."""
    try:
        data = _get(f"{_API}/repos/{repo['full_name']}")
    except Exception:
        return repo
    repo.setdefault("topics", [])
    repo["topics"] = list(data.get("topics") or [])
    repo["created_at"] = data.get("created_at") or ""
    repo["pushed_at"] = data.get("pushed_at") or ""
    repo["language"] = data.get("language") or repo.get("language")
    repo["description"] = data.get("description") or repo.get("description") or ""
    repo["stargazers_count"] = data.get("stargazers_count") or repo.get("stargazers_count", 0)
    return repo


def fetch_readme_excerpt(full_name, max_chars=4000):
    try:
        data = _get(f"{_API}/repos/{full_name}/readme")
        content = base64.b64decode(data.get("content", "")).decode("utf-8", "ignore")
        return content[:max_chars]
    except Exception:
        return ""


def normalize_repo(raw, readme="", today=None):
    pushed = (raw.get("pushed_at") or "")[:10]
    created = (raw.get("created_at") or "")[:10]
    return {
        "full_name": raw.get("full_name", ""),
        "url": raw.get("html_url", ""),
        "description": raw.get("description") or "",
        "stars": raw.get("stargazers_count", 0) or 0,
        "weekly_stars": int(round(weekly_velocity(raw, today))),
        "momentum": round(momentum(raw, today), 4),
        "age_days": age_days(raw, today),
        "source": raw.get("source", "search"),
        "language": raw.get("language") or "Unknown",
        "topics": list(raw.get("topics") or []),
        "pushed_at": pushed,
        "created_at": created,
        "readme_excerpt": readme,
    }


SEARCH_TOPICS = [
    # core AI/LLM
    "llm", "agents", "ai-agents", "generative-ai", "mcp",
    # Avi's interest areas
    "rag", "agent-skills", "prompt-engineering", "ai-agent",
    "knowledge-management", "text-to-speech", "obsidian",
    "instructional-design", "vibe-coding", "local-llm",
]

# Trending-page slices. "" is the all-languages page; the rest widen the catch
# for languages AI work actually ships in.
TRENDING_LANGUAGES = ["", "python", "typescript", "jupyter-notebook", "rust"]

# Enriching a scraped row costs one core-API call. Authenticated runs get
# 5,000/hour so the cap never bites; an unauthenticated run gets 60, so keep
# well under it and say so rather than truncating silently.
MAX_ENRICH_AUTH = 60
MAX_ENRICH_ANON = 12


def select_top(repos, seen, limit=10, star_floor=STAR_FLOOR,
               min_momentum=MIN_MOMENTUM, today=None, warnings=None):
    by_name = {}
    for r in repos:
        name = r.get("full_name", "")
        if not name or name in seen:
            continue
        if not is_relevant(r):
            continue
        prev = by_name.get(name)
        # Prefer the copy carrying a real weekly figure, then the higher star count.
        if prev is None:
            by_name[name] = r
        elif (r.get("weekly_stars") or 0) > (prev.get("weekly_stars") or 0):
            by_name[name] = r
        elif _stars(r) > _stars(prev):
            by_name[name] = r

    def qualifies(r, floor):
        return _stars(r) >= floor and momentum(r, today) >= min_momentum

    qualified = [r for r in by_name.values() if qualifies(r, star_floor)]

    # Graceful fallback: if too few clear the floor, lower it so the list never
    # silently shrinks, and record that we did.
    floor_used = star_floor
    if len(qualified) < limit:
        for fallback in (100, 50, 25, 0):
            if fallback >= star_floor:
                continue
            qualified = [r for r in by_name.values() if qualifies(r, fallback)]
            floor_used = fallback
            if len(qualified) >= limit:
                break
        if warnings is not None and floor_used < star_floor:
            warnings.append(
                f"only {len([r for r in by_name.values() if qualifies(r, star_floor)])} "
                f"repos cleared the {star_floor}-star floor; lowered to "
                f"{floor_used} stars to fill the list"
            )

    ranked = sorted(qualified, key=lambda r: emergence_score(r, today), reverse=True)
    return ranked[:limit]


def main():
    today = datetime.date.today()
    since = emergence_since(today)
    week = iso_week_string(today)
    notes_dir = os.path.expanduser(
        os.environ.get(
            "TRENDING_NOTES_DIR",
            "~/Documents/avi-workspace/Researches/Trending Repos",
        )
    )
    seen = previously_seen_repos(notes_dir, limit=3)

    warnings = []
    collected = []

    # Source 1: github.com/trending weekly — real star velocity. Plain HTTPS,
    # so it doesn't spend Search API quota. Filter on the scraped description
    # before enriching, to keep core-API calls low.
    has_token = bool(os.environ.get("GITHUB_TOKEN"))
    scraped = {}
    rows_seen = 0
    for lang in TRENDING_LANGUAGES:
        label = lang or "all"
        try:
            rows = fetch_trending(lang)
        except Exception as e:
            warnings.append(f"trending page failed for '{label}': {e}")
            continue
        # A page that fetches fine but parses to nothing means GitHub changed
        # its markup. Without this the run still "succeeds" on the search
        # source alone and the broken scraper stays invisible for weeks.
        if not rows:
            warnings.append(f"trending page '{label}' parsed 0 rows (markup change?)")
            continue
        rows_seen += len(rows)
        for r in rows:
            if r["full_name"] not in scraped and is_relevant(r):
                scraped[r["full_name"]] = r
    if rows_seen and not scraped:
        warnings.append(
            f"trending pages yielded {rows_seen} rows but none passed the AI filter"
        )

    # Enrich the fastest movers first, so if the cap bites we keep the best ones.
    candidates = sorted(
        scraped.values(), key=lambda r: r.get("weekly_stars") or 0, reverse=True
    )
    cap = MAX_ENRICH_AUTH if has_token else MAX_ENRICH_ANON
    if len(candidates) > cap:
        warnings.append(
            f"{len(candidates)} trending repos passed the filter; enriching the "
            f"top {cap} by weekly stars ({'authenticated' if has_token else 'no GITHUB_TOKEN'})"
        )
        candidates = candidates[:cap]
    for r in candidates:
        collected.append(enrich_repo(r))

    # Source 2: Search API — young repos with traction that haven't hit the
    # trending page (or hit it on a day we didn't look).
    # GitHub Search API: 10 req/min unauthenticated, 30 req/min authenticated.
    delay = 2 if os.environ.get("GITHUB_TOKEN") else 6
    for i, topic in enumerate(SEARCH_TOPICS):
        if i > 0:
            time.sleep(delay)
        try:
            for r in search_trending(since, topic, per_page=30):
                r["source"] = "search"
                collected.append(r)
        except Exception as e:
            warnings.append(f"search failed for topic '{topic}': {e}")

    top = select_top(collected, seen=seen, limit=10, today=today, warnings=warnings)
    if len(top) < 10:
        warnings.append(f"only {len(top)} emerging AI/LLM repos found (wanted 10)")

    briefs = []
    for r in top:
        readme = fetch_readme_excerpt(r.get("full_name", ""))
        if not readme:
            warnings.append(f"no README for {r.get('full_name')}")
        briefs.append(normalize_repo(r, readme=readme, today=today))

    out = {
        "week": week,
        "since": since,
        "window_days": EMERGENCE_WINDOW_DAYS,
        "generated_for": today.isoformat(),
        "notes_dir": notes_dir,
        "count": len(briefs),
        "warnings": warnings,
        "repos": briefs,
    }
    json.dump(out, sys.stdout, ensure_ascii=False, indent=2)


if __name__ == "__main__":
    main()
