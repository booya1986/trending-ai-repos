#!/usr/bin/env python3
"""
Send weekly AI repos digest email via Gmail API (gws auth).
Usage: python3 scripts/send_digest.py [week]
If week not provided, uses the latest report folder.
"""
import base64, json, os, subprocess, sys
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

REPORTS_DIR = os.path.join(os.path.dirname(__file__), '..', 'reports')
TO = 'avi.j.levi@gmail.com'
BASE_URL = 'https://booya1986.github.io/trending-ai-repos/reports'


def latest_week():
    weeks = sorted(
        d for d in os.listdir(REPORTS_DIR)
        if os.path.isdir(os.path.join(REPORTS_DIR, d)) and d[:4].isdigit()
    )
    if not weeks:
        raise RuntimeError("No report folders found in reports/")
    return weeks[-1]


def read_narration(week):
    path = os.path.join(REPORTS_DIR, week, 'narration.txt')
    if not os.path.exists(path):
        return []
    lines = open(path, encoding='utf-8').read().splitlines()
    repos = []
    for line in lines:
        if line.startswith('מספר ') and '. ' in line:
            # e.g. "מספר 1. RepoName. Description..."
            parts = line.split('. ', 2)
            if len(parts) >= 3:
                name = parts[1].strip()
                desc = parts[2].split('.')[0].strip()
                repos.append((name, desc))
    return repos[:4]


def build_html(week, top_repos):
    report_url = f"{BASE_URL}/{week}/"
    mp3_url = f"{BASE_URL}/{week}/report.mp3"
    rows = ''
    for i, (name, desc) in enumerate(top_repos):
        border = 'border-bottom:1px solid #2d2d2d;' if i < len(top_repos) - 1 else ''
        rows += f'''<tr><td style="padding:6px 0;{border}">
        <p style="margin:0;font-size:14px;color:#f3f4f6;font-family:Arial,sans-serif;">
          <strong style="color:#22c55e;">{name}</strong> &mdash; {desc}
        </p></td></tr>'''

    week_label = week.replace('-', ' ').replace('W', 'שבוע ')
    return f"""<!DOCTYPE html><html lang="he" dir="rtl">
<head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1"></head>
<body style="margin:0;padding:0;background:#f0f0f0;font-family:Arial,sans-serif;direction:rtl;">
<table width="100%" cellpadding="0" cellspacing="0" bgcolor="#f0f0f0" style="padding:24px 12px;">
<tr><td align="center">
<table width="100%" style="max-width:520px;" cellpadding="0" cellspacing="0">
  <tr><td style="background:#1b1b1b;border-radius:12px 12px 0 0;padding:24px 28px 18px;border-bottom:3px solid #22c55e;">
    <p style="margin:0 0 6px;font-size:11px;color:#22c55e;letter-spacing:2px;font-family:Arial,sans-serif;">WEEKLY AI DIGEST</p>
    <h1 style="margin:0 0 4px;font-size:22px;color:#f9fafb;font-family:Arial,sans-serif;">&#x1F525; טרנדים ב-AI/LLM</h1>
    <p style="margin:0;font-size:13px;color:#9ca3af;font-family:Arial,sans-serif;">{week_label} &middot; 10 repos נבחרים לפי הפרופיל שלך</p>
  </td></tr>
  <tr><td style="background:#1b1b1b;padding:20px 28px 16px;">
    <p style="margin:0 0 14px;font-size:11px;font-weight:bold;color:#22c55e;letter-spacing:2px;font-family:Arial,sans-serif;">TL;DR — ההיילייטס של השבוע</p>
    <table width="100%" cellpadding="0" cellspacing="0">{rows}</table>
    <p style="margin:12px 0 0;font-size:12px;color:#6b7280;font-family:Arial,sans-serif;">+ repos נוספים בדוח המלא</p>
  </td></tr>
  <tr><td style="background:#1b1b1b;padding:20px 28px 10px;">
    <a href="{report_url}" style="display:block;background:#22c55e;color:#0a1a0f;padding:16px 0;border-radius:50px;text-decoration:none;font-weight:bold;font-size:16px;text-align:center;font-family:Arial,sans-serif;">&#x1F4F1; קרא את הדוח המלא</a>
  </td></tr>
  <tr><td style="background:#1b1b1b;padding:8px 28px 24px;">
    <a href="{mp3_url}" style="display:block;background:#1b1b1b;color:#22c55e;border:2px solid #22c55e;padding:14px 0;border-radius:50px;text-decoration:none;font-weight:bold;font-size:15px;text-align:center;font-family:Arial,sans-serif;">&#x1F3A7; האזן בדרך לעבודה (עברית)</a>
  </td></tr>
  <tr><td style="background:#111111;border-radius:0 0 12px 12px;padding:14px 28px;border-top:1px solid #2d2d2d;">
    <p style="margin:0;font-size:11px;color:#6b7280;text-align:center;font-family:Arial,sans-serif;">נשלח אוטומטית כל יום ראשון &middot; <a href="{BASE_URL}/" style="color:#4b5563;text-decoration:none;">כל הדוחות</a></p>
  </td></tr>
</table>
</td></tr>
</table>
</body></html>"""


def send(week):
    top_repos = read_narration(week)
    html = build_html(week, top_repos)
    week_label = week.replace('-', ' ').replace('W', 'שבוע ')

    msg = MIMEMultipart('alternative')
    msg['To'] = TO
    msg['From'] = TO
    msg['Subject'] = f'🔥 דוח AI שבועי מוכן – {week_label}'
    msg.attach(MIMEText('10 repos AI מובילים השבוע. פתח לקרוא ולהאזין.', 'plain', 'utf-8'))
    msg.attach(MIMEText(html, 'html', 'utf-8'))

    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
    body = json.dumps({"raw": raw})

    result = subprocess.run(
        ['gws', 'gmail', 'users', 'messages', 'send',
         '--params', '{"userId": "me"}',
         '--json', body],
        capture_output=True, text=True
    )
    if result.returncode != 0:
        print(f"Send failed: {result.stderr}", file=sys.stderr)
        sys.exit(1)

    # strip "Using keyring backend: keyring" prefix line before parsing JSON
    stdout = '\n'.join(l for l in result.stdout.splitlines() if not l.startswith('Using keyring'))
    resp = json.loads(stdout) if stdout.strip() else {}
    print(f"Email sent! Message ID: {resp.get('id')} | Week: {week}")
    return resp


if __name__ == '__main__':
    week = sys.argv[1] if len(sys.argv) > 1 else latest_week()
    send(week)
