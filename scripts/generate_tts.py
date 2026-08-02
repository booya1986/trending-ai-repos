#!/usr/bin/env python3
"""Generate MP3 narration for a report week via Google Cloud Text-to-Speech.

Usage: python3 scripts/generate_tts.py <outdir>
Exit 0 always (TTS is non-fatal; errors are appended to warnings.txt).

Replaces the ElevenLabs proxy, whose free tier (10k credits/month at 1
credit/char for eleven_v3) was exhausted mid-month by a one-off long
narration, leaving the weekly digest with no audio. Chirp3-HD gives 1M
characters/month free; a weekly narration is ~2.1k characters, so this is
~0.2% of the quota.

Auth, first match wins:
  GOOGLE_TTS_API_KEY  - an API key, sent as ?key=... (this is what CI uses)
  gcloud              - an access token from the local SDK (local dev)
"""
import base64
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import urllib.request

API = "https://texttospeech.googleapis.com/v1/text:synthesize"
PROJECT = "learnwithavi-youtube"
VOICE = "he-IL-Chirp3-HD-Aoede"
LANG = "he-IL"

# The API caps a single request's input at 5000 bytes. Hebrew is ~2 bytes per
# character in UTF-8, so stay well under it and split on line boundaries, which
# in narration.txt fall between repos and therefore land on natural pauses.
MAX_CHUNK_BYTES = 3500


def _auth():
    """Return (params, headers) for whichever credential is available."""
    key = os.environ.get("GOOGLE_TTS_API_KEY", "").strip()
    if key:
        return f"?key={key}", {"Content-Type": "application/json"}
    token = subprocess.run(
        ["gcloud", "auth", "print-access-token"],
        capture_output=True, text=True,
    ).stdout.strip()
    if not token:
        raise RuntimeError(
            "no credential: set GOOGLE_TTS_API_KEY or run `gcloud auth login`"
        )
    return "", {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {token}",
        "x-goog-user-project": PROJECT,
    }


def chunk_text(text, limit=MAX_CHUNK_BYTES):
    """Split into request-sized pieces on line boundaries, never mid-line."""
    chunks, cur = [], ""
    for line in text.splitlines(keepends=True):
        # A single line over the limit can't be split safely on a boundary, so
        # send it alone and let the API reject it loudly rather than silently
        # truncating the narration.
        if len((cur + line).encode("utf-8")) > limit and cur:
            chunks.append(cur)
            cur = line
        else:
            cur += line
    if cur.strip():
        chunks.append(cur)
    return chunks


def synthesize(text, params, headers):
    body = json.dumps({
        "input": {"text": text},
        "voice": {"languageCode": LANG, "name": VOICE},
        "audioConfig": {"audioEncoding": "MP3"},
    }).encode("utf-8")
    req = urllib.request.Request(API + params, data=body, headers=headers)
    with urllib.request.urlopen(req, timeout=300) as resp:
        return base64.b64decode(json.loads(resp.read())["audioContent"])


def _concat(parts, dest):
    """Join MP3 parts. Prefer ffmpeg; fall back to byte concat."""
    if len(parts) == 1:
        open(dest, "wb").write(parts[0])
        return
    tmp = tempfile.mkdtemp()
    try:
        paths = []
        for i, part in enumerate(parts):
            p = os.path.join(tmp, f"{i:03d}.mp3")
            open(p, "wb").write(part)
            paths.append(p)
        if shutil.which("ffmpeg"):
            listing = os.path.join(tmp, "list.txt")
            with open(listing, "w") as fh:
                for p in paths:
                    fh.write(f"file '{p}'\n")
            subprocess.run(
                ["ffmpeg", "-y", "-loglevel", "error", "-f", "concat",
                 "-safe", "0", "-i", listing, "-c", "copy", dest],
                check=True,
            )
        else:
            # Frame-aligned MP3s concatenate acceptably without a remux.
            with open(dest, "wb") as out:
                for part in parts:
                    out.write(part)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def generate(outdir):
    narr_path = os.path.join(outdir, "narration.txt")
    mp3_path = os.path.join(outdir, "report.mp3")
    hash_path = os.path.join(outdir, ".narration.sha")
    warn_path = os.path.join(outdir, "warnings.txt")

    if not os.path.exists(narr_path):
        print("no narration.txt — skipping TTS")
        return

    text = open(narr_path, encoding="utf-8").read()
    narr_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()

    if os.path.exists(mp3_path) and os.path.getsize(mp3_path) > 10000:
        prev = open(hash_path).read().strip() if os.path.exists(hash_path) else ""
        if prev == narr_hash:
            print("MP3 up to date — skipping")
            return
        print("narration changed — regenerating MP3")

    try:
        params, headers = _auth()
        chunks = chunk_text(text)
        print(f"synthesizing {len(text)} chars as {len(chunks)} chunk(s) "
              f"with {VOICE}")
        parts = [synthesize(c, params, headers) for c in chunks]
        _concat(parts, mp3_path)

        size = os.path.getsize(mp3_path)
        if size > 10000:
            open(hash_path, "w").write(narr_hash)
            print(f"MP3 written: {size:,} bytes -> {mp3_path}")
        else:
            # Don't leave a truncated file behind pretending to be audio.
            os.remove(mp3_path)
            raise RuntimeError(f"MP3 too small ({size} bytes)")
    except Exception as e:
        msg = f"TTS failed: {e}"
        print(msg, file=sys.stderr)
        with open(warn_path, "a") as wf:
            wf.write(msg + "\n")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: generate_tts.py <outdir>", file=sys.stderr)
        sys.exit(1)
    generate(sys.argv[1])
