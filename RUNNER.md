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

For each repo build a `brief_html` string using this Python helper:

```python
import sys; sys.path.insert(0, 'scripts')
from build_report import wrap_brief_section
import html as h

brief_html = (
    wrap_brief_section('What it does', h.escape(what_it_does)) +
    wrap_brief_section("Why it's trending", h.escape(why_trending)) +
    wrap_brief_section('Example use case', h.escape(example)) +
    wrap_brief_section('Why it matters for you', h.escape(matters), is_matters=True)
)
```

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

### 4. Generate MP3 narration via ElevenLabs TTS

Write this Python script to `/tmp/tts.py`, then run it:

```python
# /tmp/tts.py
import json, os, sys, urllib.request

outdir = sys.argv[1]
warn_file = f"{outdir}/warnings.txt"
mp3_path = f"{outdir}/report.mp3"

# Skip if MP3 already exists (may have been generated locally and committed)
if os.path.exists(mp3_path):
    print(f"MP3 already exists at {mp3_path}, skipping TTS")
    sys.exit(0)

text = open(f"{outdir}/narration.txt", encoding="utf-8").read()
api_key = os.environ.get("ELEVENLABS_API_KEY", "").strip()

if not api_key:
    with open(warn_file, "a") as wf:
        wf.write("MP3 skipped: ELEVENLABS_API_KEY not set\n")
    print("MP3 skipped: no API key")
    sys.exit(0)

voice_id = "a1Vx4kQ93YUGGWHKxt55"  # Efi Ariely 4 — native Hebrew voice
payload = json.dumps({
    "text": text,
    "model_id": "eleven_multilingual_v2",
    "voice_settings": {"stability": 0.5, "similarity_boost": 0.75}
}).encode("utf-8")

req = urllib.request.Request(
    f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}",
    data=payload,
    headers={
        "xi-api-key": api_key,
        "Content-Type": "application/json",
        "Accept": "audio/mpeg"
    }
)
try:
    with urllib.request.urlopen(req, timeout=120) as resp:
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

### 7. Report back

Output:
- Week and report URL: `https://booya1986.github.io/trending-ai-repos/reports/<week>/`
- Repo count and whether MP3 was generated
- Any warnings from warnings.txt

## Rules
- No em-dashes anywhere in briefs. Use colons, commas, or parentheses.
- Ground every claim in the README/metadata. Never invent capabilities.
- Only touch the `reports/` folder. Never modify scripts or RUNNER.md.
- If count=0: abort, do not commit.
