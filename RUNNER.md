# Weekly runner: Trending AI/LLM Repos

You are a scheduled agent. Generate this week's Trending AI/LLM Repos brief,
build the HTML report, generate the audio narration script, commit everything,
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
# ... enrich each repo ...
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

First, diagnose what env vars are available:

```bash
echo "ELEVENLABS_API_KEY set: ${ELEVENLABS_API_KEY:+yes}"
echo "key length: ${#ELEVENLABS_API_KEY}"
```

Then generate the MP3 using curl (more reliable than Python urllib for binary downloads):

```bash
text=$(cat "$outdir/narration.txt")
curl -s -X POST "https://api.elevenlabs.io/v1/text-to-speech/JBFqnCBsd6RMkjVDRZzb" \
  -H "xi-api-key: ${ELEVENLABS_API_KEY}" \
  -H "Content-Type: application/json" \
  -H "Accept: audio/mpeg" \
  --data "{\"text\": $(python3 -c \"import json,sys; print(json.dumps(sys.stdin.read()))\") , \"model_id\": \"eleven_turbo_v2_5\", \"voice_settings\": {\"stability\": 0.5, \"similarity_boost\": 0.75}}" \
  -o "$outdir/report.mp3" \
  -w "%{http_code}" || echo "curl failed"
```

Actually, because the narration text is long with special characters, use Python to build the payload and pipe to curl:

```bash
python3 -c "
import json, subprocess, os, sys
text = open('$outdir/narration.txt', encoding='utf-8').read()
api_key = os.environ.get('ELEVENLABS_API_KEY', '')
print('API key present:', bool(api_key), 'length:', len(api_key), file=sys.stderr)
if not api_key:
    open('$outdir/warnings.txt', 'a').write('MP3 skipped: ELEVENLABS_API_KEY not set\n')
    sys.exit(0)
payload = json.dumps({
    'text': text,
    'model_id': 'eleven_turbo_v2_5',
    'voice_settings': {'stability': 0.5, 'similarity_boost': 0.75}
}).encode('utf-8')
import urllib.request
req = urllib.request.Request(
    'https://api.elevenlabs.io/v1/text-to-speech/JBFqnCBsd6RMkjVDRZzb',
    data=payload,
    headers={'xi-api-key': api_key, 'Content-Type': 'application/json', 'Accept': 'audio/mpeg'}
)
try:
    with urllib.request.urlopen(req, timeout=120) as resp:
        data = resp.read()
        open('$outdir/report.mp3', 'wb').write(data)
        print(f'MP3 written: {len(data)} bytes')
except Exception as e:
    open('$outdir/warnings.txt', 'a').write(f'MP3 not generated: {e}\n')
    print('TTS failed:', e, file=sys.stderr)
"
```

### 5. Write the Obsidian note

```bash
python3 -c "
import json
data = json.load(open('/tmp/trending_rich.json'))
week = data['week']
note_path = f'reports/{week} Trending AI Repos.md'
lines = ['---', f'created: {data[\"generated_for\"]}', f'week: {week}',
         'tags: [trending-repos, ai, llm, research]', 'type: weekly-digest', '---', '',
         f'# Trending AI/LLM Repos: {week}', '',
         f'Full report: https://booya1986.github.io/trending-ai-repos/reports/{week}/', '']
for r in data['repos']:
    lines.append(f'## [{r[\"full_name\"]}]({r[\"url\"]})')
    lines.append(f'Stars: {r[\"stars\"]:,} | {r[\"language\"]} | {r[\"created_at\"]}')
    lines.append(r['description'])
    lines.append('')
open(note_path, 'w', encoding='utf-8').write('\n'.join(lines))
print('note written:', note_path)
"
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

Output the week, report URL, repo count, MP3 status, and any warnings.
URL: https://booya1986.github.io/trending-ai-repos/reports/<week>/

## Rules
- No em-dashes. Only touch reports/. Abort if count=0.
