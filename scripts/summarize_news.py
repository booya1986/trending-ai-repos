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
CANDIDATES = 25                # how many headlines the model chooses from
WANTED = 5                     # how many make the digest

SYSTEM_PROMPT = """\
You are the news editor for Avi Levi's weekly generative-AI digest. He wants to \
stay current on gen AI without following a dozen sources: what shipped, who \
raised or acquired, which models and agent products changed, and which \
techniques are now practical. He builds with Claude, agents, and MCP, and works \
in creative and productivity tooling.

Rules:
- Pick the five biggest gen-AI stories of the week, ranked by how much they \
matter, across every area of AI. Rank on the story itself: how significant it \
is, how much attention it is getting, and how much it changes what someone \
building with gen AI can do.
- Do NOT balance across categories. If the five biggest stories of the week \
are all model launches, return five model launches. The category label is \
descriptive only, never a quota.
- Prefer concrete news (launches, releases, funding, acquisitions, benchmarks, \
technique write-ups) over opinion and think-pieces.
- Never pick two items covering the same story. Choose the better source.
- Ground every claim in the headline and summary you are given. Never invent \
details, numbers, or capabilities.
- Do not use em-dashes anywhere. Use colons, commas, or parentheses instead.
- Hebrew must read naturally, not as translated English. Keep product and \
company names in Latin script inside the Hebrew text.
- summary: one sentence on what happened. why: one sentence on why it matters \
to someone building with gen AI."""

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
                        "enum": ["product", "company", "technique", "creative", "research"],
                    },
                    "headline_he": {"type": "string"},
                    "headline_en": {"type": "string"},
                    "summary_he": {"type": "string"},
                    "summary_en": {"type": "string"},
                    "why_he": {"type": "string"},
                    "why_en": {"type": "string"},
                },
                "required": [
                    "index", "category", "headline_he", "headline_en",
                    "summary_he", "summary_en", "why_he", "why_en",
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
        traction = f" [{points} HN points]" if points else ""
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
        out.append({
            "headline_he": it["title"],
            "headline_en": it["title"],
            "summary_he": "",
            "summary_en": (it.get("summary") or "")[:200],
            "why_he": "",
            "why_en": "",
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
            # max_tokens covers thinking + text together on claude-sonnet-5
            # (adaptive thinking is on by default), so leave real headroom.
            message = client.messages.create(
                model=MODEL,
                max_tokens=16000,
                system=SYSTEM_PROMPT,
                messages=[{"role": "user", "content": prompt}],
                output_config={"format": {"type": "json_schema", "schema": NEWS_SCHEMA}},
            )
            if message.stop_reason == "max_tokens":
                warnings.append("news summary hit max_tokens; section may be short")
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
                    "why_he": entry["why_he"],
                    "why_en": entry["why_en"],
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
