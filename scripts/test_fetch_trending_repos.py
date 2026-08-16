import datetime
from fetch_trending_repos import iso_week_string, since_date
from fetch_trending_repos import is_ai_relevant
from fetch_trending_repos import previously_seen_repos
from fetch_trending_repos import normalize_repo
from fetch_trending_repos import select_top
from fetch_trending_repos import interest_score, is_relevant
from fetch_trending_repos import emergence_since, EMERGENCE_WINDOW_DAYS
from fetch_trending_repos import age_days, weekly_velocity, momentum
from fetch_trending_repos import emergence_score
from fetch_trending_repos import parse_trending_html
from fetch_trending_repos import repo_lanes, fill_lane_floors, LANE_FLOORS


def test_iso_week_string_formats_year_and_week():
    d = datetime.date(2026, 6, 4)  # ISO week 23 of 2026
    assert iso_week_string(d) == "2026-W23"


def test_since_date_is_seven_days_before():
    d = datetime.date(2026, 6, 4)
    assert since_date(d) == "2026-05-28"


def test_relevant_when_topic_matches():
    repo = {"name": "x", "description": "a tool", "topics": ["llm", "cli"]}
    assert is_ai_relevant(repo) is True


def test_relevant_when_description_has_keyword():
    repo = {"name": "x", "description": "An agent framework for RAG", "topics": []}
    assert is_ai_relevant(repo) is True


def test_irrelevant_when_no_signal():
    repo = {"name": "csv-parser", "description": "fast csv parsing", "topics": ["parser"]}
    assert is_ai_relevant(repo) is False


def test_extracts_full_names_from_note_text(tmp_path):
    note = tmp_path / "2026-W22 Trending AI Repos.md"
    note.write_text("## [foo/bar](https://github.com/foo/bar) — `Python`\nstuff\n"
                    "## [baz/qux](https://github.com/baz/qux) — `Go`\n")
    seen = previously_seen_repos(str(tmp_path), limit=5)
    assert seen == {"foo/bar", "baz/qux"}


def test_index_notes_do_not_consume_the_history_limit(tmp_path):
    # The folder holds index notes whose names sort above every report. They
    # must not crowd the weekly notes out of the dedup window.
    (tmp_path / "📌 Trending Repos — map.md").write_text("https://github.com/idx/one\n")
    (tmp_path / "מדריך - trending.md").write_text("https://github.com/idx/two\n")
    for week, repo in (("W31", "a/one"), ("W32", "b/two"), ("W33", "c/three")):
        (tmp_path / f"2026-{week} Trending AI Repos.md").write_text(
            f"https://github.com/{repo}\n"
        )
    seen = previously_seen_repos(str(tmp_path), limit=3)
    assert seen == {"a/one", "b/two", "c/three"}


def test_returns_empty_for_missing_dir():
    assert previously_seen_repos("/nonexistent/path/xyz", limit=5) == set()


def test_normalize_extracts_expected_fields():
    raw = {
        "full_name": "foo/bar",
        "html_url": "https://github.com/foo/bar",
        "description": "An LLM agent toolkit",
        "stargazers_count": 1234,
        "language": "Python",
        "topics": ["llm", "agents"],
        "pushed_at": "2026-06-03T10:00:00Z",
        "created_at": "2026-05-30T10:00:00Z",
    }
    out = normalize_repo(raw, readme="# Title\nHello")
    assert out["full_name"] == "foo/bar"
    assert out["url"] == "https://github.com/foo/bar"
    assert out["stars"] == 1234
    assert out["language"] == "Python"
    assert out["topics"] == ["llm", "agents"]
    assert out["description"] == "An LLM agent toolkit"
    assert out["pushed_at"] == "2026-06-03"
    assert out["readme_excerpt"] == "# Title\nHello"


def test_normalize_handles_missing_fields():
    out = normalize_repo({"full_name": "a/b"}, readme="")
    assert out["language"] == "Unknown"
    assert out["topics"] == []
    assert out["description"] == ""
    assert out["stars"] == 0


def test_select_top_dedups_filters_and_limits():
    repos = [
        {"full_name": "a/llm", "description": "an llm tool", "topics": ["llm"], "stargazers_count": 500},
        {"full_name": "a/llm", "description": "an llm tool", "topics": ["llm"], "stargazers_count": 500},
        {"full_name": "b/csv", "description": "csv parser", "topics": ["parser"], "stargazers_count": 999},
        {"full_name": "c/agent", "description": "agent framework", "topics": ["agents"], "stargazers_count": 100},
    ]
    out = select_top(repos, seen={"c/agent"}, limit=10)
    names = [r["full_name"] for r in out]
    assert names == ["a/llm"]


def test_select_top_sorts_by_stars_desc_and_limits():
    repos = [
        {"full_name": "x/llm1", "description": "llm", "topics": ["llm"], "stargazers_count": 10},
        {"full_name": "y/llm2", "description": "llm", "topics": ["llm"], "stargazers_count": 50},
        {"full_name": "z/llm3", "description": "llm", "topics": ["llm"], "stargazers_count": 30},
    ]
    out = select_top(repos, seen=set(), limit=2)
    assert [r["full_name"] for r in out] == ["y/llm2", "z/llm3"]


def test_select_top_keeps_higher_star_copy_on_duplicate():
    repos = [
        {"full_name": "dup/repo", "description": "llm", "topics": ["llm"], "stargazers_count": 500},
        {"full_name": "dup/repo", "description": "llm", "topics": ["llm"], "stargazers_count": 800},
    ]
    out = select_top(repos, seen=set(), limit=10)
    assert len(out) == 1
    assert out[0]["stargazers_count"] == 800


def test_interest_score_counts_distinct_signals():
    # "agent", "prompt", and "workflow" are all interest signals.
    repo = {"name": "x", "description": "an agent that turns a prompt into a workflow",
            "topics": []}
    assert interest_score(repo) >= 3
    plain = {"name": "y", "description": "a csv parser", "topics": []}
    assert interest_score(plain) == 0


def test_creative_gen_ai_repo_passes_the_ai_gate():
    # A generative-media repo may never say "ai" or "llm". It still belongs.
    repo = {"name": "nodes", "description": "Custom nodes for ComfyUI",
            "topics": ["comfyui", "stable-diffusion"]}
    assert is_ai_relevant(repo) is True
    assert is_relevant(repo) is True


def test_creative_and_productivity_signals_score():
    creative = {"name": "x", "description": "text-to-video generation with lipsync",
                "topics": ["video-generation"]}
    productive = {"name": "y", "description": "a browser agent that automates your workflow",
                  "topics": ["ai-assistant"]}
    assert interest_score(creative) >= 3
    assert interest_score(productive) >= 3


def test_knowledge_management_app_is_dropped():
    # The digest is about generative AI, not note-taking tools.
    repo = {"name": "brain", "topics": ["ai"],
            "description": "AI note-taking for your second brain, a personal knowledge base"}
    assert is_ai_relevant(repo) is True
    assert is_relevant(repo) is False


def test_agent_memory_is_still_a_technique_not_a_pkm_app():
    # Memory FOR an agent is a gen-AI technique and must survive the PKM filter.
    repo = {"name": "mem", "description": "persistent memory and retrieval for LLM agents",
            "topics": ["llm", "agents"]}
    assert is_relevant(repo) is True


def test_is_relevant_drops_anti_signal_with_no_interest():
    # AI-relevant by keyword but dominated by an anti-signal, no interest match.
    repo = {"name": "z", "description": "an llm deepfake face-swap tool", "topics": ["llm"]}
    assert is_relevant(repo) is False


def test_is_relevant_keeps_anti_signal_when_interest_present():
    # Anti-signal present but also a genuine interest match -> keep.
    repo = {"name": "z", "description": "an agent that detects deepfake for learning", "topics": ["agents"]}
    assert is_relevant(repo) is True


def test_select_top_applies_star_floor():
    # Two repos clear the 50-star floor, one does not; with enough candidates
    # the sub-floor repo is excluded.
    repos = [
        {"full_name": "a/agent", "description": "agent rag llm", "topics": ["agents"], "stargazers_count": 80},
        {"full_name": "b/agent", "description": "agent rag llm", "topics": ["agents"], "stargazers_count": 60},
    ] + [
        {"full_name": f"f/repo{i}", "description": "agent rag llm", "topics": ["agents"], "stargazers_count": 70 + i}
        for i in range(10)
    ] + [
        {"full_name": "low/repo", "description": "agent rag llm", "topics": ["agents"], "stargazers_count": 5},
    ]
    out = select_top(repos, seen=set(), limit=10)
    names = {r["full_name"] for r in out}
    assert "low/repo" not in names
    assert all((r.get("stargazers_count") or 0) >= 50 for r in out)


def test_select_top_velocity_leads_interest_only_tunes():
    # A far faster-moving repo outranks a slower one that merely matches more
    # interest keywords. Interest is a bounded multiplier, not the sort key.
    repos = [
        {"full_name": "fast/generic", "description": "an llm tool", "topics": ["llm"], "stargazers_count": 900},
        {"full_name": "slow/keyword-rich", "description": "agent skill workflow prompt copilot automation", "topics": ["agents", "rag", "mcp"], "stargazers_count": 60},
    ]
    out = select_top(repos, seen=set(), limit=10)
    assert out[0]["full_name"] == "fast/generic"


def test_interest_still_breaks_a_close_race():
    # Comparable velocity -> the repo matching Avi's interests wins.
    repos = [
        {"full_name": "plain/llm", "description": "an llm tool", "topics": ["llm"], "stargazers_count": 500},
        {"full_name": "aligned/llm", "description": "agent skill workflow prompt copilot automation", "topics": ["llm"], "stargazers_count": 480},
    ]
    out = select_top(repos, seen=set(), limit=10)
    assert out[0]["full_name"] == "aligned/llm"


def test_select_top_lowers_floor_when_too_few_qualify():
    # Only a few clear 50; fallback should fill the list and warn.
    repos = [
        {"full_name": "hi/one", "description": "agent llm", "topics": ["agents"], "stargazers_count": 90},
    ] + [
        {"full_name": f"mid/repo{i}", "description": "agent llm", "topics": ["agents"], "stargazers_count": 12 + i}
        for i in range(8)
    ]
    warnings = []
    out = select_top(repos, seen=set(), limit=10, warnings=warnings)
    assert len(out) == 9
    assert any("lowered" in w for w in warnings)


TODAY = datetime.date(2026, 8, 2)


def test_emergence_window_is_not_last_week():
    # The creation window must be wide enough for a repo to accumulate stars.
    assert EMERGENCE_WINDOW_DAYS >= 90
    assert emergence_since(TODAY) == "2026-02-03"


def test_age_days_and_missing_date():
    assert age_days({"created_at": "2026-06-12T10:00:00Z"}, today=TODAY) == 51
    assert age_days({}, today=TODAY) is None


def test_weekly_velocity_prefers_scraped_figure():
    repo = {"stargazers_count": 34004, "weekly_stars": 5737,
            "created_at": "2019-01-01T00:00:00Z"}
    assert weekly_velocity(repo, today=TODAY) == 5737.0


def test_weekly_velocity_falls_back_to_lifetime_rate():
    # ponytail-shaped: 93,616 stars in 51 days is a very fast climb.
    repo = {"stargazers_count": 93616, "created_at": "2026-06-12T00:00:00Z"}
    assert round(weekly_velocity(repo, today=TODAY)) == 12849


def test_momentum_separates_emerging_from_merely_popular():
    young = {"stargazers_count": 93616, "created_at": "2026-06-12T00:00:00Z"}
    mature = {"stargazers_count": 90000, "created_at": "2019-01-01T00:00:00Z"}
    assert momentum(young, today=TODAY) > 0.10
    assert momentum(mature, today=TODAY) < 0.01


def test_select_top_drops_popular_but_stalled_repo():
    repos = [
        {"full_name": "old/giant", "description": "an llm framework", "topics": ["llm"],
         "stargazers_count": 90000, "created_at": "2019-01-01T00:00:00Z"},
        {"full_name": "new/climber", "description": "an llm agent framework", "topics": ["llm"],
         "stargazers_count": 4000, "created_at": "2026-06-12T00:00:00Z"},
    ]
    out = select_top(repos, seen=set(), limit=10, today=TODAY)
    names = [r["full_name"] for r in out]
    assert names == ["new/climber"]


def test_select_top_prefers_copy_with_real_weekly_stars():
    repos = [
        {"full_name": "dup/repo", "description": "llm agent", "topics": ["llm"],
         "stargazers_count": 5000, "created_at": "2026-06-01T00:00:00Z"},
        {"full_name": "dup/repo", "description": "llm agent", "topics": ["llm"],
         "stargazers_count": 5000, "weekly_stars": 2200,
         "created_at": "2026-06-01T00:00:00Z"},
    ]
    out = select_top(repos, seen=set(), limit=10, today=TODAY)
    assert len(out) == 1
    assert out[0]["weekly_stars"] == 2200


TRENDING_FIXTURE = '''
<article class="Box-row">
  <h2 class="h3 lh-condensed">
    <a data-hydro-click="{&quot;x&quot;:1}" href="/citrolabs/ego-lite">ego-lite</a>
  </h2>
  <p class="col-9 color-fg-muted my-1 pr-4">The fastest browser for AI agents</p>
  <span itemprop="programmingLanguage">JavaScript</span>
  <a href="/citrolabs/ego-lite/stargazers">7,437</a>
  <span class="d-inline-block float-sm-right">4,090 stars this week</span>
</article>
'''


def test_parse_trending_html_extracts_row_fields():
    rows = parse_trending_html(TRENDING_FIXTURE)
    assert len(rows) == 1
    r = rows[0]
    assert r["full_name"] == "citrolabs/ego-lite"
    assert r["description"] == "The fastest browser for AI agents"
    assert r["language"] == "JavaScript"
    assert r["stargazers_count"] == 7437
    assert r["weekly_stars"] == 4090
    assert r["source"] == "trending"


def test_parse_trending_html_handles_empty_page():
    assert parse_trending_html("<html><body>nothing</body></html>") == []


# --- reserved lanes ---------------------------------------------------------


def _repo(name, desc, stars, weekly, topics=("llm",)):
    return {
        "full_name": name, "description": desc, "topics": list(topics),
        "stargazers_count": stars, "weekly_stars": weekly,
        "created_at": "2026-06-01T00:00:00Z",
    }


def test_repo_lanes_classifies_generative_media_as_creative():
    media = _repo("a/v", "text-to-video generation with lipsync", 900, 200)
    assert "creative" in repo_lanes(media)


def test_design_md_repo_does_not_count_as_creative():
    # A DESIGN.md dropped into a coding agent is not generative media. If it
    # counted, the creative lane would fill up with no media in it at all.
    repo = _repo("a/d", "A collection of DESIGN.md files for coding agents to "
                        "generate a matching UI", 900, 200)
    assert "creative" not in repo_lanes(repo)


def test_repo_lanes_classifies_technique():
    assert "technique" in repo_lanes(
        _repo("a/p", "prompt compression for long context windows", 900, 200))


def test_fill_lane_floors_promotes_the_fastest_repo_of_a_missing_lane():
    fast = [_repo(f"fast/agent{i}", "an llm agent framework", 9000, 900 - i)
            for i in range(10)]
    creative = [
        _repo("slow/video", "text-to-video generation with lipsync", 3000, 300),
        _repo("slower/voice", "voice clone and text-to-speech toolkit", 2000, 200),
        _repo("slowest/audio", "music generation model", 1000, 100),
    ]
    out = fill_lane_floors(fast + creative, limit=10,
                           floors={"creative": 2}, warnings=[])
    names = [r["full_name"] for r in out]
    assert len(out) == 10
    assert "slow/video" in names and "slower/voice" in names
    assert "slowest/audio" not in names          # only the floor, not a takeover
    assert "fast/agent0" in names                # the fastest is never displaced
    assert names.count("fast/agent9") == 0       # the slowest picks gave way


def test_fill_lane_floors_leaves_a_satisfied_lane_alone():
    already = [
        _repo("a/video", "text-to-video generation", 9000, 900),
        _repo("b/voice", "voice clone toolkit", 9000, 890),
    ] + [_repo(f"c/agent{i}", "an llm agent framework", 9000, 800 - i)
         for i in range(8)]
    waiting = [_repo("d/audio", "music generation model", 1000, 100)]
    out = fill_lane_floors(already + waiting, limit=10,
                           floors={"creative": 2}, warnings=[])
    assert [r["full_name"] for r in out] == [r["full_name"] for r in already]


def test_fill_lane_floors_does_not_break_another_lanes_floor():
    # Only one technique repo is in the list; filling the creative floor must
    # not evict it even though it is the slowest pick.
    picks = [_repo(f"a/agent{i}", "an llm agent framework", 9000, 900 - i)
             for i in range(9)]
    picks.append(_repo("a/prompt", "prompt compression toolkit", 9000, 800))
    waiting = [_repo("b/video", "text-to-video generation", 3000, 300),
               _repo("b/voice", "voice clone toolkit", 3000, 290)]
    out = fill_lane_floors(picks + waiting, limit=10,
                           floors={"creative": 2, "technique": 1}, warnings=[])
    names = [r["full_name"] for r in out]
    assert "a/prompt" in names
    assert "b/video" in names and "b/voice" in names


def test_fill_lane_floors_warns_when_a_lane_has_no_candidates():
    fast = [_repo(f"a/agent{i}", "an llm agent framework", 9000, 900 - i)
            for i in range(12)]
    warnings = []
    out = fill_lane_floors(fast, limit=10, floors={"creative": 2},
                           warnings=warnings)
    assert len(out) == 10
    assert any("creative" in w for w in warnings)


def test_select_top_reserves_lane_slots_end_to_end():
    repos = [_repo(f"fast/agent{i}", "an llm agent framework", 9000, 900 - i)
             for i in range(12)]
    repos += [
        _repo("slow/video", "text-to-video generation with lipsync", 3000, 300),
        _repo("slow/voice", "voice clone and text-to-speech toolkit", 2500, 250),
        _repo("slow/prompt", "prompt compression for long context windows", 2000, 200),
        _repo("slow/evals", "retrieval and embedding benchmark suite", 1800, 180),
    ]
    out = select_top(repos, seen=set(), limit=10, today=TODAY)
    lanes = [repo_lanes(r) for r in out]
    assert sum(1 for l in lanes if "creative" in l) >= LANE_FLOORS["creative"]
    assert sum(1 for l in lanes if "technique" in l) >= LANE_FLOORS["technique"]
    # Velocity still leads: the fastest repo overall is still first.
    assert out[0]["full_name"] == "fast/agent0"


def test_select_top_returns_results_in_velocity_order():
    repos = [_repo(f"fast/agent{i}", "an llm agent framework", 9000, 900 - i)
             for i in range(12)]
    repos += [_repo("slow/video", "text-to-video generation", 3000, 300),
              _repo("slow/voice", "voice clone toolkit", 2500, 250),
              _repo("slow/prompt", "prompt compression toolkit", 2000, 200),
              _repo("slow/rag", "retrieval and embedding benchmark", 1800, 180)]
    out = select_top(repos, seen=set(), limit=10, today=TODAY)
    scores = [emergence_score(r, TODAY) for r in out]
    assert scores == sorted(scores, reverse=True)
