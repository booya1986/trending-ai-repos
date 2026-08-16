#!/usr/bin/env python3
"""Render the week's social-share card to reports/<week>/og.png (1200x630).

Usage: build_og_image.py <reports/WEEK-dir> [enriched.json]

WhatsApp, Slack, LinkedIn, and X all preview a shared link from its og:image.
Without one the link renders as a bare grey box, which reads as broken.

Rendered by screenshotting an HTML card rather than drawing with an imaging
library: Hebrew needs RTL bidi handling and proper text shaping, which a
browser does correctly and PIL does not.

Non-fatal: if Playwright or Chromium is unavailable the report still ships,
just without a per-week card.
"""
import html as _h
import json
import os
import sys

W, H = 1200, 630


def card_html(week, repo_count, news_count, names):
    items = "".join(
        f'<li><span class="dot"></span><span class="txt">{_h.escape(n)}</span></li>'
        for n in names[:3]
    )
    label = week.replace("-", " ").replace("W", "שבוע ")
    # Keep the subtitle entirely in Hebrew. A Latin word such as "repos" inside
    # an RTL run reorders under bidi and renders as "1 2 repos · כתבות".
    bits = []
    if repo_count:
        bits.append(f"{repo_count} מאגרים בולטים")
    if news_count:
        bits.append(f"{news_count} כתבות")
    sub = " · ".join(bits) or "הבולטים השבוע"
    return f"""<!DOCTYPE html><html lang="he" dir="rtl"><head><meta charset="utf-8">
<link href="https://fonts.googleapis.com/css2?family=Noto+Sans+Hebrew:wght@300;400;600;700&display=swap" rel="stylesheet">
<style>
  * {{ margin:0; padding:0; box-sizing:border-box; }}
  html, body {{ margin:0; padding:0; background:#1b1b1b; }}
  /* Everything lives inside a fixed-size card that is screenshotted directly.
     Sizing the viewport instead lets an absolutely-positioned decoration widen
     the layout box, which pushes RTL text off the right edge. */
  .card {{ position:relative; width:{W}px; height:{H}px; overflow:hidden;
           background:#1b1b1b; color:#c5c1b9;
           font-family:'Noto Sans Hebrew',sans-serif; }}
  .bg-grid {{ position:absolute; inset:0;
    background-image:
      linear-gradient(rgba(34,197,94,0.05) 1px, transparent 1px),
      linear-gradient(90deg, rgba(34,197,94,0.05) 1px, transparent 1px);
    background-size:48px 48px;
    mask-image:radial-gradient(ellipse 80% 70% at 70% 0%, black 35%, transparent 100%);
    -webkit-mask-image:radial-gradient(ellipse 80% 70% at 70% 0%, black 35%, transparent 100%); }}
  .bg-blob {{ position:absolute; top:-150px; left:-150px;
    width:540px; height:540px; border-radius:50%;
    background:radial-gradient(circle, rgba(34,197,94,0.18) 0%, transparent 68%); }}
  .wrap {{ position:absolute; inset:0; z-index:2; padding:58px 68px;
           display:flex; flex-direction:column; }}
  .eyebrow {{ font-size:21px; font-weight:600; letter-spacing:4px; color:#22c55e;
              text-transform:uppercase; margin-bottom:16px; }}
  h1 {{ font-size:62px; font-weight:700; color:#f3f2ef; line-height:1.16;
        letter-spacing:-1px; margin-bottom:12px; }}
  .sub {{ font-size:26px; color:#a09d96; font-weight:300; margin-bottom:30px; }}
  ul {{ list-style:none; display:flex; flex-direction:column; gap:12px; }}
  li {{ font-size:22px; color:#c5c1b9; font-weight:300; display:flex;
        align-items:center; gap:12px; direction:ltr; justify-content:flex-end; }}
  li span.txt {{ overflow:hidden; text-overflow:ellipsis; white-space:nowrap;
                 font-family:ui-monospace,SFMono-Regular,Menlo,monospace; font-size:21px; }}
  .dot {{ width:8px; height:8px; border-radius:50%; background:#22c55e;
          flex-shrink:0; box-shadow:0 0 10px rgba(34,197,94,0.85); }}
  .foot {{ margin-top:auto; display:flex; align-items:center; gap:14px;
           font-size:20px; color:#96928c; }}
  .pill {{ background:rgba(34,197,94,0.14); border:1px solid rgba(34,197,94,0.35);
           color:#22c55e; border-radius:999px; padding:6px 20px;
           font-size:19px; font-weight:600; white-space:nowrap; }}
</style></head><body>
<div class="card">
  <div class="bg-grid"></div>
  <div class="bg-blob"></div>
  <div class="wrap">
    <div class="eyebrow">Weekly AI Digest</div>
    <h1>&#128293; טרנדים ב-AI/LLM</h1>
    <div class="sub">{sub}</div>
    <ul>{items}</ul>
    <div class="foot"><span class="pill">{label}</span><span>מה עלה השבוע, ולמה זה משנה</span></div>
  </div>
</div></body></html>"""


def load(outdir, enriched):
    """Prefer the enriched build JSON; fall back to parsing the built page so
    the card can still be regenerated for an already-published week."""
    week, names, repo_count, news_count = "", [], 0, 0
    if enriched and os.path.exists(enriched):
        data = json.load(open(enriched, encoding="utf-8"))
        week = data.get("week", "")
        repos = data.get("repos", []) or []
        repo_count = len(repos)
        news_count = len(data.get("news") or data.get("stories") or [])
        names = [r.get("full_name", "") for r in repos]
    if not news_count:
        # News is built in its own file and copied next to the report, so the
        # enriched repo JSON alone reports zero stories.
        news_path = os.path.join(outdir, "news.json")
        if os.path.exists(news_path):
            try:
                nd = json.load(open(news_path, encoding="utf-8"))
                news_count = len(nd.get("stories") or nd.get("items") or [])
            except (ValueError, OSError):
                pass
    if not names:
        import re
        page = os.path.join(outdir, "index.html")
        if os.path.exists(page):
            src = open(page, encoding="utf-8").read()
            found = re.findall(r'href="https://github\.com/([\w.\-]+/[\w.\-]+)"', src)
            # Count every distinct repo on the page, then display the first few.
            # Counting the displayed slice instead reports "3" on a 10-repo report.
            seen, uniq = set(), []
            for n in found:
                if n not in seen:
                    seen.add(n)
                    uniq.append(n)
            repo_count = repo_count or len(uniq)
            names = uniq[:3]
    if not week:
        week = os.path.basename(outdir.rstrip("/"))
    return week, repo_count, news_count, names


def main():
    if len(sys.argv) < 2:
        print("usage: build_og_image.py <reports/WEEK-dir> [enriched.json]", file=sys.stderr)
        sys.exit(2)
    outdir = sys.argv[1]
    enriched = sys.argv[2] if len(sys.argv) > 2 else None

    week, repo_count, news_count, names = load(outdir, enriched)

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("playwright not installed; skipping OG image", file=sys.stderr)
        return

    out = os.path.join(outdir, "og.png")
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch()
            page = browser.new_page(viewport={"width": W, "height": H},
                                    device_scale_factor=1)
            page.set_content(card_html(week, repo_count, news_count, names),
                             wait_until="networkidle")
            # Without this the screenshot can land before the webfont swaps in,
            # and Hebrew renders in a fallback face.
            page.evaluate("document.fonts.ready")
            page.wait_for_timeout(600)
            # Screenshot the card element, not the viewport: guarantees exactly
            # 1200x630 with nothing clipped at the edges.
            page.locator(".card").screenshot(path=out, type="png")
            browser.close()
    except Exception as e:
        print(f"OG image failed ({type(e).__name__}: {e}); report ships without it",
              file=sys.stderr)
        return

    size = os.path.getsize(out)
    print(f"wrote {out} ({size:,} bytes)")
    # WhatsApp is the strictest common consumer and gets unreliable past ~300KB.
    if size > 300_000:
        print(f"::warning title=og::og.png is {size:,} bytes; "
              f"WhatsApp previews get unreliable above ~300KB", file=sys.stderr)


if __name__ == "__main__":
    main()
