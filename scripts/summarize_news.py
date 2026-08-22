#!/usr/bin/env python3
"""Turn raw news candidates into a short bilingual digest section.

Usage: summarize_news.py <in.json> <out.json>

One Claude call picks the week's most significant generative-AI stories and
writes Hebrew + English copy for each. If ANTHROPIC_API_KEY is not set (or the
call fails) it degrades to the top English headlines, so the report always has
a news section and the pipeline never fails on this step.

Two deliberate choices, both learned from generate_briefs.py:
  * Structured outputs (output_config.format) instead of "return only JSON".
    The model cannot emit unparseable output, so the truncated-JSON failure
    that silently emptied the briefs for two weeks cannot recur here.
  * The model selects by INDEX, never by echoing a URL. Links come from the
    fetched data, so a story can never point at a hallucinated address.
"""
import json
import os
import sys

MODEL = "claude-sonnet-5"      # matches generate_briefs.py
CANDIDATES = 40                # how many headlines the model chooses from
WANTED = 10                    # how many make the digest
# max_tokens covers thinking AND text together on claude-sonnet-5, where
# adaptive thinking is on by default. 16000 was sized for five stories with two
# short fields each; ten stories with an insight paragraph apiece truncated the
# response mid-string, the JSON failed to parse, and the digest silently
# published English headlines with empty summaries. Sized with real headroom,
# and retried at double on a truncated response.
MAX_TOKENS = 32000

SYSTEM_PROMPT = """\
You are the news editor for Avi Levi's weekly generative-AI digest. He wants to \
stay current on gen AI without following a dozen sources: what shipped, who \
raised or acquired, which models and agent products changed, and which \
techniques are now practical. He builds with Claude, agents, and MCP, and works \
in creative and productivity tooling.

Rules:
- Pick the ten stories that would keep Avi best informed about generative AI \
this week. The test is simple: after reading these ten, is he up to date? \
Rank on the story itself: how significant it is, how much attention it is \
getting, and how much it changes what someone building with gen AI can do.
- COVER THE WHOLE FIELD, not just product launches. Model releases and \
capability jumps, company and money news, research results and benchmarks, \
new techniques and tools worth using, creative and media generation, and \
policy, safety or regulation when it actually changes what people can build. \
A week of ten product announcements is almost always a sign you have looked \
only at the obvious sources.
- Do NOT balance mechanically. If one area genuinely dominated the week, let \
it dominate. But do not let a single category fill the list by default.
- Prefer concrete news (launches, releases, funding, acquisitions, benchmarks, \
technique write-ups) over opinion and think-pieces.
- Never pick two items covering the same story. Candidates arrive from press \
feeds, Hacker News, Reddit and X at once, so the SAME story often appears \
several times under different wording. Before choosing, group the candidates \
by the event they describe and take at most one from each group. Prefer the \
primary source or the outlet that reported it over the aggregator, forum \
thread or social post about it.
- Ground every claim in the headline and summary you are given. Never invent \
details, numbers, or capabilities.
- Do not use em-dashes anywhere. Use colons, commas, or parentheses instead.
- Hebrew must read naturally, not as translated English. Keep product and \
company names in Latin script inside the Hebrew text.
- summary: one or two sentences, strictly WHAT HAPPENED. Facts only: who did \
what, what shipped, what the numbers were. No interpretation here.
- insight: two or three sentences, strictly WHAT TO TAKE FROM IT. What it \
changes, what it now makes possible, what it says about where things are \
going, or what Avi should do differently knowing it. Never restate the \
summary. If the only thing you can write is a rephrasing of what happened, \
the story does not belong in the ten."""

NEWS_SCHEMA = {
    "type": "object",
    "properties": {
        "items": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "index": {
                        "type": "integer",
                        "description": "Index of the chosen candidate, from the list given.",
                    },
                    "category": {
                        "type": "string",
                        "enum": ["model", "product", "company", "research",
                                 "technique", "tool", "creative", "policy"],
                    },
                    "headline_he": {"type": "string"},
                    "headline_en": {"type": "string"},
                    "summary_he": {
                        "type": "string",
                        "description": "1-2 sentences, facts only: what happened.",
                    },
                    "summary_en": {"type": "string"},
                    "insight_he": {
                        "type": "string",
                        "description": "2-3 sentences: what to take from it. Never restates the summary.",
                    },
                    "insight_en": {"type": "string"},
                },
                "required": [
                    "index", "category", "headline_he", "headline_en",
                    "summary_he", "summary_en", "insight_he", "insight_en",
                ],
                "additionalProperties": False,
            },
        }
    },
    "required": ["items"],
    "additionalProperties": False,
}


def _candidate_list(items):
    lines = []
    for i, it in enumerate(items):
        summary = (it.get("summary") or "")[:280]
        points = it.get("points") or 0
        # Points now come from Hacker News and from Reddit, so label them by
        # source rather than calling everything HN.
        traction = f" [{points} points]" if points else ""
        lines.append(
            f"{i}. {it['title']}\n"
            f"   source: {it.get('source', '')}{traction} | {it.get('published', '')[:10]}\n"
            f"   {summary}"
        )
    return "\n".join(lines)


def _response_text(message):
    """First text block. Never content[0] — adaptive thinking puts a thinking
    block there, which has no .text (the bug that killed the briefs step)."""
    for block in message.content:
        if block.type == "text":
            return block.text
    return ""


def _fallback(items, warnings):
    """No API key, or the call failed: ship English headlines rather than nothing."""
    out = []
    for it in items[:WANTED]:
        # Both language slots carry the same English text on purpose. Leaving
        # the Hebrew side empty rendered ten blank paragraphs on the page,
        # which reads as a broken report rather than a degraded one.
        text = (it.get("summary") or "")[:300]
        out.append({
            # Flagged explicitly rather than inferred. An earlier gate looked
            # for empty Hebrew text, which stopped detecting anything the
            # moment the fallback started filling both language slots.
            "degraded": True,
            "headline_he": it["title"],
            "headline_en": it["title"],
            "summary_he": text,
            "summary_en": text,
            "insight_he": "",
            "insight_en": "",
            "category": "product",
            "url": it["url"],
            "source": it.get("source", ""),
            "published": it.get("published", ""),
        })
    return out


def summarize(infile, outfile):
    data = json.load(open(infile, encoding="utf-8"))
    items = data.get("items", [])
    warnings = list(data.get("warnings") or [])

    if not items:
        warnings.append("no news items to summarize")
        data["stories"] = []
        data["warnings"] = warnings
        json.dump(data, open(outfile, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
        return

    pool = items[:CANDIDATES]
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        print("ANTHROPIC_API_KEY not set; using headline-only news section", file=sys.stderr)
        warnings.append("news summarized without Claude (no ANTHROPIC_API_KEY)")
        stories = _fallback(pool, warnings)
    else:
        try:
            import anthropic
            client = anthropic.Anthropic(api_key=api_key)
            prompt = (
                f"Here are this week's candidate gen-AI stories.\n\n"
                f"{_candidate_list(pool)}\n\n"
                f"Choose the {WANTED} most significant and write the digest entries."
            )
            def call(budget):
                # Streaming, not create(): the SDK refuses a non-streaming
                # request whose max_tokens implies it could run past ten
                # minutes, which 32000 does. Same pattern the L&D pipeline's
                # editorial review already uses.
                with client.messages.stream(
                    model=MODEL,
                    max_tokens=budget,
                    system=SYSTEM_PROMPT,
                    messages=[{"role": "user", "content": prompt}],
                    output_config={"format": {"type": "json_schema", "schema": NEWS_SCHEMA}},
                ) as stream:
                    return stream.get_final_message()

            message = call(MAX_TOKENS)
            if message.stop_reason == "max_tokens":
                # A truncated response is unparseable JSON, so retrying here is
                # the difference between the real digest and English headlines.
                warnings.append(
                    f"news summary hit max_tokens at {MAX_TOKENS}; retried at {MAX_TOKENS * 2}")
                print(f"news summary truncated at {MAX_TOKENS}, retrying", file=sys.stderr)
                message = call(MAX_TOKENS * 2)
                if message.stop_reason == "max_tokens":
                    warnings.append("news summary truncated again; falling back to headlines")
            parsed = json.loads(_response_text(message))
            stories = []
            for entry in parsed.get("items", []):
                idx = entry.get("index")
                if not isinstance(idx, int) or not 0 <= idx < len(pool):
                    continue  # model named a candidate that doesn't exist
                src = pool[idx]
                stories.append({
                    "headline_he": entry["headline_he"],
                    "headline_en": entry["headline_en"],
                    "summary_he": entry["summary_he"],
                    "summary_en": entry["summary_en"],
                    "insight_he": entry["insight_he"],
                    "insight_en": entry["insight_en"],
                    "category": entry.get("category", "product"),
                    "url": src["url"],
                    "source": src.get("source", ""),
                    "published": src.get("published", ""),
                })
            if not stories:
                raise ValueError("model returned no usable stories")
            print(f"news: {len(stories)} stories from {len(pool)} candidates", file=sys.stderr)
        except Exception as e:
            print(f"news summary failed: {type(e).__name__}: {e}", file=sys.stderr)
            warnings.append(f"news summary failed ({type(e).__name__}); used headlines only")
            stories = _fallback(pool, warnings)

    data["stories"] = stories
    data["warnings"] = warnings
    json.dump(data, open(outfile, "w", encoding="utf-8"), ensure_ascii=False, indent=2)


def main():
    if len(sys.argv) < 3:
        print("usage: summarize_news.py <in.json> <out.json>", file=sys.stderr)
        sys.exit(2)
    summarize(sys.argv[1], sys.argv[2])


if __name__ == "__main__":
    main()
