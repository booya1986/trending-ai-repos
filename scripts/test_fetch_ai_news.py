import datetime

from fetch_ai_news import (
    parse_date,
    parse_feed,
    is_recent,
    is_on_beat,
    news_score,
    dedupe,
    iso_week_string,
    canonical_url,
    _reddit_outbound,
    fetch_reddit,
    _x_posts,
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


# --- Reddit ----------------------------------------------------------------

REDDIT_ENTRY_LINK = (
    '<content type="html">&lt;a href=&quot;https://www.reddit.com/r/OpenAI/comments/x/&quot;&gt;'
    '&lt;/a&gt; &lt;span&gt;&lt;a href=&quot;https://techcrunch.com/2026/08/20/openai-ships/&quot;&gt;'
    '[link]&lt;/a&gt;&lt;/span&gt; &lt;span&gt;&lt;a href=&quot;https://www.reddit.com/r/OpenAI/comments/x/&quot;&gt;'
    '[comments]&lt;/a&gt;&lt;/span&gt;</content>'
)
REDDIT_ENTRY_IMAGE = (
    '<content type="html">&lt;span&gt;&lt;a href=&quot;https://i.redd.it/abc.jpeg&quot;&gt;'
    '[link]&lt;/a&gt;&lt;/span&gt;</content>'
)
PERMALINK = "https://www.reddit.com/r/OpenAI/comments/x/"


def test_reddit_link_post_resolves_to_the_article():
    # This is what makes a Reddit post collide with the press copy in dedupe()
    # instead of appearing twice.
    assert _reddit_outbound(REDDIT_ENTRY_LINK, PERMALINK) == \
        "https://techcrunch.com/2026/08/20/openai-ships/"


def test_reddit_image_post_keeps_the_permalink():
    # Reddit-hosted media is not an article. Returning the permalink is what
    # the caller uses to drop the post: memes dominate top-of-week otherwise.
    assert _reddit_outbound(REDDIT_ENTRY_IMAGE, PERMALINK) == PERMALINK


def test_reddit_self_post_keeps_the_permalink():
    assert _reddit_outbound("<content>no link here</content>", PERMALINK) == PERMALINK


def test_reddit_rank_lifts_score_but_less_than_hn_traction():
    top = {"title": "OpenAI ships a thing", "summary": "", "rank_hint": 0,
           "published_dt": NOW}
    buried = {"title": "OpenAI ships a thing", "summary": "", "rank_hint": 8,
              "published_dt": NOW}
    hn = {"title": "OpenAI ships a thing", "summary": "", "points": 500,
          "published_dt": NOW}
    assert news_score(top, NOW) > news_score(buried, NOW)
    assert news_score(hn, NOW) > news_score(top, NOW)


# --- canonical URLs --------------------------------------------------------

def test_canonical_url_collapses_the_ways_four_sources_link_one_article():
    variants = [
        "https://www.techcrunch.com/2026/08/20/x/?utm_source=rss&utm_medium=feed",
        "http://techcrunch.com/2026/08/20/x",
        "https://m.techcrunch.com/2026/08/20/x/amp",
        "https://techcrunch.com/2026/08/20/x/",
    ]
    assert len({canonical_url(v) for v in variants}) == 1


def test_canonical_url_keeps_a_meaningful_query():
    assert canonical_url("https://a.com/x?page=2") != canonical_url("https://a.com/x")


# --- X, as Firecrawl actually renders it -----------------------------------

X_MARKDOWN = """# OpenAI (@OpenAI)

- Followers: 4,000,000

## Latest Posts

### 1. Post
Posted: 2026\\-08\\-21T10:00:00\\.000Z
URL: [https://x\\.com/OpenAI/status/123](https://x.com/OpenAI/status/123)

> Previewing Ultrafast mode: GPT\\-5.6 Sol at up to 14x the speed, available to
>
> all paid tiers starting today.

Likes: 15,000 | Retweets: 925

### 2. Post
Posted: 2026\\-08\\-20T10:00:00\\.000Z
URL: [https://x\\.com/OpenAI/status/124](https://x.com/OpenAI/status/124)

> short

Likes: 3 | Retweets: 0
"""


def test_x_posts_recover_url_timestamp_and_engagement():
    posts = _x_posts(X_MARKDOWN)
    assert len(posts) == 1          # the "short" post is below the length floor
    post = posts[0]
    assert post["url"] == "https://x.com/OpenAI/status/123"
    assert post["posted"] == "2026-08-21T10:00:00.000Z"   # backslashes stripped
    assert post["points"] == 15925                        # likes + retweets
    assert "Ultrafast mode" in post["text"]
    assert "\\" not in post["text"]


def test_x_posts_survive_a_page_without_the_posts_section():
    assert _x_posts("# OpenAI\n\nNo posts here.") == []


def test_x_timestamp_parses_into_the_freshness_window():
    posts = _x_posts(X_MARKDOWN)
    dt = parse_date(posts[0]["posted"])
    assert dt is not None
    assert is_recent({"published_dt": dt},
                     datetime.datetime(2026, 8, 22, 12, 0, tzinfo=datetime.timezone.utc))


MULTI_RSS = """<?xml version="1.0"?>
<feed xmlns="http://www.w3.org/2005/Atom">
<category term="multi" label="multi"/>
<entry>
  <category term="OpenAI" label="r/OpenAI"/>
  <content type="html">&lt;span&gt;&lt;a href="https://techcrunch.com/a"&gt;[link]&lt;/a&gt;&lt;/span&gt;</content>
  <link href="https://www.reddit.com/r/OpenAI/comments/a/"/>
  <published>2026-08-16T01:00:00+00:00</published>
  <title>OpenAI ships an agent runtime</title>
</entry>
<entry>
  <category term="OpenAI" label="r/OpenAI"/>
  <content type="html">&lt;span&gt;&lt;a href="https://i.redd.it/meme.jpeg"&gt;[link]&lt;/a&gt;&lt;/span&gt;</content>
  <link href="https://www.reddit.com/r/OpenAI/comments/b/"/>
  <published>2026-08-16T02:00:00+00:00</published>
  <title>POV: you are born as an AI</title>
</entry>
<entry>
  <category term="LocalLLaMA" label="r/LocalLLaMA"/>
  <content type="html">no outbound link at all</content>
  <link href="https://www.reddit.com/r/LocalLLaMA/comments/c/"/>
  <published>2026-08-16T03:00:00+00:00</published>
  <title>What GPU should I buy</title>
</entry>
</feed>"""


def test_reddit_keeps_only_link_posts(monkeypatch):
    # The meme (Reddit-hosted image) and the self post must both be dropped:
    # top-of-week rewards those, and neither is news.
    monkeypatch.setattr("fetch_ai_news._get", lambda url, **kw: MULTI_RSS)
    items = fetch_reddit(warnings=[])
    assert len(items) == 1
    assert items[0]["url"] == "https://techcrunch.com/a"
    assert items[0]["source"] == "r/OpenAI"
    assert items[0]["rank_hint"] == 0


def test_reddit_uses_one_combined_request(monkeypatch):
    # One multireddit URL, not one request per subreddit: per-subreddit calls
    # 429 after the first, and Firecrawl refuses reddit.com outright.
    seen = []
    monkeypatch.setattr("fetch_ai_news._get",
                        lambda url, **kw: seen.append(url) or MULTI_RSS)
    fetch_reddit(subreddits=("OpenAI", "ClaudeAI", "LocalLLaMA"), warnings=[])
    assert len(seen) == 1
    assert "r/OpenAI+ClaudeAI+LocalLLaMA/top/.rss" in seen[0]


def test_reddit_caps_each_subreddit(monkeypatch):
    monkeypatch.setattr("fetch_ai_news._get", lambda url, **kw: MULTI_RSS)
    items = fetch_reddit(per_sub=0, warnings=[])
    assert items == []


def test_reddit_failure_is_not_fatal(monkeypatch):
    def boom(url, **kw):
        raise OSError("blocked")
    monkeypatch.setattr("fetch_ai_news._get", boom)
    warnings = []
    # retry_waits=(0,) so the gate does not sit through the real backoff.
    assert fetch_reddit(warnings=warnings, retry_waits=(0,)) == []
    assert any("reddit failed" in w for w in warnings)
