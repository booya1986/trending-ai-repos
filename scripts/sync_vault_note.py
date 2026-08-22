#!/usr/bin/env python3
"""
Build the weekly AI News vault note, and install it into the Obsidian vault.

Split in two on purpose, because the vault is a local folder with no git
remote and the cloud cannot reach it:

  RENDER  runs in the Friday workflow (`--emit <path>`) and writes the note
          markdown into the repo as reports/<week>/vault-note.md. The note
          therefore EXISTS whether or not the Mac is on.
  INSTALL runs on the Mac (`--install-all`) and copies every rendered note the
          vault is missing. It walks all weeks, not just the newest: before
          this, a Mac that was off across a Friday lost that week's note
          permanently, because only the latest week was ever considered.

Primary source: the committed index.html, which contains the full bilingual
briefs (What it does / Why it's trending / Example / Why it matters, he+en).
Fallback: narration.txt (thin, Hebrew-only) if HTML parsing yields nothing.

Idempotent throughout: an existing vault note is never overwritten.

Usage:
  python3 sync_vault_note.py [week]              write one week into the vault
  python3 sync_vault_note.py <week> --emit PATH  render to PATH, no vault touch
  python3 sync_vault_note.py --install-all       backfill every missing week
"""
import datetime
import html as htmllib
import json
import os
import re
import sys

# Default to the LIVE clone, not the in-vault one. The copy under
# ~/Documents/avi-workspace/Researches/Trending Repos/trending-site is stale
# (it stopped being pulled) and pointing here at it meant any run without the
# TRENDING_SITE_CLONE override silently read months-old reports.
# Default to the repo this script lives in, which is correct on the Mac clone
# and on a CI runner alike. It used to default to the Mac path, so the Friday
# `--emit` step read a directory that does not exist on a runner, found no
# report, and refused to write the note.
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SITE_DIR = os.environ.get("TRENDING_SITE_CLONE", _REPO_ROOT)
VAULT_DIR = os.path.expanduser("~/Documents/avi-workspace/Researches/AI News")
NOTE_SUFFIX = "AI News"
INDEX_NOTE = "📌 AI News — מפת תוכן.md"
INDEX_START = "<!-- INDEX:START -->"
INDEX_END = "<!-- INDEX:END -->"
REPORTS_DIR = os.path.join(SITE_DIR, "reports")
BASE_URL = "https://booya1986.github.io/trending-ai-repos/reports"


def latest_week():
    weeks = sorted(
        d for d in os.listdir(REPORTS_DIR)
        if os.path.isdir(os.path.join(REPORTS_DIR, d)) and re.fullmatch(r"\d{4}-W\d+", d)
    )
    if not weeks:
        raise SystemExit("No report week folders found.")
    return weeks[-1]


def _unescape(s):
    # The HTML double-escapes some entities (e.g. &amp;#x27;); unescape twice.
    return htmllib.unescape(htmllib.unescape(s or "")).strip()


def parse_cards(week):
    """Extract rich per-repo data from index.html.

    Returns a list of dicts: {full_name, url, lang, stars, created, tags,
    sections: [(label_he, label_en, text_he, text_en), ...]}.
    Returns [] if the HTML cannot be parsed (caller falls back to narration).
    """
    path = os.path.join(REPORTS_DIR, week, "index.html")
    if not os.path.exists(path):
        return []
    html = open(path, encoding="utf-8").read()
    cards = []
    # Each repo is a block beginning at class="card"
    chunks = html.split('class="card"')[1:]
    for chunk in chunks:
        # Stop at the next card boundary (already split) — chunk is one card.
        m_url = re.search(r'href="(https://github\.com/[\w.\-/]+?)"', chunk)
        if not m_url:
            continue
        url = m_url.group(1).rstrip("/")
        full_name = url.split("github.com/", 1)[-1]
        lang = (re.search(r'card__eyebrow">([^<]+)<', chunk) or [None, ""])[1].strip()
        stars = (re.search(r'stars-num">([^<]+)<', chunk) or [None, ""])[1].strip()
        created = (re.search(r'meta-icon">[^<]*</span>\s*([0-9]{4}-[0-9]{2}-[0-9]{2})', chunk) or [None, ""])[1].strip()
        tags = re.findall(r'class="tag">([^<]+)<', chunk)
        # Brief sections: each has a label (he/en) then text (he/en).
        sections = []
        for sec in re.findall(
            r'brief-label"\s+data-he="(.*?)"\s+data-en="(.*?)">.*?'
            r'brief-text[^"]*"\s+data-he="(.*?)"\s+data-en="(.*?)">',
            chunk, re.DOTALL,
        ):
            label_he, label_en, text_he, text_en = (_unescape(x) for x in sec)
            sections.append((label_he, label_en, text_he, text_en))
        if sections:
            cards.append({
                "full_name": full_name, "url": url, "lang": lang,
                "stars": stars, "created": created, "tags": tags,
                "sections": sections,
            })
    return cards


def parse_narration(week):
    """Fallback: (name_he, desc_he) per repo from narration.txt."""
    path = os.path.join(REPORTS_DIR, week, "narration.txt")
    if not os.path.exists(path):
        return []
    repos = []
    for line in open(path, encoding="utf-8").read().splitlines():
        if line.startswith("מספר ") and ". " in line:
            parts = line.split(". ", 2)
            if len(parts) >= 3:
                repos.append((parts[1].strip(), parts[2].strip()))
    return repos


def read_news(week):
    """Stories written by summarize_news.py and committed next to the report.

    Missing or unreadable file returns [], so the vault note falls back to the
    repos-only shape it had before the news section existed.
    """
    path = os.path.join(REPORTS_DIR, week, "news.json")
    if not os.path.exists(path):
        return []
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh).get("stories") or []
    except (ValueError, OSError):
        return []


def build_news_section(stories):
    if not stories:
        return []
    lines = ["## 📰 10 הכתבות המובילות", ""]
    for s in stories:
        head_he = s.get("headline_he") or s.get("headline_en") or ""
        head_en = s.get("headline_en") or ""
        url = s.get("url", "")
        source = s.get("source", "")
        published = (s.get("published") or "")[:10]
        lines.append(f"### [{head_he}]({url})")
        meta = " · ".join(x for x in (source, published) if x)
        if meta:
            lines.append(f"_{meta}_")
        lines.append("")
        if s.get("summary_he"):
            lines.append(s["summary_he"])
            lines.append("")
        if s.get("insight_he"):
            lines.append(f"**מה לקחת מזה:** {s['insight_he']}")
            lines.append("")
        if head_en and head_en != head_he:
            lines.append(f"_{head_en}_")
            lines.append("")
    lines.append("---")
    lines.append("")
    return lines


def _header(week):
    week_num = week.split("W")[-1]
    report_url = f"{BASE_URL}/{week}/"
    mp3_url = f"{report_url}report.mp3"
    return [
        "---",
        f"created: {datetime.date.today().isoformat()}",
        f"week: {week}",
        "tags: [ai-news, gen-ai, llm, trending-repos, research]",
        "type: weekly-digest",
        "lang: bilingual",
        "---",
        "",
        f"# 📰 AI News — {week}",
        "",
        f"דוח שבועי: 10 הכתבות הגדולות ו-3 ה-repos החמים ב-Gen AI לשבוע {week_num}.",
        "",
        f"[📱 הדוח המלא]({report_url}) · [🎧 האזנה (עברית)]({mp3_url})",
        "",
        "---",
        "",
    ]


def build_note_rich(week, cards, stories=None):
    lines = _header(week) + build_news_section(stories or [])
    lines += ["## 📈 3 ה-repos המובילים", ""]
    for c in cards:
        tagstr = " ".join(f"`{t}`" for t in c["tags"][:6])
        lines.append(f'## [{c["full_name"]}]({c["url"]}) — `{c["lang"] or "Unknown"}`')
        if tagstr:
            lines.append(tagstr)
        stat_bits = []
        if c["stars"]:
            stat_bits.append(f'⭐ {c["stars"]}')
        if c["created"]:
            stat_bits.append(f'created {c["created"]}')
        if stat_bits:
            lines.append("")
            lines.append(f'**Stats:** {" · ".join(stat_bits)}')
        lines.append("")
        for label_he, label_en, text_he, text_en in c["sections"]:
            lines.append(f"**{label_en} / {label_he}:** {text_he}")
            lines.append("")
            lines.append(f"_{text_en}_")
            lines.append("")
        lines.append("---")
        lines.append("")
    return "\n".join(lines)


def build_note_fallback(week, repos_he, stories=None):
    lines = _header(week) + build_news_section(stories or [])
    lines += ["## 📈 3 ה-repos המובילים", ""]
    for name_he, desc_he in repos_he:
        lines.append(f"## {name_he}")
        lines.append("")
        lines.append(f"**מה זה עושה:** {desc_he}")
        lines.append("")
        lines.append("---")
        lines.append("")
    return "\n".join(lines)


def render_note(week):
    """The note markdown for a week, or None if the report is unparseable.

    Touches nothing outside the reports directory, so it is safe to run in CI
    where no vault exists.
    """
    stories = read_news(week)
    cards = parse_cards(week)
    if cards:
        print(f"Built RICH note from HTML: {len(cards)} repos.")
        return build_note_rich(week, cards, stories)
    repos_he = parse_narration(week)
    if not repos_he:
        print(f"No parseable HTML or narration for {week}.")
        return None
    print(f"HTML parse empty -> fell back to narration: {len(repos_he)} repos.")
    return build_note_fallback(week, repos_he, stories)


def all_weeks():
    return sorted(
        d for d in os.listdir(REPORTS_DIR)
        if os.path.isdir(os.path.join(REPORTS_DIR, d)) and re.fullmatch(r"\d{4}-W\d+", d)
    )


def note_path_for(week):
    return os.path.join(VAULT_DIR, f"{week} {NOTE_SUFFIX}.md")


def install_all():
    """Backfill every week the vault is missing a note for.

    Prefers the note rendered in the cloud (reports/<week>/vault-note.md) and
    falls back to rendering locally, so weeks published before the cloud
    render existed are still recoverable.
    """
    os.makedirs(VAULT_DIR, exist_ok=True)
    written = 0
    for week in all_weeks():
        target = note_path_for(week)
        if os.path.exists(target):
            continue
        cloud = os.path.join(REPORTS_DIR, week, "vault-note.md")
        if os.path.exists(cloud):
            content, origin = open(cloud, encoding="utf-8").read(), "cloud"
        else:
            content, origin = render_note(week), "rendered locally"
        if not content or not content.strip():
            print(f"{week}: nothing to write, skipping")
            continue
        open(target, "w", encoding="utf-8").write(content)
        print(f"{week}: note written ({origin}) -> {target}")
        written += 1
    print(f"install-all: {written} note(s) written, {len(all_weeks())} week(s) checked")
    update_index()
    return 0



def _friday_of(week):
    """The Friday a week's report was built, derived from the ISO week itself."""
    try:
        year, num = week.split("-W")
        return datetime.date.fromisocalendar(int(year), int(num), 5).isoformat()
    except ValueError:
        return ""


def installed_weeks():
    """Weeks that have both a published report and a note in the vault.

    Derived from the reports directory and probed one path at a time, NOT by
    listing the vault. Under launchd, macOS TCC lets this process stat a known
    file under ~/Documents but denies enumerating the directory, so os.listdir
    on the vault raises PermissionError while os.path.exists on a file inside
    it succeeds. Observed 2026-08-22.
    """
    return [w for w in reversed(all_weeks()) if os.path.exists(note_path_for(w))]


def render_index(weeks):
    """The generated table of contents for the vault folder.

    Written as plain markdown with wikilinks rather than a dataview query, so
    it reads correctly in preview, in source mode, and anywhere the note is
    exported or opened outside Obsidian.
    """
    lines = [INDEX_START,
             "<!-- נוצר אוטומטית על ידי sync_vault_note.py. אין לערוך ידנית. -->",
             ""]
    if not weeks:
        lines.append("_עוד אין דוחות._")
    else:
        lines.append("| שבוע | תאריך | פתק | דוח מלא |")
        lines.append("|---|---|---|---|")
        for week in weeks:
            lines.append(f"| `{week}` | {_friday_of(week)} "
                         f"| [[{week} {NOTE_SUFFIX}]] "
                         f"| [פתח]({BASE_URL}/{week}/) |")
        lines.append("")
        lines.append(f"_{len(weeks)} דוחות._")
    lines.append(INDEX_END)
    return "\n".join(lines)


def update_index():
    """Refresh the generated block inside the folder's index note.

    Marker scoped: the note carries hand-written documentation above the table
    which has to survive.
    """
    path = os.path.join(VAULT_DIR, INDEX_NOTE)
    table = render_index(installed_weeks())
    try:
        with open(path, encoding="utf-8") as fh:
            text = fh.read()
    except OSError:
        print(f"index note not found at {path}, skipping")
        return
    if INDEX_START in text and INDEX_END in text:
        text = (text[:text.index(INDEX_START)] + table
                + text[text.index(INDEX_END) + len(INDEX_END):])
    else:
        text = text.rstrip("\n") + "\n\n## 📝 כל הדוחות\n\n" + table + "\n"
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)
    print(f"index updated: {path}")


def main():
    args = sys.argv[1:]
    if "--install-all" in args:
        return install_all()

    emit = None
    if "--emit" in args:
        i = args.index("--emit")
        try:
            emit = args[i + 1]
        except IndexError:
            print("--emit needs a path", file=sys.stderr)
            return 2
        del args[i:i + 2]

    week = args[0] if args else latest_week()

    if emit:
        content = render_note(week)
        if not content or not content.strip():
            print(f"Refusing to emit an empty note for {week}.", file=sys.stderr)
            return 1
        os.makedirs(os.path.dirname(os.path.abspath(emit)) or ".", exist_ok=True)
        open(emit, "w", encoding="utf-8").write(content)
        print(f"Note rendered: {emit} ({len(content)} chars)")
        return 0

    target = note_path_for(week)
    if os.path.exists(target):
        print(f"Vault note already exists for {week}, skipping.")
        return 0
    content = render_note(week)
    if not content or not content.strip():
        print(f"No note content for {week}, skipping.")
        return 0
    os.makedirs(VAULT_DIR, exist_ok=True)
    open(target, "w", encoding="utf-8").write(content)
    print(f"Vault note written: {target}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
