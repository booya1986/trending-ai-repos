#!/usr/bin/env python3
"""
Send the weekly AI repos digest via Gmail SMTP using an App Password.

This is the CLOUD send path (GitHub Actions), independent of the Mac. It reuses
send_digest.py's HTML builder so the email is byte-identical to the local path.

Auth: env GMAIL_APP_PASSWORD (a 16-char Gmail App Password). Sender/recipient
default to avi.j.levi@gmail.com (override with GMAIL_USER / DIGEST_TO).

Dedup: writes reports/<week>/.email_sent after a successful send; the caller
(workflow) commits+pushes it so the local Sunday job sees it and never double-sends.

Catch-up: DIGEST_CATCHUP_TO sends the week to those addresses alone, so someone
who joins after Sunday's send gets it without everyone else getting it twice. It
ignores the marker and never writes one.

Usage: python3 scripts/send_digest_smtp.py [week]
Exit 0 = sent or already-sent; exit 1 = real failure; exit 2 = no credential.
"""
import os
import smtplib
import sys
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

# Reuse the exact HTML/TLDR logic from the local sender.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import send_digest as sd  # noqa: E402

GMAIL_USER = os.environ.get("GMAIL_USER", "avi.j.levi@gmail.com")


def parse_addresses(value):
    return [a.strip() for a in (value or "").split(",") if a.strip()]


# Comma separated, so the audience is a repo secret rather than a code change.
# A secret and not a variable because this repo is public: a variable's value is
# printed in the public Actions log. `or` not a get() default: an unset secret
# arrives as an empty string.
RECIPIENTS = parse_addresses(os.environ.get("DIGEST_TO") or "avi.j.levi@gmail.com")
CATCHUP_RECIPIENTS = parse_addresses(os.environ.get("DIGEST_CATCHUP_TO"))
APP_PASSWORD = os.environ.get("GMAIL_APP_PASSWORD", "").replace(" ", "")


def send(week, recipients):
    top = sd.read_narration(week)
    html = sd.build_html(week, top)
    week_label = week.replace("-", " ").replace("W", "שבוע ")

    msg = MIMEMultipart("alternative")
    # Recipients go on the envelope only, so they don't see each other's address.
    msg["To"] = GMAIL_USER
    msg["From"] = GMAIL_USER
    msg["Subject"] = f"📰 AI News שבועי – {week_label}"
    msg.attach(MIMEText(
        "10 הכתבות הגדולות ב-Gen AI השבוע, ו-3 הרפוזיטוריות שצוברות הכי הרבה "
        "תאוצה. פתח לקרוא ולהאזין.", "plain", "utf-8"))
    msg.attach(MIMEText(html, "html", "utf-8"))

    with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=60) as s:
        s.login(GMAIL_USER, APP_PASSWORD)
        s.sendmail(GMAIL_USER, recipients, msg.as_string())
    # A count, not the addresses: this log is public.
    print(f"Email sent via SMTP to {len(recipients)} recipient(s) | Week: {week}")


def main():
    week = sys.argv[1] if len(sys.argv) > 1 else sd.latest_week()
    if CATCHUP_RECIPIENTS:
        if not APP_PASSWORD:
            print("GMAIL_APP_PASSWORD not set — cannot send.", file=sys.stderr)
            return 2
        try:
            send(week, CATCHUP_RECIPIENTS)
        except Exception as e:
            print(f"SMTP send failed: {e}", file=sys.stderr)
            return 1
        return 0
    sent_marker = os.path.join(sd.REPORTS_DIR, week, ".email_sent")
    if os.path.exists(sent_marker):
        print(f"Email already sent for {week}, skipping.")
        return 0
    if not RECIPIENTS:
        print("DIGEST_TO resolved to no recipients.", file=sys.stderr)
        return 2
    if not APP_PASSWORD:
        print("GMAIL_APP_PASSWORD not set — cannot send.", file=sys.stderr)
        return 2
    try:
        send(week, RECIPIENTS)
    except Exception as e:
        print(f"SMTP send failed: {e}", file=sys.stderr)
        return 1
    open(sent_marker, "w").write(week)
    return 0


if __name__ == "__main__":
    sys.exit(main())
