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
    # generative media — these repos often never say "llm" or "ai"
    "text-to-image", "image-generation", "text-to-video", "video-generation",
    "stable-diffusion", "diffusion-models", "comfyui", "text-to-speech",
    "multimodal", "ai-tools", "ai-assistant", "openai", "anthropic", "chatgpt",
}
AI_KEYWORDS = (
    "llm", "agent", "rag", "machine learning", "generative",
    "gpt", "transformer", "neural", "prompt", "inference",
    "fine-tun", "embedding", "diffusion", "ai ",
    "text-to-image", "text-to-video", "image generation", "video generation",
    "comfyui", "multimodal", "claude", "openai", "anthropic", "copilot",
    "text-to-speech",
)

# What the digest is FOR: generative AI that changes how work gets done. The
# lanes are named because they do double duty — they feed interest_score, and
# select_top reserves slots for the ones that would otherwise get outrun (see
# LANE_FLOORS).

# Generative media: making pictures, video, audio and voice. Deliberately NOT
# the word "design" on its own, which matches every DESIGN.md coding-agent repo
# and would let the creative lane fill up without any actual media in it.
CREATIVE_SIGNALS = (
    "image generation", "image-generation", "text-to-image", "text to image",
    "text-to-video", "text to video", "video generation", "video-generation",
    "video", "diffusion", "comfyui", "inpaint", "outpaint", "upscal",
    "image edit", "photo edit", "animation", "animate", "avatar",
    "lipsync", "lip-sync", "3d model", "render", "music", "audio",
    "voice clone", "voice-clone", "text-to-speech", "text to speech", "tts",
    "speech", "voice", "transcription", "subtitle", "caption",
    "generative art", "creative",
)

# Getting more out of a model: the craft, not the product.
TECHNIQUE_SIGNALS = (
    "prompt", "context engineering", "context-engineering", "system prompt",
    "fine-tun", "lora", "distill", "quantiz", "evaluation", "benchmark",
    "reasoning", "chain-of-thought", "tool use", "tool-use",
    "structured output", "rag", "retrieval", "embedding", "context window",
    "from scratch", "build your own",
)

# Assistants, copilots, and agents that do real work.
PRODUCTIVITY_SIGNALS = (
    "assistant", "copilot", "automation", "automate", "workflow",
    "productivity", "browser agent", "computer use", "computer-use",
    "coding agent", "code generation", "code-generation", "vibe coding",
    "vibe-coding", "deep research", "research agent", "presentation", "slides",
)

# Agents, skills, MCP, orchestration — the substrate all three lanes run on.
AGENT_SIGNALS = (
    "agent", "agents", "agentic", "multi-agent", "orchestrat", "mcp",
    "claude", "claude-code", "skill", "skills", "autonomous",
    "human-in-the-loop", "harness", "open-source model", "local-first",
)

# Repos matching more of these rank higher (see interest_score).
INTEREST_SIGNALS = (
    CREATIVE_SIGNALS + TECHNIQUE_SIGNALS + PRODUCTIVITY_SIGNALS + AGENT_SIGNALS
)

# Topics/keywords that look AI-adjacent but are NOT what this digest is for. A
# repo whose signal is dominated by these is dropped (see is_relevant). The
# note-taking / PKM entries are deliberate: an AI note app is a knowledge-
# management tool, not a generative-AI one, and those were crowding the list.
ANTI_SIGNALS = (
    "crypto", "blockchain", "web3", "trading-bot", "robot", "robotics",
    "game", "gaming", "anti-detect", "antidetect", "scraper", "scraping",
    "deepfake", "face-swap", "faceswap",
    "note-taking", "note taking", "zettelkasten", "second brain",
    "second-brain", "personal knowledge", "knowledge management",
    "knowledge-management", "obsidian plugin", "wiki",
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

REPO_COUNT = 5        # repos in the weekly digest
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


LANE_SIGNALS = {
    "creative": CREATIVE_SIGNALS,
    "technique": TECHNIQUE_SIGNALS,
}
# Slots held for lanes that raw velocity would otherwise crowd out. Coding-agent
# tooling moves an order of magnitude faster than generative-media work, so
# without this the list is whatever agent framework is hottest, every week.
# Sized against REPO_COUNT: 1 of 5 each, the same 60/40 velocity-to-reserved
# split as the 2-of-10 the list used before it was cut to five.
LANE_FLOORS = {"creative": 1, "technique": 1}


def repo_lanes(repo):
    """Which reserved lanes this repo belongs to (may be none, or several)."""
    text = _repo_text(repo)
    return {
        lane for lane, signals in LANE_SIGNALS.items()
        if any(sig in text for sig in signals)
    }


def _trade_in(picked, promo, floors, protected=()):
    """Lowest-ranked pick we can swap out for `promo` without breaking a floor.

    `picked` holds the originals in rank order, so walking it backwards gives up
    the slowest repo first. Two kinds of pick are passed over: one already
    promoted into a reserved slot (otherwise a lane keeps trading its own repo
    away and never reaches its floor), and the last one holding up another
    lane's floor.
    """
    before = {lane: sum(1 for r in picked if lane in repo_lanes(r)) for lane in floors}
    for cand in reversed(picked):
        if any(cand is p for p in protected):
            continue
        trial = [r for r in picked if r is not cand] + [promo]
        after = {lane: sum(1 for r in trial if lane in repo_lanes(r)) for lane in floors}
        if all(after[lane] >= min(floors[lane], before[lane]) for lane in floors):
            return cand
    return None


def fill_lane_floors(ranked, limit, floors=LANE_FLOORS, warnings=None):
    """Promote the fastest repo of an under-represented lane into the list.

    Velocity still decides the order and still fills most of the list; this only
    guarantees the reserved lanes are represented when candidates exist.
    """
    picked = list(ranked[:limit])
    if len(picked) < limit:
        return picked  # list isn't even full — nothing to trade away
    waiting = list(ranked[limit:])
    promoted = []
    for lane in sorted(floors):
        floor = floors[lane]
        while sum(1 for r in picked if lane in repo_lanes(r)) < floor:
            promo = next((r for r in waiting if lane in repo_lanes(r)), None)
            if promo is None:
                if warnings is not None:
                    have = sum(1 for r in picked if lane in repo_lanes(r))
                    warnings.append(
                        f"only {have} '{lane}' repos qualified this week "
                        f"({floor} slots reserved); filled with the next fastest"
                    )
                break
            victim = _trade_in(picked, promo, floors, protected=promoted)
            if victim is None:
                break
            picked.remove(victim)
            waiting.remove(promo)
            picked.append(promo)
            promoted.append(promo)
    return picked


def emergence_score(repo, today=None):
    """Velocity leads; Avi's interest signals tune it within a bounded range.

    Interest can lift a repo by at most ~2x, so a genuinely fast-moving repo
    always outranks a slow one that merely name-drops more keywords.
    """
    interest = min(interest_score(repo), INTEREST_CAP)
    return weekly_velocity(repo, today) * (1 + INTEREST_WEIGHT * interest)


_GH_LINK = re.compile(r"github\.com/([^/\s)]+/[^/\s)]+)")
# Only weekly report notes count as history. The folder also holds index notes
# ("מדריך…", "📌 Trending Repos…") whose names sort ABOVE every report, so an
# unfiltered reverse sort spent the whole limit on them and deduped against a
# single week — which is how W32 repos resurfaced in W34's candidate pool.
_REPORT_NOTE = re.compile(r"^\d{4}-W\d{2}\b")


def previously_seen_repos(notes_dir, limit=3):
    if not os.path.isdir(notes_dir):
        return set()
    files = sorted(
        (f for f in os.listdir(notes_dir)
         if f.endswith(".md") and _REPORT_NOTE.match(f)),
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
    # core gen AI
    "generative-ai", "llm", "ai-agents", "agents", "mcp", "multimodal",
    # techniques — getting more out of a model
    "prompt-engineering", "rag", "fine-tuning", "llmops",
    # creative work — image, video, audio
    "text-to-image", "image-generation", "text-to-video", "video-generation",
    "stable-diffusion", "comfyui", "text-to-speech",
    # productivity — assistants, copilots, agentic tooling
    "ai-tools", "ai-assistant", "code-generation", "vibe-coding",
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
    picked = fill_lane_floors(ranked, limit, warnings=warnings)
    return sorted(picked, key=lambda r: emergence_score(r, today), reverse=True)


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

    top = select_top(collected, seen=seen, limit=REPO_COUNT, today=today, warnings=warnings)
    if len(top) < REPO_COUNT:
        warnings.append(
            f"only {len(top)} emerging gen-AI repos found (wanted {REPO_COUNT})"
        )

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
