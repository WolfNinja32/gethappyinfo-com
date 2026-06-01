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
ARCHIVE_FILE = ROOT / "public" / "joy-archive.json"

# Source allowlist — topic-clean GNN category feeds (validated 2026-05-30).
# Curating at the source is the primary topic control; the denylist below is a
# backstop. Slot policy A: an animal story always leads (slot 1); the other two
# slots fill from the non-animal feeds in proportion to weight. Weights seed from
# the Reuters Digital News Report 2024 interest figures, adjusted per Gregory
# (Kindness +20% -> 36, Science -20% -> 23). Tune freely.
_GNN = "https://www.goodnewsnetwork.org/category/news"
FEEDS = [
    {"topic": "animals",   "url": f"{_GNN}/animals/feed/",   "weight": 0,  "is_animal": True},
    {"topic": "inspiring", "url": f"{_GNN}/inspiring/feed/", "weight": 36, "is_animal": False},
    {"topic": "health",    "url": f"{_GNN}/health/feed/",    "weight": 30, "is_animal": False},
    {"topic": "earth",     "url": f"{_GNN}/earth/feed/",     "weight": 27, "is_animal": False},
    {"topic": "science",   "url": f"{_GNN}/science/feed/",   "weight": 23, "is_animal": False},
]

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
    # off-brand / mystical (backstop — the feed allowlist is the primary control)
    "horoscope", "zodiac", "astrology", "astrologer", "astrological",
    "tarot", "psychic", "clairvoyant", "occult", "séance", "seance",
    "ouija", "star sign", "mercury retrograde", "crystal healing",
    "manifestation ritual",
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


def fetch_feed(url: str) -> list[dict]:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
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


def fetch_all(feeds: list[dict]) -> dict[str, list[dict]]:
    """Fetch every feed independently. A feed that fails yields an empty list so
    one dead source never sinks the run (degrade-safe)."""
    by_topic: dict[str, list[dict]] = {}
    for f in feeds:
        try:
            by_topic[f["topic"]] = fetch_feed(f["url"])
        except Exception as exc:  # network / parse — skip this feed, keep going
            print(f"[update_joy] feed '{f['topic']}' fetch failed: {exc}", file=sys.stderr)
            by_topic[f["topic"]] = []
    return by_topic


def weighted_topic_cycle(feeds: list[dict]) -> list[str]:
    """Smooth weighted round-robin over the non-animal topics: a cyclic sequence
    in which each topic appears `weight` times, evenly interleaved (no clumps).
    Indexing into it by date gives a deterministic rotation whose long-run share
    per topic approximates its weight."""
    pool = [(f["topic"], f["weight"]) for f in feeds
            if not f["is_animal"] and f["weight"] > 0]
    total = sum(w for _, w in pool)
    if total == 0:
        return [t for t, _ in pool]
    current = {t: 0 for t, _ in pool}
    seq: list[str] = []
    for _ in range(total):
        for t, w in pool:
            current[t] += w
        best = max(pool, key=lambda tw: current[tw[0]])[0]
        current[best] -= total
        seq.append(best)
    return seq


def select(by_topic: dict[str, list[dict]], recent_urls: set[str],
           date, feeds: list[dict]) -> list[dict]:
    """Slot policy A: slot 1 is an animal story; slots 2-3 come from the
    non-animal feeds, prioritised by a date-rotated weighted topic order, two
    distinct topics where possible. Degrades gracefully when a feed is dry."""
    chosen: list[dict] = []
    seen: set[str] = set()

    def take_from(topic: str) -> bool:
        for it in by_topic.get(topic, []):
            if it["url"] in recent_urls or it["url"] in seen:
                continue
            if is_denylisted(it["text"]):
                continue
            if too_similar(it["text"], chosen):
                continue
            chosen.append(it)
            seen.add(it["url"])
            return True
        return False

    # Slot 1 — guaranteed animal lead.
    animal_topics = [f["topic"] for f in feeds if f["is_animal"]]
    if not any(take_from(t) for t in animal_topics):
        print("[update_joy] no animal story available — slot 1 falls back to a "
              "weighted topic", file=sys.stderr)

    # Slots 2-3 — date-rotated weighted order, distinct topics first.
    cycle = weighted_topic_cycle(feeds)
    if cycle:
        start = (date.toordinal() * (NUM_HEADLINES - 1)) % len(cycle)
        rotated = [cycle[(start + i) % len(cycle)] for i in range(len(cycle))]
        topic_order: list[str] = []
        for t in rotated:
            if t not in topic_order:
                topic_order.append(t)
        # First pass: one story per distinct topic, in weighted-rotation order.
        for t in topic_order:
            if len(chosen) >= NUM_HEADLINES:
                break
            take_from(t)
        # Second pass: if still short (some topics dry), allow extra picks.
        for t in topic_order:
            if len(chosen) >= NUM_HEADLINES:
                break
            take_from(t)

    # Last resort: keep pulling from any feed (incl. animals) until full or dry,
    # so a single feed with surplus items can still fill every slot.
    progress = True
    while len(chosen) < NUM_HEADLINES and progress:
        progress = False
        for f in feeds:
            if len(chosen) >= NUM_HEADLINES:
                break
            if take_from(f["topic"]):
                progress = True

    return chosen[:NUM_HEADLINES]


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


def main(force: bool = False) -> int:
    date = today_pt()
    task = pick_task(date)
    existing = load_json(JOY_FILE, default=None)

    # Idempotency guard. This updater is fired by a reliable Cloudflare cron AND a
    # later GitHub-cron backup (see .github/workflows/daily.yml). When today's
    # postcard is already complete, the second trigger must be a no-op — otherwise
    # it could re-pick newer stories and reshuffle a set the first run published.
    # Bail before the network fetch. `--force` overrides for deliberate same-day
    # regeneration.
    if (not force and existing
            and existing.get("lastUpdated") == date.isoformat()
            and isinstance(existing.get("topNews"), list)
            and len(existing["topNews"]) >= NUM_HEADLINES):
        print(f"[update_joy] {date.isoformat()} already current "
              f"({len(existing['topNews'])} stories) — nothing to do "
              "(use --force to override)")
        return 0

    by_topic = fetch_all(FEEDS)

    recent = prune_recent(load_json(RECENT_FILE, default=[]), date)
    recent_urls = {e["url"] for e in recent}
    picks = select(by_topic, recent_urls, date, FEEDS)

    # Record fresh picks (already denylist-filtered by select()) in recent.json.
    if picks:
        for p in picks:
            recent.append({"url": p["url"], "date": date.isoformat()})
        RECENT_FILE.write_text(json.dumps(recent, indent=2) + "\n", encoding="utf-8")
        print(f"[update_joy] {len(picks)} fresh headline(s); recent.json now {len(recent)} urls")

    # Assemble the published set — fail-closed AND degrade-safe: fresh picks
    # first, then top up from the last good set so a thin fetch never shrinks or
    # blanks the site. Carryover items are denylist-filtered (so a story that was
    # fine under an older/looser denylist can't linger once the denylist grows)
    # and deduped by URL. The result is the most CLEAN items available, up to 3.
    news = list(picks)
    if len(news) < NUM_HEADLINES and existing and isinstance(existing.get("topNews"), list):
        # Dedup on canon()'d URLs both sides — fresh URLs are already canonical,
        # but carryover may predate canon() or differ by slash/query, so compare
        # like-for-like to avoid the same story appearing twice.
        have = {canon(n.get("url", "")) for n in news}
        carried = 0
        for n in existing["topNews"]:
            if len(news) >= NUM_HEADLINES:
                break
            cu = canon(n.get("url", ""))
            if cu in have or is_denylisted(n.get("text", "")):
                continue
            news.append(n)
            have.add(cu)
            carried += 1
        if carried:
            print(f"[update_joy] topped up with {carried} filtered item(s) from last set")
    if not news:
        print("[update_joy] no clean headlines available — writing task only", file=sys.stderr)

    joy = {"lastUpdated": date.isoformat(), "dailyTask": task, "topNews": news}
    JOY_FILE.write_text(json.dumps(joy, indent=2) + "\n", encoding="utf-8")
    print(f"[update_joy] wrote {JOY_FILE.relative_to(ROOT)} — task seeded for {date.isoformat()}")

    # Append today to the public archive (keyed by ISO date so client-side
    # per-day routes — /YYYY-MM-DD — can resolve old postcards). Object keyed
    # by date sorts chronologically when serialized with sort_keys=True.
    # Fail closed: a fresh archive is fine, but if the file exists and won't
    # parse (or has the wrong shape) we must NOT overwrite it — doing so would
    # replace all prior days with today alone. Abort instead so a failed run
    # surfaces it rather than silently truncating history.
    if ARCHIVE_FILE.exists():
        try:
            archive = json.loads(ARCHIVE_FILE.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            print(
                f"[update_joy] ERROR: {ARCHIVE_FILE.relative_to(ROOT)} is corrupt "
                f"({exc}); refusing to overwrite and lose history",
                file=sys.stderr,
            )
            return 1
        if not isinstance(archive, dict) or not isinstance(archive.get("days"), dict):
            print(
                f"[update_joy] ERROR: {ARCHIVE_FILE.relative_to(ROOT)} has an "
                "unexpected shape; refusing to overwrite and lose history",
                file=sys.stderr,
            )
            return 1
    else:
        archive = {"days": {}}
    archive["days"][date.isoformat()] = joy
    ARCHIVE_FILE.write_text(
        json.dumps(archive, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(f"[update_joy] archive: {len(archive['days'])} day(s) total")
    return 0


if __name__ == "__main__":
    sys.exit(main(force="--force" in sys.argv[1:]))
