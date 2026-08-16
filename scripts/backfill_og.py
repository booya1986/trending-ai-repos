#!/usr/bin/env python3
"""Add social-share tags and a card to an already-published report.

Usage: backfill_og.py <reports/WEEK-dir>

Reports built before social sharing existed have no og:/twitter: tags and no
og.png, so sharing one to WhatsApp still previews as a bare grey box. New
reports get both from build_report.py and build_og_image.py; this brings an
old one up to the same standard without rebuilding it from source data (which
is no longer available for past weeks).

Idempotent: a report that already has the tags is left alone.
"""
import html
import os
import re
import sys

SITE_BASE = "https://booya1986.github.io/trending-ai-repos/reports"


def build_meta(week, lead):
    page_url = f"{SITE_BASE}/{week}/"
    og_image = f"{SITE_BASE}/{week}/og.png"
    og_title = f"טרנדים ב-AI/LLM — {week}"
    og_desc = "ה-repos והחדשות הבולטים בעולם ה-AI השבוע"
    if lead:
        og_desc += f". פותח ב: {lead}"
    og_desc = html.escape(og_desc[:200])
    t = html.escape(og_title)
    return f"""<meta name="description" content="{og_desc}">
<link rel="canonical" href="{page_url}">

<meta property="og:type" content="article">
<meta property="og:site_name" content="טרנדים ב-AI/LLM">
<meta property="og:locale" content="he_IL">
<meta property="og:url" content="{page_url}">
<meta property="og:title" content="{t}">
<meta property="og:description" content="{og_desc}">
<meta property="og:image" content="{og_image}">
<meta property="og:image:secure_url" content="{og_image}">
<meta property="og:image:type" content="image/png">
<meta property="og:image:width" content="1200">
<meta property="og:image:height" content="630">
<meta property="og:image:alt" content="{t}">

<meta name="twitter:card" content="summary_large_image">
<meta name="twitter:title" content="{t}">
<meta name="twitter:description" content="{og_desc}">
<meta name="twitter:image" content="{og_image}">
"""


def main():
    if len(sys.argv) < 2:
        print("usage: backfill_og.py <reports/WEEK-dir>", file=sys.stderr)
        sys.exit(2)
    outdir = sys.argv[1].rstrip("/")
    week = os.path.basename(outdir)
    page = os.path.join(outdir, "index.html")

    if not os.path.exists(page):
        print(f"no index.html in {outdir}", file=sys.stderr)
        sys.exit(1)

    src = open(page, encoding="utf-8").read()
    if "og:image" in src:
        print(f"{week} already has social tags; nothing to do")
        return

    names = re.findall(r'href="https://github\.com/([\w.\-]+/[\w.\-]+)"', src)
    lead = names[0] if names else ""

    # Insert right after the <title>, matching where build_report.py puts them.
    m = re.search(r"(</title>\n)", src)
    if not m:
        print("could not find </title>", file=sys.stderr)
        sys.exit(1)
    src = src[:m.end()] + build_meta(week, lead) + src[m.end():]
    open(page, "w", encoding="utf-8").write(src)
    print(f"added social tags to {page}")


if __name__ == "__main__":
    main()
