#!/usr/bin/env python3
"""
Generate rich bilingual (Hebrew/English) briefs for each repo using Claude API.

Reads the raw JSON from fetch_trending_repos.py, enriches each repo with
brief_html and narration fields, and writes to an output JSON file.

If ANTHROPIC_API_KEY is not set, copies input to output unchanged so the
downstream build_report.py can still fall back to plain descriptions.

Requires: pip install anthropic
"""
import html as h
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from build_report import wrap_brief_section


SYSTEM_PROMPT = """\
You write weekly AI/LLM trending-repo briefs for Avi Levi: an instructional \
designer and AI enthusiast who cares about agents, RAG, MCP, knowledge \
management, TTS/audio, Hebrew content, and learning tools.

Rules:
- Ground every claim in the README/description. Never invent capabilities.
- Do not use em-dashes anywhere. Use colons, commas, or parentheses instead.
- Keep each section to 2-3 sentences.
- Write Hebrew and English as requested.
- Return only valid JSON, no markdown fences."""


def _brief_prompt(repo):
    name = repo.get("full_name", "")
    description = repo.get("description", "")
    readme = (repo.get("readme_excerpt") or "")[:3000]
    stars = repo.get("stars", 0)
    topics = ", ".join((repo.get("topics") or [])[:8])
    language = repo.get("language", "")
    return f"""\
Repo: {name}
Stars: {stars:,}
Language: {language}
Topics: {topics}
Description: {description}

README excerpt:
{readme}

Return a JSON object with exactly these keys:
{{
  "what_it_does_he": "...",
  "what_it_does_en": "...",
  "why_trending_he": "...",
  "why_trending_en": "...",
  "example_he": "...",
  "example_en": "...",
  "matters_he": "...",
  "matters_en": "...",
  "narration_he": "2-sentence Hebrew plain text for TTS: what it does and why it matters to Avi"
}}"""


def _parse_json(text):
    text = text.strip()
    for fence in ("```json", "```"):
        if fence in text:
            text = text.split(fence, 1)[1].split("```")[0].strip()
            break
    return json.loads(text)


def _build_brief_html(d):
    return (
        wrap_brief_section("What it does", h.escape(d["what_it_does_he"]), text_en=h.escape(d["what_it_does_en"])) +
        wrap_brief_section("Why it's trending", h.escape(d["why_trending_he"]), text_en=h.escape(d["why_trending_en"])) +
        wrap_brief_section("Example use case", h.escape(d["example_he"]), text_en=h.escape(d["example_en"])) +
        wrap_brief_section("Why it matters for you", h.escape(d["matters_he"]), text_en=h.escape(d["matters_en"]), is_matters=True)
    )


def generate_briefs(infile, outfile):
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        print("ANTHROPIC_API_KEY not set; skipping brief generation (using descriptions only)", file=sys.stderr)
        import shutil
        shutil.copy(infile, outfile)
        return

    import anthropic
    client = anthropic.Anthropic(api_key=api_key)

    data = json.load(open(infile, encoding="utf-8"))
    repos = data.get("repos", [])

    for i, repo in enumerate(repos):
        name = repo.get("full_name", "")
        print(f"  [{i+1}/{len(repos)}] {name}")
        try:
            resp = client.messages.create(
                model="claude-sonnet-5",
                max_tokens=1200,
                system=SYSTEM_PROMPT,
                messages=[{"role": "user", "content": _brief_prompt(repo)}],
            )
            brief_data = _parse_json(resp.content[0].text)
            repo["brief_html"] = _build_brief_html(brief_data)
            repo["narration"] = brief_data.get("narration_he", "")
        except Exception as e:
            print(f"    brief failed: {e}", file=sys.stderr)
        # Stay well inside Anthropic rate limits
        if i < len(repos) - 1:
            time.sleep(1)

    data["repos"] = repos
    json.dump(data, open(outfile, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    print(f"Wrote enriched JSON -> {outfile} ({len(repos)} repos)")


def main():
    if len(sys.argv) < 3:
        print("Usage: generate_briefs.py <input.json> <output.json>", file=sys.stderr)
        sys.exit(1)
    generate_briefs(sys.argv[1], sys.argv[2])


if __name__ == "__main__":
    main()
