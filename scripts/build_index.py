#!/usr/bin/env python3
"""Regenerate the report archives from the report directories that exist.

Writes two indexes, both from the same scan:
  reports/index.html  the published archive page
  README.md           the same list for anyone reading the repo on GitHub

Usage: build_index.py [reports_dir]

The previous index was hand-written and drifted: it listed only 2026-W23 while
ten reports existed on disk, so the archive page was effectively broken for
everything after June. Generating it on every build means it cannot drift again,
and the README index is generated for the same reason.
"""
import datetime
import html
import json
import os
import re
import sys

BASE_URL = "https://booya1986.github.io/trending-ai-repos/reports"

DEFAULT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "reports")


def week_label(week):
    return week.replace("-", " ").replace("W", "שבוע ")


def friday_of(week):
    """The Friday the report for an ISO week was built.

    Derived from the week number rather than a file timestamp: a CI checkout
    rewrites every mtime, so the filesystem cannot answer this.
    """
    year, num = week.split("-W")
    try:
        return datetime.date.fromisocalendar(int(year), int(num), 5).isoformat()
    except ValueError:
        return ""


def _story_count(path):
    try:
        with open(os.path.join(path, "news.json"), encoding="utf-8") as fh:
            return len(json.load(fh).get("stories") or [])
    except (OSError, ValueError):
        return 0


def _repo_count(path):
    """Repos on the page, counted from the card markup."""
    try:
        with open(os.path.join(path, "index.html"), encoding="utf-8") as fh:
            return fh.read().count('<article class="card"')
    except OSError:
        return 0


def collect(reports_dir):
    weeks = []
    for name in sorted(os.listdir(reports_dir), reverse=True):
        if not re.fullmatch(r"\d{4}-W\d{2}", name):
            continue
        path = os.path.join(reports_dir, name)
        if not os.path.isdir(path) or not os.path.exists(os.path.join(path, "index.html")):
            continue
        weeks.append({
            "week": name,
            "has_mp3": os.path.exists(os.path.join(path, "report.mp3")),
            "sent": os.path.exists(os.path.join(path, ".email_sent")),
            "stories": _story_count(path),
            "repos": _repo_count(path),
            "date": friday_of(name),
        })
    return weeks


def render(weeks):
    rows = ""
    for w in weeks:
        meta = "🎧 עם הקראה" if w["has_mp3"] else ""
        rows += (
            f'<a href="{html.escape(w["week"])}/">'
            f'<span class="w">{html.escape(week_label(w["week"]))}</span>'
            f'<span class="m">{meta}</span></a>\n'
        )
    if not rows:
        rows = '<p class="empty">עוד אין דוחות.</p>'

    return f"""<!DOCTYPE html>
<html lang="he" dir="rtl">
<head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="theme-color" content="#1b1b1b">
<title>טרנדים ב-AI/LLM</title>
<link href="https://fonts.googleapis.com/css2?family=Noto+Sans+Hebrew:wght@300;400;600;700&display=swap" rel="stylesheet">
<style>
body{{background:#1b1b1b;color:#c5c1b9;font-family:'Noto Sans Hebrew',sans-serif;max-width:520px;margin:0 auto;padding:40px 16px 60px}}
h1{{color:#dcdad5;font-size:1.5rem;margin:0 0 6px}}
.sub{{color:#a09d96;font-size:.9rem;margin:0 0 28px}}
a{{color:#22c55e;text-decoration:none;display:flex;justify-content:space-between;align-items:baseline;gap:12px;
   padding:14px 0;border-bottom:1px solid rgba(34,197,94,0.12);transition:color .15s}}
a:hover{{color:#fff}}
.w{{font-weight:600}}
.m{{color:#96928c;font-size:.78rem;white-space:nowrap}}
.empty{{color:#96928c;font-size:.9rem}}
</style></head>
<body>
<h1>&#128293; טרנדים ב-AI/LLM</h1>
<p class="sub">דוחות שבועיים: חדשות ו-repos</p>
{rows}</body></html>
"""



REPORTS_START = "<!-- REPORTS:START -->"
REPORTS_END = "<!-- REPORTS:END -->"

HEADER_ROW = "| Week | Date | Report | News | Repos | Audio | Emailed |"
DIVIDER_ROW = "|---|---|---|---|---|---|---|"

README_SKELETON = """# Trending AI News

Weekly generative-AI digest: the 10 biggest stories of the week, then the 3
repos gaining the most momentum. Built every Friday by
[`friday-report.yml`](.github/workflows/friday-report.yml) and emailed every
Sunday by [`sunday-digest.yml`](.github/workflows/sunday-digest.yml). Both run
in GitHub Actions, so neither needs a machine of Avi's to be on.

Published archive: <{base}/>

## How it runs

| When | What | Where |
|---|---|---|
| Friday 07:05 IL | Fetch news and repos, write briefs, build the report and the MP3 | GitHub Actions |
| Friday, same run | Render the Obsidian note into `reports/<week>/vault-note.md` | GitHub Actions |
| Sunday 07:07 IL | Email the digest | GitHub Actions |
| Whenever the Mac is awake | Copy any missing `vault-note.md` into the vault | launchd, `com.avilevi.trending-vault-note` |

The vault is a local folder with no git remote, so CI cannot write into it.
Rendering the note in the cloud means it exists regardless of the Mac, and the
local step is a copy that backfills every week the vault is missing.

Sources: 14 press and lab feeds, Hacker News, gen-AI subreddits (link posts
only, because top-of-week rewards memes), and X. Reddit and X are reached
through Firecrawl, which is what makes them work from a CI runner; both are
optional and never fail the build.

## Secrets

| Secret | Needed by | Required? |
|---|---|---|
| `ANTHROPIC_API_KEY` | briefs, news summaries | Yes |
| `GMAIL_APP_PASSWORD` | Sunday email | Yes |
| `GOOGLE_TTS_API_KEY` | MP3 narration | Optional |
| `FIRECRAWL_API_KEY` | Reddit fallback and X | Optional; without it both go quiet |

## Variables

| Variable | Meaning |
|---|---|
| `DIGEST_TO` | Comma separated recipient list. Unset means Avi alone |

## Reports
"""


def row_for(w, base):
    return (f'| `{w["week"]}` | {w["date"]} | [open]({base}/{w["week"]}/) '
            f'| {w["stories"] or ""} | {w["repos"] or ""} '
            f'| {"🎧" if w["has_mp3"] else ""} | {"✅" if w["sent"] else ""} |')


def render_reports_table(weeks, base=BASE_URL):
    """The generated index, as a markdown table."""
    lines = [REPORTS_START,
             "<!-- Generated by scripts/build_index.py on every build. Do not edit by hand. -->",
             ""]
    if not weeks:
        lines.append("No reports yet.")
    else:
        lines.append(HEADER_ROW)
        lines.append(DIVIDER_ROW)
        for w in weeks:
            lines.append(row_for(w, base))
        lines.append("")
        lines.append(f"_{len(weeks)} reports._")
    lines.append(REPORTS_END)
    return "\n".join(lines)


def update_readme(path, weeks, base=BASE_URL):
    """Replace the generated block in README.md, leaving the prose alone.

    Markers rather than a full rewrite: the README carries hand-written
    documentation that must survive every build.
    """
    table = render_reports_table(weeks, base)
    try:
        with open(path, encoding="utf-8") as fh:
            text = fh.read()
    except OSError:
        text = README_SKELETON.format(base=base)
    if REPORTS_START in text and REPORTS_END in text:
        head = text[:text.index(REPORTS_START)]
        tail = text[text.index(REPORTS_END) + len(REPORTS_END):]
        text = head + table + tail
    else:
        text = text.rstrip("\n") + "\n\n" + table + "\n"
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)
    return path


def main():
    reports_dir = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_DIR
    weeks = collect(reports_dir)
    out = os.path.join(reports_dir, "index.html")
    with open(out, "w", encoding="utf-8") as f:
        f.write(render(weeks))
    print(f"wrote {out} ({len(weeks)} weeks)")

    readme = os.path.join(os.path.dirname(os.path.abspath(reports_dir)), "README.md")
    update_readme(readme, weeks)
    print(f"updated {readme}")


if __name__ == "__main__":
    main()
