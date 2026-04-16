#!/usr/bin/env python3
"""Morning Digest — fetch RSS, synthesize with Gemini, email via Gmail."""

import os
import re
import ssl
import smtplib
import time
from datetime import datetime, timedelta, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from html import unescape
from urllib.request import Request, urlopen
from urllib.error import URLError

import feedparser
from dotenv import load_dotenv
from google import genai
from google.genai import types

load_dotenv()

# ── Constants ────────────────────────────────────────────────────────────────

HOURS_FRESH = 24
MAX_ARTICLES_PER_FEED = 3
MAX_ARTICLES_TOTAL = 30

NITTER_INSTANCES = [
    "nitter.privacydev.net",
    "nitter.poast.org",
    "nitter.woodland.cafe",
    "nitter.1d4.us",
]

TWITTER_ACCOUNTS = ["AndrewYNg", "ylecun", "sama", "elonmusk"]

# ── Feeds ────────────────────────────────────────────────────────────────────

FEEDS = {
    "tech_ai": [
        ("The Verge", "https://www.theverge.com/rss/index.xml"),
        ("TechCrunch", "https://techcrunch.com/feed/"),
        ("Wired", "https://www.wired.com/feed/rss"),
        ("Hacker News", "https://news.ycombinator.com/rss"),
        ("Ars Technica", "https://feeds.arstechnica.com/arstechnica/index"),
        ("MIT Technology Review", "https://www.technologyreview.com/feed/"),
        ("VentureBeat AI", "https://venturebeat.com/category/ai/feed/"),
        ("NVIDIA Newsroom", "https://nvidianews.nvidia.com/rss"),
        ("Anthropic", "https://www.anthropic.com/rss.xml"),
        ("OpenAI", "https://openai.com/news/rss.xml"),
        ("Google DeepMind", "https://deepmind.google/blog/rss.xml"),
        ("Reddit AI/Tech", "https://www.reddit.com/r/artificial+MachineLearning+singularity+technology+ChatGPT+LocalLLaMA/top/.rss?t=day"),
    ],
    "markets_world": [
        ("CNBC", "https://www.cnbc.com/id/100003114/device/rss/rss.html"),
        ("MarketWatch", "https://feeds.marketwatch.com/marketwatch/topstories/"),
        ("Yahoo Finance", "https://finance.yahoo.com/news/rssindex"),
        ("Reuters Business", "https://www.reutersagency.com/feed/?best-topics=business-finance&post_type=best"),
        ("BBC Business", "https://feeds.bbci.co.uk/news/business/rss.xml"),
        ("Guardian Business", "https://www.theguardian.com/uk/business/rss"),
        ("Axios", "https://api.axios.com/feed/"),
        ("Reuters World", "https://www.reutersagency.com/feed/?best-topics=world&post_type=best"),
        ("BBC World", "https://feeds.bbci.co.uk/news/world/rss.xml"),
        ("Foreign Policy", "https://foreignpolicy.com/feed/"),
        ("Al Jazeera", "https://www.aljazeera.com/xml/rss/all.xml"),
        ("NPR World", "https://feeds.npr.org/1004/rss.xml"),
        ("Deutsche Welle", "https://rss.dw.com/rdf/rss-en-all"),
        ("The Economist", "https://www.economist.com/rss"),
    ],
    "trending": [
        ("Digiday", "https://digiday.com/feed/"),
        ("Social Media Today", "https://www.socialmediatoday.com/rss.xml"),
        ("Axios Media", "https://api.axios.com/feed/technology"),
        ("Reddit Popular", "https://www.reddit.com/r/popular/top/.rss?t=day"),
    ],
}


# ── Nitter ───────────────────────────────────────────────────────────────────

def build_nitter_feeds():
    """Try Nitter instances, return RSS feeds for Twitter accounts."""
    for instance in NITTER_INSTANCES:
        try:
            req = Request(
                f"https://{instance}/",
                method="HEAD",
                headers={"User-Agent": "MorningDigest/1.0"},
            )
            urlopen(req, timeout=5)
            print(f"  Using Nitter instance: {instance}")
            return [
                (f"X/{acct}", f"https://{instance}/{acct}/rss")
                for acct in TWITTER_ACCOUNTS
            ]
        except (URLError, OSError, Exception):
            continue
    print("  ⚠ No working Nitter instance found, skipping X/Twitter feeds")
    return []


# ── Article helpers ──────────────────────────────────────────────────────────

def is_recent(entry):
    """Check if an entry was published within HOURS_FRESH."""
    ts = entry.get("published_parsed") or entry.get("updated_parsed")
    if not ts:
        return False
    published = datetime(*ts[:6], tzinfo=timezone.utc)
    return datetime.now(timezone.utc) - published < timedelta(hours=HOURS_FRESH)


def normalize_title(title):
    """Lowercase, strip non-alphanumeric for dedup."""
    return re.sub(r"[^a-z0-9]", "", title.lower())


def strip_html(text):
    """Remove HTML tags."""
    return unescape(re.sub(r"<[^>]+>", "", text or ""))


def fetch_articles():
    """Fetch, filter, deduplicate articles from all feeds."""
    nitter_feeds = build_nitter_feeds()
    FEEDS["trending"].extend(nitter_feeds)

    all_articles = {}
    seen_titles = set()

    for category, feeds in FEEDS.items():
        fresh = old = dupes = 0
        articles = []

        for source_name, url in feeds:
            try:
                is_reddit = "reddit.com" in url
                is_nitter = any(inst in url for inst in NITTER_INSTANCES)

                if is_reddit:
                    feed = feedparser.parse(
                        url,
                        request_headers={"User-Agent": "MorningDigest/1.0"},
                    )
                else:
                    feed = feedparser.parse(url)

                count = 0
                for entry in feed.entries:
                    if count >= MAX_ARTICLES_PER_FEED:
                        break

                    if not is_recent(entry):
                        old += 1
                        continue

                    title = entry.get("title", "").strip()
                    if not title:
                        continue

                    norm = normalize_title(title)
                    if norm in seen_titles:
                        dupes += 1
                        continue
                    seen_titles.add(norm)

                    summary = strip_html(
                        entry.get("summary", "") or entry.get("description", "")
                    )
                    max_len = 150 if (is_reddit or is_nitter) else 280
                    if len(summary) > max_len:
                        summary = summary[:max_len].rsplit(" ", 1)[0] + "..."

                    articles.append(f"[{source_name}] {title}: {summary}")
                    fresh += 1
                    count += 1

            except Exception as e:
                print(f"  ⚠ Failed to fetch {source_name}: {e}")

        cap = {"tech_ai": 15, "markets_world": 10, "trending": 5}.get(category, 10)
        all_articles[category] = articles[:cap]
        print(f"  {category}: {fresh} fresh | {old} too old | {dupes} duplicates | {len(all_articles[category])} sent to Gemini")

    return all_articles


# ── Gemini ───────────────────────────────────────────────────────────────────

def build_prompt(articles):
    """Build the Gemini prompt from categorized articles."""
    today = datetime.now().strftime("%A, %B %-d, %Y")

    sections = ""
    for cat, items in articles.items():
        if items:
            sections += f"\n--- {cat.upper()} ---\n" + "\n".join(items) + "\n"

    return f"""You are an expert news curator writing a morning briefing email.
Write like Superhuman AI and The Rundown AI — sharp, concise, high signal.

Today is {today}.

RULES:
- Only genuinely newsworthy stories, no padding or filler
- Cover AI/tech comprehensively — this is the priority section
- Each bullet: what happened + why it matters, in 1-2 sentences
- No repetition across sections — consolidate overlapping stories
- Add "(via Source Name)" at the end of each bullet
- For Reddit/X items, surface what people are actually talking about

USE EXACTLY THESE SECTION HEADERS:

## 🌅 Good Morning
Start with the date and one sentence naming the single biggest story today.

## 🤖 Tech & AI
ALL significant AI/tech stories. No fixed count — include everything noteworthy.

## 🌍 Markets & World
Market moves, macro trends, geopolitics.

## 🔥 Trending Today
Viral stuff from Reddit and X, cultural moments.

For each bullet use this format:
- **Headline in bold** — explanation of what happened and why it matters. (via Source Name)

HERE ARE TODAY'S ARTICLES:
{sections}
"""


def call_gemini(prompt):
    """Call Gemini with fallback chain."""
    api_key = os.environ["GEMINI_API_KEY"]
    client = genai.Client(api_key=api_key)

    models = ["gemini-2.5-flash", "gemini-2.0-flash", "gemini-2.5-pro"]

    for model in models:
        try:
            print(f"  Trying {model}...")
            response = client.models.generate_content(
                model=model,
                contents=prompt,
                config=types.GenerateContentConfig(
                    temperature=0.7,
                    max_output_tokens=4096,
                ),
            )
            print(f"  ✓ Got response from {model}")
            return response.text
        except Exception as e:
            err = str(e).lower()
            print(f"  ✗ {model} error: {e}")
            if "api_key_invalid" in err or "api key not valid" in err:
                raise RuntimeError("Invalid Gemini API key. Check your .env file.") from e
            if any(k in err for k in ["503", "unavailable", "429", "resource_exhausted"]):
                print(f"  ⚠ {model} unavailable, trying next...")
                time.sleep(2)
                continue
            raise

    raise RuntimeError("All Gemini models failed")


# ── HTML Email ───────────────────────────────────────────────────────────────

SECTION_STYLES = {
    "Good Morning": {"bg": "#E8F5E9", "border": "#7CB99A", "header": "#2E7D52"},
    "Tech & AI": {"bg": "#FFF8F0", "border": "#F4A261", "header": "#C4622D"},
    "Markets & World": {"bg": "#F0F7F4", "border": "#A8D5B5", "header": "#3A7D5A"},
    "Trending Today": {"bg": "#FFFBF0", "border": "#F4A261", "header": "#C4622D"},
}

SECTION_EMOJIS = {
    "Good Morning": "\U0001f305",
    "Tech & AI": "\U0001f916",
    "Markets & World": "\U0001f30d",
    "Trending Today": "\U0001f525",
}


def parse_bullet(text):
    """Extract headline, body, source from a bullet line."""
    text = text.lstrip("- •").strip()

    # Extract (via Source)
    source = ""
    via_match = re.search(r"\(via\s+(.+?)\)\s*$", text)
    if via_match:
        source = via_match.group(1)
        text = text[: via_match.start()].strip()

    # Extract **bold headline**
    headline = ""
    body = text
    bold_match = re.match(r"\*\*(.+?)\*\*\s*[-–—:]?\s*(.*)", text, re.DOTALL)
    if bold_match:
        headline = bold_match.group(1).strip()
        body = bold_match.group(2).strip()
    else:
        # No bold — first sentence is headline
        parts = re.split(r"[.!?]", text, maxsplit=1)
        if len(parts) == 2:
            headline = parts[0].strip()
            body = parts[1].strip()
        else:
            headline = text
            body = ""

    return headline, body, source


def render_bullet(headline, body, source, is_last):
    """Render a single bullet as an HTML table row."""
    divider = "" if is_last else (
        '<tr><td colspan="2" style="padding:0;">'
        '<div style="border-bottom:1px solid #E8E3DD;margin:8px 0;"></div>'
        '</td></tr>'
    )
    source_html = (
        f'<div style="font-family:Arial,sans-serif;font-size:11px;'
        f'font-style:italic;color:#A09890;margin-top:2px;">via {source}</div>'
        if source else ""
    )
    body_html = (
        f'<div style="font-family:Arial,sans-serif;font-size:13px;'
        f'color:#555;margin-top:2px;">{body}</div>'
        if body else ""
    )
    return f"""
    <tr>
      <td style="vertical-align:top;padding:8px 10px 8px 0;width:16px;color:#C4A882;font-size:10px;">&#9654;</td>
      <td style="padding:8px 0;">
        <div style="font-family:Georgia,serif;font-size:14px;font-weight:bold;color:#2D2D2D;">{headline}</div>
        {body_html}
        {source_html}
      </td>
    </tr>
    {divider}
    """


def render_section(name, content_md):
    """Render a full section card."""
    style = SECTION_STYLES.get(name, SECTION_STYLES["Tech & AI"])
    emoji = SECTION_EMOJIS.get(name, "")

    if name == "Good Morning":
        # Render as italic intro paragraph
        clean = re.sub(r"^##\s*\S+\s*Good Morning\s*", "", content_md).strip()
        clean = re.sub(r"\*\*(.+?)\*\*", r"\1", clean)
        return f"""
        <div style="background:{style['bg']};border:1px solid {style['border']};
                    border-radius:12px;padding:20px 24px;margin-bottom:16px;">
          <div style="font-family:Georgia,serif;font-size:15px;font-style:italic;
                      color:{style['header']};line-height:1.6;">
            {emoji} {clean}
          </div>
        </div>
        """

    # Parse bullets
    lines = [l.strip() for l in content_md.split("\n") if l.strip().startswith(("- ", "* ", "• "))]
    if not lines:
        return ""

    bullets_html = ""
    for i, line in enumerate(lines):
        h, b, s = parse_bullet(line)
        bullets_html += render_bullet(h, b, s, i == len(lines) - 1)

    return f"""
    <div style="background:{style['bg']};border:1px solid {style['border']};
                border-radius:12px;padding:20px 24px;margin-bottom:16px;">
      <div style="font-family:Georgia,serif;font-size:18px;font-weight:bold;
                  color:{style['header']};padding-bottom:10px;margin-bottom:12px;
                  border-bottom:2px solid {style['border']};">
        {emoji} {name}
      </div>
      <table cellpadding="0" cellspacing="0" border="0" width="100%">
        {bullets_html}
      </table>
    </div>
    """


def build_html(gemini_text):
    """Build the full HTML email from Gemini's markdown output."""
    today = datetime.now().strftime("%A, %B %-d, %Y")

    # Split into sections
    section_pattern = r"##\s*(?:\S+\s+)?(Good Morning|Tech & AI|Markets & World|Trending Today)"
    parts = re.split(section_pattern, gemini_text)

    sections_html = ""
    i = 1
    while i < len(parts) - 1:
        name = parts[i].strip()
        content = parts[i + 1].strip()
        sections_html += render_section(name, content)
        i += 2

    return f"""<!DOCTYPE html>
<html>
<head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1.0"></head>
<body style="margin:0;padding:0;background:#F7F4EF;font-family:Arial,sans-serif;">
<div style="max-width:650px;margin:0 auto;padding:20px;">

  <!-- Header -->
  <div style="background:#E8F5E9;border-radius:12px 12px 0 0;padding:30px 24px;text-align:center;">
    <div style="font-family:Arial,sans-serif;font-size:11px;letter-spacing:3px;
                color:#7CB99A;text-transform:uppercase;margin-bottom:4px;">Morning Digest</div>
    <h1 style="font-family:Georgia,serif;font-size:26px;color:#2E7D52;
               margin:8px 0;font-weight:normal;">Your Daily Brief</h1>
    <div style="font-family:Arial,sans-serif;font-size:13px;color:#6B9E7D;">{today}</div>
  </div>

  <div style="padding:16px 0;">
    {sections_html}
  </div>

  <!-- Footer -->
  <div style="background:#EDE9E3;border-radius:0 0 12px 12px;padding:20px 24px;text-align:center;">
    <div style="font-family:Arial,sans-serif;font-size:11px;color:#A09890;line-height:1.6;">
      Generated by your morning digest bot &middot; Powered by Gemini &middot; Only news from the last 24 hours
    </div>
  </div>

</div>
</body>
</html>"""


# ── Email ────────────────────────────────────────────────────────────────────

def send_email(html):
    """Send the digest email via Gmail SMTP."""
    sender = os.environ["GMAIL_ADDRESS"]
    password = os.environ["GMAIL_APP_PASSWORD"]
    recipient = os.environ["RECIPIENT_EMAIL"]
    today = datetime.now().strftime("%B %-d, %Y")

    msg = MIMEMultipart("alternative")
    msg["Subject"] = f"Morning Digest — {today}"
    msg["From"] = sender
    msg["To"] = recipient
    msg.attach(MIMEText(html, "html"))

    context = ssl.create_default_context()
    with smtplib.SMTP_SSL("smtp.gmail.com", 465, context=context) as server:
        server.login(sender, password)
        server.sendmail(sender, recipient, msg.as_string())

    return recipient


# ── Main ─────────────────────────────────────────────────────────────────────

def main():
    # Validate required env vars upfront
    missing = [k for k in ["GEMINI_API_KEY", "GMAIL_ADDRESS", "GMAIL_APP_PASSWORD", "RECIPIENT_EMAIL"] if not os.environ.get(k)]
    if missing:
        raise RuntimeError(f"Missing environment variables: {', '.join(missing)}")

    print("[1/4] Fetching articles...")
    articles = fetch_articles()

    total = sum(len(v) for v in articles.values())
    print(f"  Total: {total} articles")

    if total == 0:
        print("No articles found. Exiting.")
        return

    print("[2/4] Calling Gemini...")
    prompt = build_prompt(articles)
    gemini_text = call_gemini(prompt)

    print("[3/4] Building HTML email...")
    html = build_html(gemini_text)

    print("[4/4] Sending email...")
    recipient = send_email(html)
    print(f"Done! Digest sent to {recipient}")


if __name__ == "__main__":
    main()
