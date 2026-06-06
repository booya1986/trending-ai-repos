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

Where:
- **What it does**: 2-3 sentences, grounded in README excerpt only.
- **Why it's trending**: who it's for and why now.
- **Example use case**: concrete scenario tied to Avi's AI / L&D / content / Poalim / Obsidian / teaching work where it fits; otherwise a neutral specific example.
- **Why it matters for you**: one short tie-in line to Avi's work.

Also write a `narration` string (plain text, ~2 sentences) for audio: what the repo does and why it matters to Avi.

Inject both fields into each repo object in the JSON, then save:

```python
import json
data = json.load(open('/tmp/trending.json'))
# ... enrich each repo with brief_html and narration ...
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

Read the narration.txt and call the ElevenLabs TTS API (voice: Adam, model: eleven_multilingual_v2):

```python
import os, urllib.request, json

text = open(f"{outdir}/narration.txt", encoding="utf-8").read()
api_key = os.environ.get("ELEVENLABS_API_KEY", "")
if not api_key:
    open(f"{outdir}/warnings.txt", "a").write("MP3 skipped: ELEVENLABS_API_KEY not set\n")
else:
    voice_id = "pNInz6obpgDQGcFmaJgB"  # Adam — clear, natural English
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
            with open(f"{outdir}/report.mp3", "wb") as f:
                f.write(resp.read())
        print(f"MP3 written to {outdir}/report.mp3")
    except Exception as e:
        open(f"{outdir}/warnings.txt", "a").write(f"MP3 not generated: {e}\n")
        print(f"TTS failed (skipping): {e}")
```

If the call fails — skip the MP3, note it in warnings.txt, continue with commit.

### 5. Write the Obsidian note

Also write the markdown brief to the notes folder for the vault:

```bash
TRENDING_NOTES_DIR="$PWD/reports" python3 -c "
import json, os
data = json.load(open('/tmp/trending_rich.json'))
week = data['week']
note_path = f'reports/{week} Trending AI Repos.md'
# Build markdown from brief_html would be messy; write a minimal md instead
lines = [f'---', f'created: {data[\"generated_for\"]}', f'week: {week}',
         'tags: [trending-repos, ai, llm, research]', 'type: weekly-digest', '---', '',
         f'# 🔥 Trending AI/LLM Repos: {week}', '',
         f'Full report: https://booya1986.github.io/trending-ai-repos/reports/{week}/', '']
for r in data['repos']:
    lines.append(f'## [{r[\"full_name\"]}]({r[\"url\"]})')
    lines.append(f'⭐ {r[\"stars\"]:,} · {r[\"language\"]} · {r[\"created_at\"]}')
    lines.append(f'{r[\"description\"]}')
    lines.append('')
open(note_path, 'w', encoding='utf-8').write('\n'.join(lines))
print('note written:', note_path)
"
```

### 6. Commit and push

```bash
git config user.email "avi.j.levi@gmail.com"
git config user.name "Avi Levi"
git add "reports/$week/" "reports/$week Trending AI Repos.md" 2>/dev/null || true
git add reports/ 2>/dev/null || true
git commit -m "Add trending AI repos report for $week"
git push
```

### 7. Report back

Output:
- Week, report URL: `https://booya1986.github.io/trending-ai-repos/reports/<week>/`
- Repo count, any warnings
- Whether MP3 was generated

## Style rules (hard)
- No em-dashes anywhere in briefs. Use colons, commas, or parentheses.
- Ground every claim in the README/metadata. Never invent capabilities.
- Only touch the `reports/` folder. Never modify scripts or RUNNER.md.
- If count=0: abort, do not commit.
