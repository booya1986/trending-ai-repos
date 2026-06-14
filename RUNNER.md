# Weekly runner: Trending AI/LLM Repos

You are a scheduled agent. Generate this week's Trending AI/LLM Repos brief,
build the HTML report, generate the Hebrew audio narration, commit everything,
and push to GitHub so GitHub Pages serves the updated report.

## Steps

### 1. Fetch repos

```bash
TRENDING_NOTES_DIR="$PWD/reports" python3 scripts/fetch_trending_repos.py > /tmp/trending.json
```

Check the JSON: if `count` is 0 it is almost certainly a GitHub rate limit.
Do NOT proceed — output "Rate limit hit, aborting" and stop.

### 2. Compose rich briefs

For EACH repo in the JSON's `repos` array, write a grounded brief using its
`description` and `readme_excerpt`. Do not invent features.

For each repo build a `brief_html` string using this Python helper.
Write each section in **both Hebrew and English**. The Hebrew text is displayed
by default; English is shown when the user toggles the EN button.

```python
import sys; sys.path.insert(0, 'scripts')
from build_report import wrap_brief_section
import html as h

# Write Hebrew text for text_he and English text for text_en.
brief_html = (
    wrap_brief_section('What it does',         h.escape(what_it_does_he),    text_en=h.escape(what_it_does_en)) +
    wrap_brief_section("Why it's trending",    h.escape(why_trending_he),    text_en=h.escape(why_trending_en)) +
    wrap_brief_section('Example use case',     h.escape(example_he),         text_en=h.escape(example_en)) +
    wrap_brief_section('Why it matters for you', h.escape(matters_he),       text_en=h.escape(matters_en), is_matters=True)
)
```

For each section write:
- `*_he`: Hebrew translation of the section text
- `*_en`: the English original

Also write a `narration` string (plain text Hebrew, ~2 sentences) for audio:
what the repo does and why it matters to Avi. Write the narration in Hebrew.

Inject both fields into each repo object, then save:

```python
import json
data = json.load(open('/tmp/trending.json'))
# enrich each repo with brief_html and narration
json.dump(data, open('/tmp/trending_rich.json', 'w'), ensure_ascii=False, indent=2)
```

### 3. Build HTML report

```bash
week=$(python3 -c "import json; print(json.load(open('/tmp/trending_rich.json'))['week'])")
outdir="reports/$week"
mkdir -p "$outdir"
python3 scripts/build_report.py --in /tmp/trending_rich.json --outdir "$outdir"
```

### 4. Generate MP3 narration via TTS proxy

Write this Python script to `/tmp/tts.py`, then run it:

```python
# /tmp/tts.py
import json, sys, urllib.request

outdir = sys.argv[1]
warn_file = f"{outdir}/warnings.txt"
mp3_path = f"{outdir}/report.mp3"

if __import__('os').path.exists(mp3_path):
    print(f"MP3 already exists, skipping")
    sys.exit(0)

text = open(f"{outdir}/narration.txt", encoding="utf-8").read()

# Call the TTS proxy (routes through Google Cloud to avoid ElevenLabs IP block)
PROXY_URL = "https://us-central1-learnwithavi-youtube.cloudfunctions.net/tts-proxy"
PROXY_TOKEN = "tts-proxy-trending-repos-2026"

payload = json.dumps({
    "text": text,
    "voice_id": "TX3LPaxmHKxFdv7VOQHJ",
    "model_id": "eleven_v3"
}).encode("utf-8")

req = urllib.request.Request(
    PROXY_URL,
    data=payload,
    headers={
        "Content-Type": "application/json",
        "X-Proxy-Token": PROXY_TOKEN
    }
)
try:
    with urllib.request.urlopen(req, timeout=180) as resp:
        audio = resp.read()
    with open(mp3_path, "wb") as f:
        f.write(audio)
    print(f"MP3 written: {len(audio)} bytes -> {mp3_path}")
except Exception as e:
    with open(warn_file, "a") as wf:
        wf.write(f"MP3 not generated: {e}\n")
    print(f"TTS failed: {e}", file=sys.stderr)
```

Run it:

```bash
python3 /tmp/tts.py "$outdir"
```

### 5. Write the Obsidian note

```bash
python3 - <<'PYEOF'
import json
data = json.load(open('/tmp/trending_rich.json'))
week = data['week']
note_path = f'reports/{week} Trending AI Repos.md'
lines = ['---', f'created: {data["generated_for"]}', f'week: {week}',
         'tags: [trending-repos, ai, llm, research]', 'type: weekly-digest', '---', '',
         f'# Trending AI/LLM Repos: {week}', '',
         f'Full report: https://booya1986.github.io/trending-ai-repos/reports/{week}/', '']
for r in data['repos']:
    lines.append(f'## [{r["full_name"]}]({r["url"]})')
    lines.append(f'Stars: {r["stars"]:,} | {r["language"]} | {r["created_at"]}')
    lines.append(r['description'])
    lines.append('')
open(note_path, 'w', encoding='utf-8').write('\n'.join(lines))
print('note written:', note_path)
PYEOF
```

### 6. Commit and push

```bash
git config user.email "avi.j.levi@gmail.com"
git config user.name "Avi Levi"
git add "reports/$week/" 2>/dev/null || true
git add "reports/$week Trending AI Repos.md" 2>/dev/null || true
git add reports/ 2>/dev/null || true
git commit -m "Add trending AI repos report for $week"
git push
```

### 7. Create the digest email draft (Gmail)

Build the digest HTML and create a Gmail draft so the email is ready to send.
A local Sunday job also auto-sends; the shared `reports/$week/.email_sent` marker
(committed in step 6 if present) prevents duplicates. Only create the draft if
that marker does NOT already exist.

First build the HTML to `/tmp/digest.html` using the committed builder so cloud
and local produce identical emails:

```bash
if [ -f "reports/$week/.email_sent" ]; then
  echo "Email already sent for $week, skipping draft."
else
  python3 - "$week" <<'PYEOF' > /tmp/digest_subject.txt
import sys, os
sys.path.insert(0, 'scripts')
import send_digest as sd
week = sys.argv[1]
top = sd.read_narration(week)
html = sd.build_html(week, top)
open('/tmp/digest.html', 'w', encoding='utf-8').write(html)
print(week.replace('-', ' ').replace('W', 'שבוע '))
PYEOF
  echo "Digest HTML built for $week"
fi
```

Then, if `/tmp/digest.html` was written (marker absent), create the Gmail draft
using the **Gmail MCP `create_draft` tool** (not a shell command). Read
`/tmp/digest.html` for `htmlBody` and `/tmp/digest_subject.txt` for the week label:

- `to`: `["avi.j.levi@gmail.com"]`
- `subject`: `🔥 דוח AI שבועי מוכן – <week label from /tmp/digest_subject.txt>`
- `body`: `10 repos AI מובילים השבוע. פתח לקרוא ולהאזין.`
- `htmlBody`: the full contents of `/tmp/digest.html`

If the marker already existed, skip the draft entirely.

### 8. Report back

Output:
- Week and report URL: `https://booya1986.github.io/trending-ai-repos/reports/<week>/`
- Repo count and whether MP3 was generated
- Whether a Gmail draft was created (or skipped because already sent)
- Any warnings from warnings.txt

## Rules
- No em-dashes anywhere in briefs. Use colons, commas, or parentheses.
- Ground every claim in the README/metadata. Never invent capabilities.
- Only touch the `reports/` folder. Never modify scripts or RUNNER.md.
- If count=0: abort, do not commit.
- The email/draft step may read `scripts/send_digest.py` as a library (import only); do not modify it.
