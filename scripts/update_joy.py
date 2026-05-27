#!/usr/bin/env python3
"""gethappyinfo.com daily updater — stdlib only, no LLM, no external deps.

Pulls Good News Network's RSS feed, filters/dedups, and writes public/joy.json
with a date-seeded micro-joy task plus a few good-news headlines.

Design invariants (see plan):
  * No paid/external compute and no third-party packages — urllib + xml.etree.
  * Fail-closed safety filter (a denylist match is dropped, never kept on doubt).
  * Cross-day dedup via data/recent.json so feed-top headlines that linger for
    days don't show twice.
  * Degrade-safe: the daily task always rotates; headlines carry over from the
    last good run when the fetch fails or yields nothing new. The site never
    blanks.
"""
from __future__ import annotations

import html
import json
import re
import sys
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime
from difflib import SequenceMatcher
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit
from zoneinfo import ZoneInfo

FEED_URL = "https://www.goodnewsnetwork.org/feed/"
USER_AGENT = "gethappyinfo-bot/1.0 (+https://gethappyinfo.com)"
NUM_HEADLINES = 3
RECENT_DAYS = 14
SIMILARITY_THRESHOLD = 0.6
FETCH_TIMEOUT = 20
PACIFIC = ZoneInfo("America/Los_Angeles")

ROOT = Path(__file__).resolve().parent.parent
TASKS_FILE = ROOT / "data" / "tasks.txt"
RECENT_FILE = ROOT / "data" / "recent.json"
JOY_FILE = ROOT / "public" / "joy.json"

# Fail-closed denylist. A positive-news source rarely trips these, but when a
# title carries a grim or off-brand term we drop it rather than risk it. Word
# boundaries keep "war" from matching "warm" / "dead" from "deadline".
DENY_TERMS = [
    "kill", "killed", "dead", "death", "died", "dies", "dying", "fatal",
    "murder", "homicide", "shooting", "shot", "gun", "stab", "assault",
    "attack", "war", "bomb", "explosion", "terror", "terrorist", "hostage",
    "kidnap", "rape", "abuse", "suicide", "overdose", "crash", "disaster",
    "tragedy", "tragic", "victim", "wounded", "massacre", "genocide",
    "outbreak", "pandemic", "lawsuit", "arrested", "indicted", "scandal",
    # profanity (kept minimal; extend as needed)
    "damn", "hell",
]
DENY_RE = re.compile(r"\b(" + "|".join(map(re.escape, DENY_TERMS)) + r")\b", re.I)


def today_pt():
    return datetime.now(PACIFIC).date()


def load_json(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default


def canon(url: str) -> str:
    """Drop query/fragment and any trailing slash so dedup is stable."""
    p = urlsplit(url.strip())
    return urlunsplit((p.scheme, p.netloc, p.path.rstrip("/"), "", ""))


def norm_title(title: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", title.lower()).strip()


def is_denylisted(title: str) -> bool:
    return bool(DENY_RE.search(title))


def too_similar(title: str, chosen: list[dict]) -> bool:
    nt = norm_title(title)
    return any(
        SequenceMatcher(None, nt, norm_title(c["text"])).ratio() > SIMILARITY_THRESHOLD
        for c in chosen
    )


def fetch_items() -> list[dict]:
    req = urllib.request.Request(FEED_URL, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=FETCH_TIMEOUT) as resp:
        raw = resp.read()
    root = ET.fromstring(raw)
    items = []
    for item in root.iter("item"):
        title = html.unescape((item.findtext("title") or "").strip())
        link = (item.findtext("link") or "").strip()
        if title and link:
            items.append({"text": title, "url": canon(link)})
    return items


def select(items: list[dict], recent_urls: set[str]) -> list[dict]:
    picks: list[dict] = []
    seen: set[str] = set()
    for it in items:
        if it["url"] in recent_urls or it["url"] in seen:
            continue
        if is_denylisted(it["text"]):
            continue
        if too_similar(it["text"], picks):
            continue
        picks.append(it)
        seen.add(it["url"])
        if len(picks) >= NUM_HEADLINES:
            break
    return picks


def pick_task(date) -> str:
    lines = [
        ln.strip()
        for ln in TASKS_FILE.read_text(encoding="utf-8").splitlines()
        if ln.strip() and not ln.lstrip().startswith("#")
    ]
    if not lines:
        return "Do one small, kind thing for someone today."
    return lines[date.toordinal() % len(lines)]


def prune_recent(recent: list[dict], date) -> list[dict]:
    cutoff = date.toordinal() - RECENT_DAYS
    out = []
    for e in recent:
        try:
            d = datetime.strptime(e["date"], "%Y-%m-%d").date()
        except (KeyError, ValueError):
            continue
        if d.toordinal() >= cutoff:
            out.append(e)
    return out


def main() -> int:
    date = today_pt()
    task = pick_task(date)
    existing = load_json(JOY_FILE, default=None)

    try:
        items = fetch_items()
    except Exception as exc:  # network / parse — degrade, don't crash
        print(f"[update_joy] feed fetch failed: {exc}", file=sys.stderr)
        items = []

    recent = prune_recent(load_json(RECENT_FILE, default=[]), date)
    recent_urls = {e["url"] for e in recent}
    picks = select(items, recent_urls)

    if picks:
        news = picks
        for p in picks:
            recent.append({"url": p["url"], "date": date.isoformat()})
        RECENT_FILE.write_text(json.dumps(recent, indent=2) + "\n", encoding="utf-8")
        print(f"[update_joy] {len(picks)} fresh headline(s); recent.json now {len(recent)} urls")
    elif existing and existing.get("topNews"):
        news = existing["topNews"]
        print("[update_joy] no fresh headlines — carrying over last good set")
    else:
        news = []
        print("[update_joy] no headlines available and no prior set — writing task only", file=sys.stderr)

    joy = {"lastUpdated": date.isoformat(), "dailyTask": task, "topNews": news}
    JOY_FILE.write_text(json.dumps(joy, indent=2) + "\n", encoding="utf-8")
    print(f"[update_joy] wrote {JOY_FILE.relative_to(ROOT)} — task seeded for {date.isoformat()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
