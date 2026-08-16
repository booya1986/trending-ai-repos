import datetime

from fetch_ai_news import (
    parse_date,
    parse_feed,
    is_recent,
    is_on_beat,
    news_score,
    dedupe,
    iso_week_string,
)

NOW = datetime.datetime(2026, 8, 16, 12, 0, tzinfo=datetime.timezone.utc)


RSS = """<?xml version="1.0"?>
<rss version="2.0"><channel>
  <title>Example AI</title>
  <item>
    <title>OpenAI ships GPT-5.6 Ultrafast mode</title>
    <link>https://example.com/gpt56</link>
    <pubDate>Fri, 14 Aug 2026 10:00:00 +0000</pubDate>
    <description>&lt;p&gt;A faster inference tier for &lt;b&gt;agents&lt;/b&gt;.&lt;/p&gt;</description>
  </item>
</channel></rss>"""

ATOM = """<?xml version="1.0" encoding="utf-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <title>Example Atom</title>
  <entry>
    <title>Anthropic details Claude watermarks</title>
    <link rel="alternate" href="https://example.com/watermarks"/>
    <published>2026-08-15T09:30:00Z</published>
    <summary>How the new watermarks work.</summary>
  </entry>
</feed>"""


def test_parses_rss_item():
    items = parse_feed(RSS, "Example AI", tier=1)
    assert len(items) == 1
    it = items[0]
    assert it["title"] == "OpenAI ships GPT-5.6 Ultrafast mode"
    assert it["url"] == "https://example.com/gpt56"
    assert it["source"] == "Example AI"
    # description HTML is stripped, not passed through to the report
    assert "<b>" not in it["summary"] and "agents" in it["summary"]


def test_parses_atom_entry_link_href():
    """Atom puts the URL in link/@href, not in the element text."""
    items = parse_feed(ATOM, "Example Atom")
    assert len(items) == 1
    assert items[0]["url"] == "https://example.com/watermarks"
    assert items[0]["published_dt"] is not None


def test_parse_feed_survives_malformed_xml():
    assert parse_feed("<rss><channel><item>truncated", "Broken") == []
    assert parse_feed("", "Empty") == []


def test_parse_date_handles_both_feed_conventions():
    rfc = parse_date("Fri, 14 Aug 2026 10:00:00 +0000")
    iso = parse_date("2026-08-14T10:00:00Z")
    assert rfc == iso
    assert parse_date("not a date") is None
    assert parse_date("") is None


def test_recent_window_and_undated_items():
    fresh = {"published_dt": NOW - datetime.timedelta(days=2)}
    stale = {"published_dt": NOW - datetime.timedelta(days=30)}
    undated = {"published_dt": None}
    assert is_recent(fresh, NOW)
    assert not is_recent(stale, NOW)
    # An item with no parseable date is never assumed fresh: a feed that stops
    # stamping dates would otherwise flood the digest with its whole archive.
    assert not is_recent(undated, NOW)


def test_on_beat_keeps_gen_ai_and_drops_the_rest():
    assert is_on_beat({"title": "Anthropic launches Claude agent tooling", "summary": ""})
    assert is_on_beat({"title": "New open-source video generation model released", "summary": ""})
    assert not is_on_beat({"title": "Bitcoin ETF sees record inflows", "summary": "crypto"})
    assert not is_on_beat({"title": "Local team wins the cup", "summary": "sports"})


def test_score_prefers_fresh_on_beat_stories():
    today = {"title": "OpenAI launches new agent API", "summary": "",
             "tier": 1, "published_dt": NOW, "points": 0}
    week_old = dict(today, published_dt=NOW - datetime.timedelta(days=7))
    assert news_score(today, NOW) > news_score(week_old, NOW)


def test_hacker_news_traction_lifts_score():
    plain = {"title": "Anthropic ships a model", "summary": "", "tier": 2,
             "published_dt": NOW, "points": 0}
    hot = dict(plain, points=900)
    assert news_score(hot, NOW) > news_score(plain, NOW)


def test_dedupe_collapses_the_same_story_across_outlets():
    items = [
        {"title": "OpenAI launches GPT-5.6 Ultrafast mode", "url": "https://a.com/1", "_score": 5},
        {"title": "OpenAI Launches GPT-5.6 Ultrafast Mode", "url": "https://b.com/2", "_score": 9},
        {"title": "Anthropic details Claude watermarks", "url": "https://c.com/3", "_score": 7},
    ]
    kept = dedupe(items)
    assert len(kept) == 2
    # the higher-scoring copy of the duplicated story survives
    assert kept[0]["url"] == "https://b.com/2"


def test_dedupe_collapses_identical_urls_with_tracking_params():
    items = [
        {"title": "Story one", "url": "https://a.com/x?utm_source=rss", "_score": 3},
        {"title": "Story one again", "url": "https://a.com/x", "_score": 5},
    ]
    assert len(dedupe(items)) == 1


def test_iso_week_matches_the_repo_digest_week():
    assert iso_week_string(datetime.date(2026, 8, 16)) == "2026-W33"
