#!/usr/bin/env python3
"""Generate MP3 narration for a report week via the ElevenLabs TTS proxy.

Usage: python3 scripts/generate_tts.py <outdir>
Exit 0 always (TTS is non-fatal; errors are appended to warnings.txt).
"""
import hashlib
import json
import os
import sys
import urllib.request

PROXY_URL = "https://us-central1-learnwithavi-youtube.cloudfunctions.net/tts-proxy"
PROXY_TOKEN = "tts-proxy-trending-repos-2026"


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

    payload = json.dumps({
        "text": text,
        "voice_id": "TX3LPaxmHKxFdv7VOQHJ",
        "model_id": "eleven_v3",
    }).encode("utf-8")

    req = urllib.request.Request(
        PROXY_URL,
        data=payload,
        headers={"Content-Type": "application/json", "X-Proxy-Token": PROXY_TOKEN},
    )
    try:
        with urllib.request.urlopen(req, timeout=480) as resp:
            audio = resp.read()
        if len(audio) > 10000:
            open(mp3_path, "wb").write(audio)
            open(hash_path, "w").write(narr_hash)
            print(f"MP3 written: {len(audio):,} bytes -> {mp3_path}")
        else:
            msg = f"TTS response too small ({len(audio)} bytes) — skipping"
            print(msg)
            with open(warn_path, "a") as wf:
                wf.write(msg + "\n")
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
