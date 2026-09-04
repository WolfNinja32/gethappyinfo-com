#!/usr/bin/env python3
"""gethappyinfo.com daily updater — postcard pipeline v4 (stdlib only).

Writes public/joy.json as {date, line, paragraph, optional seed}. Research may
return one GNN seed; writer + grounding (Workers AI) produce a matching
paragraph. Matching-card degrade: on writer/grounding fail, reuse yesterday's
line+paragraph+seed unit and only advance date. Site never blanks.

Design invariants:
  * No third-party packages — urllib + xml.etree.
  * Denylist filters research titles/feeds ONLY (never written paragraphs).
  * Cross-day URL dedup via data/recent.json (14 days).
  * Pacific edition date America/Los_Angeles.
  * AI optional: missing CLOUDFLARE_API_TOKEN or CLOUDFLARE_ACCOUNT_ID → degrade
    path, Actions stay green, conspicuous warning log.
  * V3 /story and /kindness generation is STOPPED (files left; see breadcrumb
    in main). Do not revive without a new product decision.
"""
from __future__ import annotations

import hashlib
import html
import json
import os
import re
import sys
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime
from difflib import SequenceMatcher
from email.utils import parsedate_to_datetime
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit
from zoneinfo import ZoneInfo

USER_AGENT = "gethappyinfo-bot/1.0 (+https://gethappyinfo.com)"
NUM_HEADLINES = 3
RECENT_DAYS = 14
SIMILARITY_THRESHOLD = 0.6
FETCH_TIMEOUT = 20
PACIFIC = ZoneInfo("America/Los_Angeles")
SITE_URL = "https://gethappyinfo.com"

ROOT = Path(__file__).resolve().parent.parent
TASKS_FILE = ROOT / "data" / "tasks.txt"
RECENT_FILE = ROOT / "data" / "recent.json"
JOY_FILE = ROOT / "public" / "joy.json"
ARCHIVE_FILE = ROOT / "public" / "joy-archive.json"

# --- V3 content library (per-story + per-kindness pages, SEO, agent feed) ---
VOICE_FILE = ROOT / "scripts" / "voice.md"
VOICE_FALLBACK_FILE = ROOT / "scripts" / "voice-fallback.md"
STORY_DIR = ROOT / "public" / "story"
KINDNESS_DIR = ROOT / "public" / "kindness"
SITEMAP_FILE = ROOT / "public" / "sitemap.xml"
ROBOTS_FILE = ROOT / "public" / "robots.txt"
LLMS_FILE = ROOT / "public" / "llms.txt"
INDEX_JSON_FILE = ROOT / "public" / "index.json"
TODAY_PAGES_FILE = ROOT / "public" / "today-pages.json"
KINDNESS_SLUGS_FILE = ROOT / "data" / "kindness-slugs.json"
GEN_LOG_FILE = ROOT / "data" / "generation-log.json"

# Cloudflare Workers AI, called via stdlib urllib (no SDK, no paid API — free tier).
# Writer is Kimi K2.6 with reasoning DISABLED (thinking:false): a model bake-off showed
# reasoning-on is slow, costly, and frequently returns no output, while reasoning-off is
# fast, reliable, and on-voice. Creds live only in env — CLOUDFLARE_API_TOKEN (a GitHub
# secret, needs Workers AI:Read) + CLOUDFLARE_ACCOUNT_ID (public; hardcoded in daily.yml).
# Missing token → degrade; the daily postcard never blanks.
WORKERS_AI_ACCOUNT = os.environ.get("CLOUDFLARE_ACCOUNT_ID", "")
WORKERS_AI_URL = "https://api.cloudflare.com/client/v4/accounts/{account}/ai/run/{model}"
WRITER_MODEL = "@cf/moonshotai/kimi-k2.6"
WRITER_PARAMS = {"chat_template_kwargs": {"thinking": False}}  # K2.6 reasoning off
# Independent grounding reviewer — a DIFFERENT, non-reasoning model (so it can't hit the
# token-blowup that disqualified reasoning models), chosen via bake-off: fast, clean JSON.
REVIEW_MODEL = "@cf/mistralai/mistral-small-3.1-24b-instruct"
WORKERS_AI_MAX_TOKENS = 1024
WORKERS_AI_TIMEOUT = 90  # Kimi-off is usually ~5s but can run ~25s; latency is fine here

# Content gates (plan §Decisions #5/#8b). A failure of any gate => skip that page
# and log; never blank the postcard, never hard-fail the run on live content.
HEADLINE_DISTANCE_MAX = 0.70   # our headline vs source title: ratio must be < this
STORY_MIN_WORDS = 60           # story-summary original-copy floor
GEN_LOG_DAYS = 180             # generation-log.json retention window

# Slugs are a forever-contract. These collide with real routes / file roots and
# must never be minted as a page slug (plan §Decisions #6c).
RESERVED_SLUGS = {
    "index", "sitemap", "robots", "llms", "feed", "story", "kindness",
    "joy", "joy-archive", "og-image", "stamp-square", "page",
}
DATE_SLUG_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")  # would shadow /YYYY-MM-DD route

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
# Denylist scope: research TITLES / feed items ONLY. Never strip words from a
# written paragraph. Memorial-adjacent hard-truth vocabulary (died/death/dead)
# is allowed in titles when the item is otherwise a clean kindness deed — voice
# permits hard truth next to joy. Violence / abuse / disaster terms still drop.
DENY_TERMS = [
    "kill", "killed", "fatal",
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


_DC = "{http://purl.org/dc/elements/1.1/}"  # dc:creator namespace


def clean_excerpt(raw: str) -> str:
    """Strip HTML tags and GNN's trailing "[…] The post … appeared first on …"
    boilerplate from a feed <description>, leaving plain intro prose to ground a
    summary on. Never the full article body — GNN doesn't syndicate that, and we
    never publish it; this excerpt is grounding input only."""
    if not raw:
        return ""
    txt = html.unescape(raw)
    txt = re.sub(r"<[^>]+>", " ", txt)
    # Cut GNN's syndication boilerplate (optionally preceded by a "[…]" marker).
    txt = re.sub(r"\s*(\[…\]\s*)?The post\b.*$", "", txt, flags=re.S)
    return re.sub(r"\s+", " ", txt).strip()


def parse_pub_date(raw: str) -> str:
    """RFC-822 pubDate -> ISO date string, or "" if absent/unparseable."""
    if not raw:
        return ""
    try:
        return parsedate_to_datetime(raw).date().isoformat()
    except (TypeError, ValueError):
        return ""


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
            items.append({
                "text": title,
                "url": canon(link),
                # Grounding + attribution fields for V3 story pages (transient;
                # never written into joy.json, which keeps its {text, url} shape).
                "excerpt": clean_excerpt(item.findtext("description") or ""),
                "creator": html.unescape((item.findtext(_DC + "creator") or "").strip()),
                "date": parse_pub_date(item.findtext("pubDate") or ""),
            })
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


# ===========================================================================
# V3 — content library: original-voice summaries + per-page static HTML + SEO
# All best-effort and AFTER the joy.json write; never blanks the daily postcard.
# ===========================================================================

def log_skip(reason: str, *, url: str = "", slug: str = "", date=None) -> None:
    """Record a skipped page (gate failure / API error) to generation-log.json,
    pruned to the last GEN_LOG_DAYS so it never grows unbounded. A solo operator
    can scan it to see what content was missed (GH Action logs are ephemeral)."""
    date = date or today_pt()
    log = load_json(GEN_LOG_FILE, default=[])
    if not isinstance(log, list):
        log = []
    log.append({"date": date.isoformat(), "reason": reason, "url": url, "slug": slug})
    cutoff = date.toordinal() - GEN_LOG_DAYS
    kept = []
    for e in log:
        try:
            d = datetime.strptime(e["date"], "%Y-%m-%d").date()
        except (KeyError, TypeError, ValueError):
            continue
        if d.toordinal() >= cutoff:
            kept.append(e)
    GEN_LOG_FILE.write_text(json.dumps(kept, indent=2) + "\n", encoding="utf-8")


# --- slugs (forever-contract: stable, collision-safe, reserved-aware) --------

def slugify(text: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return re.sub(r"-{2,}", "-", s)[:80].strip("-")


def _short_hash(s: str) -> str:
    return hashlib.sha1(s.encode("utf-8")).hexdigest()[:6]


def slug_from_url(url: str) -> str:
    """Story slug = the source URL's last path segment (GNN slugs are unique and
    human-readable), slugified. Reserved / date-like / empty bases get a stable
    hash suffix so they can never shadow a real route. Deterministic per URL."""
    seg = urlsplit(url).path.rstrip("/").rsplit("/", 1)[-1]
    base = slugify(seg)
    if not base or base in RESERVED_SLUGS or DATE_SLUG_RE.match(base):
        base = (base + "-" if base else "story-") + _short_hash(url)
    return base


def kindness_slug(task: str, ledger: dict) -> str:
    """Ledger is authoritative: a task text keeps its slug forever. New tasks mint
    a slug, resolving collisions with a numeric suffix, and are recorded."""
    if task in ledger:
        return ledger[task]
    base = slugify(task) or "kindness"
    if base in RESERVED_SLUGS or DATE_SLUG_RE.match(base):
        base = base + "-act"
    taken = set(ledger.values())
    slug, n = base, 2
    while slug in taken:
        slug, n = f"{base}-{n}", n + 1
    ledger[task] = slug
    return slug


# --- LLM summarization (stdlib urllib; grounded-only) ------------------------

def load_prompt(marker: str) -> str:
    """Extract a prompt template from voice.md, between
    `<!-- BEGIN {marker} -->` and `<!-- END {marker} -->`."""
    text = VOICE_FILE.read_text(encoding="utf-8")
    m = re.search(rf"<!-- BEGIN {marker} -->\n(.*?)\n<!-- END {marker} -->",
                  text, re.S)
    if not m:
        raise ValueError(f"prompt block '{marker}' not found in {VOICE_FILE}")
    return m.group(1).strip()


def call_workers_ai(prompt: str, model: str = WRITER_MODEL,
                    params: dict | None = None) -> str:
    """POST a single user message to Cloudflare Workers AI via urllib and return the
    text reply. Raises on missing creds or any transport/format error; callers treat
    that as a skip (degrade-safe). Free tier — no paid API."""
    token = os.environ.get("CLOUDFLARE_API_TOKEN")
    if not token or not WORKERS_AI_ACCOUNT:
        raise RuntimeError("CLOUDFLARE_API_TOKEN / CLOUDFLARE_ACCOUNT_ID not set")
    body = json.dumps({
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": WORKERS_AI_MAX_TOKENS,
        **(params or {}),
    }).encode("utf-8")
    url = WORKERS_AI_URL.format(account=WORKERS_AI_ACCOUNT, model=model)
    req = urllib.request.Request(url, data=body, method="POST", headers={
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "User-Agent": USER_AGENT,
    })
    try:
        with urllib.request.urlopen(req, timeout=WORKERS_AI_TIMEOUT) as resp:
            payload = json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        # Surface Cloudflare's actual error body (which permission / what's wrong)
        # instead of a bare "HTTP 401" — turn the failure into evidence.
        detail = exc.read().decode("utf-8", "replace")[:400]
        raise RuntimeError(f"workers-ai HTTP {exc.code} on {model}: {detail}") from None
    if not payload.get("success", False):
        raise ValueError(f"workers-ai error: {payload.get('errors')}")
    result = payload.get("result") or {}
    # Two response shapes: simple {response} or OpenAI-style {choices:[{message:{content}}]}.
    text = result.get("response")
    if not text:
        choices = result.get("choices") or []
        if choices:
            text = (choices[0].get("message") or {}).get("content") or ""
    if not text or not text.strip():
        raise ValueError("empty completion")
    return text


def parse_json_reply(text: str) -> dict:
    """The prompts demand a bare JSON object; tolerate stray prose / code fences."""
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", text, re.S)
        if not m:
            raise
        return json.loads(m.group(0))


# --- content gates (one canonical failure path: skip + log) ------------------

def numbers_in(text: str) -> set[str]:
    # Collapse thousands separators first so "30,000" == "30000" and "1,000,000"
    # matches "1000000" — otherwise faithful reformatting reads as a fabricated stat.
    text = re.sub(r"(?<=\d),(?=\d{3}(?:,\d{3})*\b)", "", text or "")
    return set(re.findall(r"\d+", text))


def groundedness_ok(summary: str, source: str) -> bool:
    """Every number in the summary must appear in the source (title+excerpt).
    Catches the most damaging, most detectable hallucination: invented stats.
    Thousands separators are normalized so reformatting isn't mistaken for one."""
    return numbers_in(summary) <= numbers_in(source)


def word_count(text: str) -> int:
    return len(re.findall(r"\w+", text or ""))


def headline_distinct(headline: str, source_title: str) -> bool:
    ratio = SequenceMatcher(None, norm_title(headline), norm_title(source_title)).ratio()
    return ratio < HEADLINE_DISTANCE_MAX


def summary_unique(summary: str, existing: list[str]) -> bool:
    ns = norm_title(summary)
    return all(
        SequenceMatcher(None, ns, norm_title(s)).ratio() <= SIMILARITY_THRESHOLD
        for s in existing
    )


def summarize_story(item: dict) -> dict | None:
    """Return {headline, summary, why} for a story, grounded strictly in its
    title + excerpt — or None if generation/grounding fails (caller skips+logs).
    GHI_LLM=off yields a deterministic, grounded placeholder for offline tests."""
    title, excerpt = item.get("text", ""), item.get("excerpt", "")
    if os.environ.get("GHI_LLM") == "off":
        return {
            "headline": "A little good news to brighten your day",
            "summary": excerpt or title,
            "why": "A small, real reminder that good things keep happening.",
        }
    raw = call_workers_ai(load_prompt("STORY PROMPT")
                          .replace("{{TITLE}}", title)
                          .replace("{{EXCERPT}}", excerpt),
                          model=WRITER_MODEL, params=WRITER_PARAMS)
    obj = parse_json_reply(raw)
    if not all(k in obj for k in ("headline", "summary", "why")):
        raise ValueError("story reply missing keys")
    return obj


def expand_kindness(task: str) -> dict | None:
    if os.environ.get("GHI_LLM") == "off":
        return {
            "title": task.rstrip("."),
            "why": ("Small kindnesses are the easiest good to do and the easiest to "
                    "skip. Naming one makes it likelier you actually do it today."),
            "ways": [task, "Do it for a stranger.", "Do it without being asked."],
        }
    raw = call_workers_ai(load_prompt("KINDNESS PROMPT").replace("{{TASK}}", task),
                          model=WRITER_MODEL, params=WRITER_PARAMS)
    obj = parse_json_reply(raw)
    if not all(k in obj for k in ("title", "why", "ways")):
        raise ValueError("kindness reply missing keys")
    return obj


def review_summary(item: dict, summary: dict) -> dict:
    """Independent grounding check by a DIFFERENT model (REVIEW_MODEL) than the writer:
    does the draft assert anything the source doesn't support? Catches the gap the
    deterministic number-gate can't — added facts / outside knowledge / invented
    mechanisms. Returns {"ok": bool, "reason": str}; fail-closed (unparseable or
    missing 'ok' => not ok => the caller skips+logs). GHI_LLM=off auto-passes."""
    if os.environ.get("GHI_LLM") == "off":
        return {"ok": True, "reason": "llm-off"}
    raw = call_workers_ai(
        load_prompt("REVIEW PROMPT")
        .replace("{{TITLE}}", item.get("text", ""))
        .replace("{{EXCERPT}}", item.get("excerpt", ""))
        .replace("{{HEADLINE}}", summary.get("headline", ""))
        .replace("{{SUMMARY}}", summary.get("summary", ""))
        .replace("{{WHY}}", summary.get("why", "")),
        model=REVIEW_MODEL)
    obj = parse_json_reply(raw)
    return {"ok": bool(obj.get("ok", False)), "reason": str(obj.get("reason", ""))}


# --- HTML rendering (shared brand stylesheet at /page.css) --------------------

_FAVICON = ("data:image/svg+xml,<svg xmlns=%22http://www.w3.org/2000/svg%22 "
            "viewBox=%220 0 100 100%22><text y=%22.9em%22 font-size=%2290%22>"
            "%F0%9F%8C%9E</text></svg>")


def esc(s: str) -> str:
    return html.escape(s or "", quote=True)


def _related(parent: Path, self_slug: str, label: str, base_path: str) -> str:
    slugs = sorted(p.parent.name for p in parent.glob("*/index.html")
                   if p.parent.name != self_slug)[:3]
    if not slugs:
        return ""
    items = "".join(
        f'<li><a href="{base_path}{esc(s)}/">{esc(s.replace("-", " "))}</a></li>'
        for s in slugs)
    return f'<nav class="related" aria-label="More {label}"><h2>More {label}</h2><ul>{items}</ul></nav>'


def _page_html(*, title: str, description: str, canonical: str,
               body: str, jsonld: dict) -> str:
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{esc(title)}</title>
<meta name="description" content="{esc(description)}">
<link rel="canonical" href="{esc(canonical)}">
<meta property="og:type" content="article">
<meta property="og:title" content="{esc(title)}">
<meta property="og:description" content="{esc(description)}">
<meta property="og:url" content="{esc(canonical)}">
<meta property="og:image" content="{SITE_URL}/og-image.png">
<meta name="twitter:card" content="summary_large_image">
<link rel="icon" href="{_FAVICON}">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Fraunces:opsz,wght@9..144,400;9..144,500;9..144,600&family=IBM+Plex+Mono:wght@400;500&display=swap">
<link rel="stylesheet" href="/page.css">
<script type="application/ld+json">{json.dumps(jsonld)}</script>
</head>
<body>
<main class="card">
{body}
</main>
<footer class="foot"><a href="/">gethappyinfo.com</a> · a daily postcard of good · <span class="sig">~ Get Happy</span></footer>
</body>
</html>
"""


def render_story_page(item: dict, summary: dict, date) -> str | None:
    """Write public/story/<slug>/index.html (generate-once). Returns the slug, or
    None if the story failed a content gate (skip + log)."""
    url, title = item.get("url", ""), item.get("text", "")
    slug = slug_from_url(url)
    out = STORY_DIR / slug / "index.html"
    if out.exists():
        return slug  # generate-once: never re-word a live URL

    headline = (summary.get("headline") or "").strip()
    body_summary = (summary.get("summary") or "").strip()
    why = (summary.get("why") or "").strip()
    source_text = f"{title} {item.get('excerpt', '')}"
    original_copy = f"{headline} {body_summary} {why}"  # all OUR words on the page
    existing = [m.get("summary", "") for m in iter_page_meta(STORY_DIR)]

    if not headline_distinct(headline, title):
        log_skip("headline-too-close", url=url, slug=slug, date=date); return None
    # No number anywhere in our copy may be absent from the source (anti-hallucination).
    if not groundedness_ok(original_copy, source_text):
        log_skip("groundedness", url=url, slug=slug, date=date); return None
    # Floor applies to total original copy (headline + summary + why), since a
    # grounded 2-4 sentence summary alone often runs under 60 words.
    if word_count(original_copy) < STORY_MIN_WORDS:
        log_skip("below-word-floor", url=url, slug=slug, date=date); return None
    if not summary_unique(body_summary, existing):
        log_skip("near-duplicate", url=url, slug=slug, date=date); return None

    canonical = f"{SITE_URL}/story/{slug}/"
    pub = item.get("date") or date.isoformat()
    creator = item.get("creator", "")
    credit = (f'Read the full story at <a href="{esc(url)}" rel="noopener nofollow" '
              f'target="_blank">Good News Network</a>'
              + (f" — reporting by {esc(creator)}." if creator else "."))
    body = (
        f'<p class="kicker">Good news · {esc(pub)}</p>\n'
        f'<h1>{esc(headline)}</h1>\n'
        f'<div class="summary"><p>{esc(body_summary)}</p></div>\n'
        f'<p class="why">{esc(why)}</p>\n'
        f'<p class="credit">{credit}</p>\n'
        f'<p class="home"><a href="/">← back to today\'s postcard</a></p>\n'
        + _related(STORY_DIR, slug, "good news", "/story/")
    )
    jsonld = {
        "@context": "https://schema.org", "@type": "NewsArticle",
        "headline": headline, "description": body_summary, "datePublished": pub,
        "url": canonical, "isBasedOn": url,
        "publisher": {"@type": "Organization", "name": "Get Happy", "url": SITE_URL},
    }
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(_page_html(title=f"{headline} — Get Happy",
                              description=body_summary[:155], canonical=canonical,
                              body=body, jsonld=jsonld), encoding="utf-8")
    return slug


def render_kindness_page(task: str, expanded: dict, ledger: dict) -> str | None:
    slug = kindness_slug(task, ledger)
    out = KINDNESS_DIR / slug / "index.html"
    if out.exists():
        return slug
    page_title = (expanded.get("title") or task).strip()
    why = (expanded.get("why") or "").strip()
    ways = [w.strip() for w in (expanded.get("ways") or []) if w.strip()]
    canonical = f"{SITE_URL}/kindness/{slug}/"
    ways_html = "".join(f"<li>{esc(w)}</li>" for w in ways)
    body = (
        f'<p class="kicker">A small kindness</p>\n'
        f'<h1>{esc(page_title)}</h1>\n'
        f'<p class="why">{esc(why)}</p>\n'
        + (f'<h2>Ways to try it today</h2>\n<ul class="ways">{ways_html}</ul>\n' if ways else "")
        + f'<p class="home"><a href="/">← today\'s postcard</a></p>\n'
        + _related(KINDNESS_DIR, slug, "kindnesses", "/kindness/")
    )
    jsonld = {
        "@context": "https://schema.org", "@type": "Article",
        "headline": page_title, "description": why, "url": canonical,
        "publisher": {"@type": "Organization", "name": "Get Happy", "url": SITE_URL},
    }
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(_page_html(title=f"{page_title} — Get Happy",
                              description=why[:155], canonical=canonical,
                              body=body, jsonld=jsonld), encoding="utf-8")
    return slug


# --- cumulative SEO + agent plumbing (regenerated from the page dirs) --------

def iter_page_meta(parent: Path):
    """Yield {slug, url, headline, summary} per published page by reading each
    page's JSON-LD — the page is the single source of truth, so sitemap/index/
    llms are always cumulative and in sync. Globs index.html (only real pages)."""
    base = "story" if parent == STORY_DIR else "kindness"
    for p in sorted(parent.glob("*/index.html")):
        m = re.search(r'<script type="application/ld\+json">(.*?)</script>', p.read_text(encoding="utf-8"), re.S)
        meta = {}
        if m:
            try:
                meta = json.loads(m.group(1))
            except json.JSONDecodeError:
                meta = {}
        slug = p.parent.name
        yield {
            "slug": slug,
            "url": f"{SITE_URL}/{base}/{slug}/",
            "headline": meta.get("headline", slug.replace("-", " ")),
            "summary": meta.get("description", ""),
        }


def write_sitemap() -> None:
    locs = [f"{SITE_URL}/"]
    locs += [m["url"] for m in iter_page_meta(STORY_DIR)]
    locs += [m["url"] for m in iter_page_meta(KINDNESS_DIR)]
    urls = "\n".join(f"  <url><loc>{esc(u)}</loc></url>" for u in locs)
    SITEMAP_FILE.write_text(
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
        f"{urls}\n</urlset>\n", encoding="utf-8")


def write_robots() -> None:
    ROBOTS_FILE.write_text(
        f"User-agent: *\nAllow: /\nSitemap: {SITE_URL}/sitemap.xml\n", encoding="utf-8")


def write_llms_txt() -> None:
    """llmstxt.org format: H1 + blockquote summary + linked sections."""
    lines = [
        "# Get Happy",
        "",
        "> A daily postcard of good news and small kindnesses from gethappyinfo.com — "
        "original, warm summaries of genuinely uplifting stories, plus an evergreen "
        "library of small kind things to try. No tracking, no signup.",
        "",
        "## Stories",
        "",
    ]
    for m in iter_page_meta(STORY_DIR):
        why = f": {m['summary']}" if m["summary"] else ""
        lines.append(f"- [{m['headline']}]({m['url']}){why}")
    lines += ["", "## Kindness", ""]
    for m in iter_page_meta(KINDNESS_DIR):
        lines.append(f"- [{m['headline']}]({m['url']})")
    LLMS_FILE.write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_index_json(date) -> None:
    """Machine-readable index for agents — cumulative, derived from the pages."""
    data = {
        "site": SITE_URL,
        "generated": date.isoformat(),
        "stories": [
            {"url": m["url"], "headline": m["headline"], "summary": m["summary"]}
            for m in iter_page_meta(STORY_DIR)
        ],
        "kindness": [
            {"url": m["url"], "title": m["headline"]}
            for m in iter_page_meta(KINDNESS_DIR)
        ],
    }
    INDEX_JSON_FILE.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def regenerate_plumbing(date) -> None:
    write_sitemap(); write_robots(); write_llms_txt(); write_index_json(date)


def generate_story_pages(picks: list[dict], date) -> dict:
    """Best-effort per-story page generation. Each story is independent: a failure
    skips+logs that one page and never propagates. Returns a {source_url:
    /story/slug/} map of pages that actually rendered (drives the postcard's
    internal links via today-pages.json)."""
    rendered: dict[str, str] = {}
    for item in picks:
        try:
            summary = summarize_story(item)
            if summary is None:
                continue
            verdict = review_summary(item, summary)  # independent grounding check
            if not verdict.get("ok"):
                print(f"[update_joy] story page failed review ({item.get('url','')}): "
                      f"{verdict.get('reason','')}", file=sys.stderr)
                log_skip(f"review:{verdict.get('reason','')[:60]}",
                         url=item.get("url", ""), date=date)
                continue
            slug = render_story_page(item, summary, date)
            if slug:
                rendered[item.get("url", "")] = f"/story/{slug}/"
        except Exception as exc:  # API/parse/IO — skip this story, keep going
            print(f"[update_joy] story page skipped ({item.get('url','')}): {exc}",
                  file=sys.stderr)
            log_skip(f"error:{type(exc).__name__}", url=item.get("url", ""), date=date)
    return rendered


def write_today_pages(date, story_map: dict, task: str) -> None:
    """Tiny map the postcard fetches to add internal links to today's freshly
    rendered pages — WITHOUT touching joy.json (which stays written-first and
    immutable). Degrade-safe: if it's absent/stale, the postcard just links out."""
    ledger = load_json(KINDNESS_SLUGS_FILE, default={})
    kindness = ""
    if isinstance(ledger, dict) and task in ledger:
        kindness = f"/kindness/{ledger[task]}/"
    TODAY_PAGES_FILE.write_text(json.dumps(
        {"date": date.isoformat(), "stories": story_map, "kindness": kindness},
        indent=2) + "\n", encoding="utf-8")


def build_kindness_batch() -> int:
    """One-time: generate a page for every micro-joy task in tasks.txt. Idempotent
    (generate-once + ledger). Review the output before committing."""
    tasks = [ln.strip() for ln in TASKS_FILE.read_text(encoding="utf-8").splitlines()
             if ln.strip() and not ln.lstrip().startswith("#")]
    ledger = load_json(KINDNESS_SLUGS_FILE, default={})
    if not isinstance(ledger, dict):
        ledger = {}
    rendered = 0
    for task in tasks:
        try:
            slug = kindness_slug(task, ledger)
            existed = (KINDNESS_DIR / slug / "index.html").exists()
            expanded = expand_kindness(task)
            if expanded and render_kindness_page(task, expanded, ledger) and not existed:
                rendered += 1  # count only NEW pages (generate-once skips re-writes)
        except Exception as exc:
            print(f"[update_joy] kindness page skipped ({task[:40]}…): {exc}",
                  file=sys.stderr)
            log_skip(f"kindness-error:{type(exc).__name__}", slug=slugify(task))
    KINDNESS_SLUGS_FILE.write_text(json.dumps(ledger, indent=2, sort_keys=True) + "\n",
                                   encoding="utf-8")
    regenerate_plumbing(today_pt())
    print(f"[update_joy] kindness batch: {rendered} new page(s); "
          f"ledger {len(ledger)} entries")
    return rendered



# ===========================================================================
# Postcard pipeline v4 — line + paragraph (+ optional seed)
# ===========================================================================

def is_new_shape(card) -> bool:
    """New-shape iff non-empty line AND non-empty paragraph (not date alone)."""
    if not isinstance(card, dict):
        return False
    line = card.get("line")
    paragraph = card.get("paragraph")
    return (
        isinstance(line, str) and bool(line.strip())
        and isinstance(paragraph, str) and bool(paragraph.strip())
    )


def is_old_shape(card) -> bool:
    if is_new_shape(card):
        return False
    if not isinstance(card, dict):
        return False
    return bool(card.get("lastUpdated") or card.get("dailyTask"))


def actions_warning(msg: str) -> None:
    """Conspicuous degrade log — GitHub Actions warning annotation + stderr."""
    print(f"::warning::[update_joy] {msg}")
    print(f"[update_joy] WARNING: {msg}", file=sys.stderr)


def escape_md_line(line: str) -> str:
    """Escape markdown-significant chars from tasks.txt before {{LINE}} sub."""
    out = []
    for ch in line:
        if ch in "*_`[]":
            out.append("\\" + ch)
        else:
            out.append(ch)
    return "".join(out)


def render_voice_fallback(line: str) -> str:
    """Substitute {{LINE}} in scripts/voice-fallback.md; return paragraph body.

    The published paragraph must include today's line as the concrete deed.
    Not model output. Invents no seed.
    """
    raw = VOICE_FALLBACK_FILE.read_text(encoding="utf-8")
    # Drop markdown preamble above the first horizontal rule; keep craft body.
    if "\n---\n" in raw:
        body = raw.split("\n---\n", 1)[1].strip()
    else:
        body = raw.strip()
    # Skip leading blank / heading-only leftovers
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", body) if p.strip()]
    # Prefer the chunk that contains the slot; else last chunk.
    chosen = next((p for p in paragraphs if "{{LINE}}" in p), paragraphs[-1] if paragraphs else body)
    # If the chosen chunk still starts with a markdown heading, strip heading lines.
    lines_out = []
    for ln in chosen.splitlines():
        if ln.lstrip().startswith("#"):
            continue
        lines_out.append(ln)
    tmpl = " ".join(x.strip() for x in lines_out if x.strip())
    return tmpl.replace("{{LINE}}", escape_md_line(line)).strip()


def pick_seed(by_topic: dict[str, list[dict]], recent_urls: set[str],
              date) -> dict | None:
    """Pick ONE research seed {summary, sourceUrl, sourceTitle} or None.

    Prefer inspiring, then animals, then other topic feeds. Denylist is
    title-scoped only. 14-day URL dedup via recent_urls. A miss is allowed.
    """
    topic_order = ["inspiring", "animals", "health", "earth", "science"]
    # Also include any unexpected topics last
    for t in by_topic:
        if t not in topic_order:
            topic_order.append(t)

    for topic in topic_order:
        for it in by_topic.get(topic, []):
            url = it.get("url", "")
            title = it.get("text", "")
            if not url or not title:
                continue
            if url in recent_urls:
                continue
            # Title/feed denylist only — do not inspect excerpt for deny terms
            # beyond what's needed for a clean headline gate.
            if is_denylisted(title):
                continue
            excerpt = (it.get("excerpt") or "").strip()
            summary = excerpt or title
            # Keep summary plain and short
            if len(summary) > 400:
                summary = summary[:397].rstrip() + "…"
            return {
                "summary": summary,
                "sourceUrl": url,
                "sourceTitle": title,
            }
    return None


def workers_ai_configured() -> bool:
    token = os.environ.get("CLOUDFLARE_API_TOKEN") or ""
    return bool(token.strip() and (WORKERS_AI_ACCOUNT or "").strip())


def write_postcard_paragraph(line: str, seed: dict | None) -> str | None:
    """Writer mode: return paragraph text or None on failure.

    GHI_LLM=off yields a deterministic offline paragraph for tests.
    """
    if os.environ.get("GHI_LLM") == "off":
        if seed:
            return (
                f"Here is what landed today. {seed.get('summary', '').strip()} "
                f"It pairs with a small ask: {line} That was enough for one card."
            )
        return (
            f"Someone meant to skip it and did it anyway. {line} "
            "It took almost nothing. The day shifted half a degree. "
            "They went on lighter for having refused to wait."
        )
    if not workers_ai_configured():
        raise RuntimeError("CLOUDFLARE_API_TOKEN / CLOUDFLARE_ACCOUNT_ID not set")

    if seed:
        seed_block = (
            "SEED (use ONLY these facts; invent nothing beyond them):\n"
            f"- summary: {seed.get('summary', '')}\n"
            f"- sourceTitle: {seed.get('sourceTitle', '')}\n"
            f"- sourceUrl: {seed.get('sourceUrl', '')}"
        )
    else:
        seed_block = (
            "No SEED today (research miss). Write an imagined scene from the "
            "line only — invent no real biography, public figure, org, news event, "
            "or source URL."
        )
    prompt = (
        load_prompt("POSTCARD PROMPT")
        .replace("{{LINE}}", line)
        .replace("{{SEED_BLOCK}}", seed_block)
    )
    raw = call_workers_ai(prompt, model=WRITER_MODEL, params=WRITER_PARAMS)
    obj = parse_json_reply(raw)
    para = (obj.get("paragraph") or "").strip()
    if not para:
        raise ValueError("postcard reply missing paragraph")
    return para


def review_postcard(paragraph: str, *, line: str, seed: dict | None) -> dict:
    """Two-mode grounding. Seeded vs no-seed use different prompts — never one for both."""
    if os.environ.get("GHI_LLM") == "off":
        # Deterministic offline gate used by tests (can be monkeypatched).
        return {"ok": True, "reason": "llm-off"}
    if not workers_ai_configured():
        raise RuntimeError("CLOUDFLARE_API_TOKEN / CLOUDFLARE_ACCOUNT_ID not set")

    if seed:
        prompt = (
            load_prompt("POSTCARD REVIEW SEEDED")
            .replace("{{SUMMARY}}", seed.get("summary", ""))
            .replace("{{SOURCE_TITLE}}", seed.get("sourceTitle", ""))
            .replace("{{PARAGRAPH}}", paragraph)
        )
    else:
        prompt = (
            load_prompt("POSTCARD REVIEW NOSEED")
            .replace("{{LINE}}", line)
            .replace("{{PARAGRAPH}}", paragraph)
        )
    raw = call_workers_ai(prompt, model=REVIEW_MODEL)
    obj = parse_json_reply(raw)
    return {"ok": bool(obj.get("ok", False)), "reason": str(obj.get("reason", ""))}


def find_last_good_card(existing, archive: dict) -> dict | None:
    """Most recent new-shape card (matching line+paragraph[+seed] unit)."""
    if is_new_shape(existing):
        return existing
    days = archive.get("days") if isinstance(archive, dict) else None
    if not isinstance(days, dict):
        return None
    for key in sorted(days.keys(), reverse=True):
        card = days[key]
        if is_new_shape(card):
            return card
    return None


def card_unit(card: dict, date_iso: str) -> dict:
    """Build published new-shape card; copy seed only when present and complete."""
    out = {
        "date": date_iso,
        "line": card["line"],
        "paragraph": card["paragraph"],
    }
    seed = card.get("seed")
    if isinstance(seed, dict):
        summary = (seed.get("summary") or "").strip()
        url = (seed.get("sourceUrl") or "").strip()
        title = (seed.get("sourceTitle") or "").strip()
        if summary and url and title:
            out["seed"] = {
                "summary": summary,
                "sourceUrl": url,
                "sourceTitle": title,
            }
    return out


def write_archive_day(archive: dict, date_iso: str, joy: dict) -> dict:
    archive.setdefault("days", {})
    archive["days"][date_iso] = joy
    ARCHIVE_FILE.write_text(
        json.dumps(archive, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return archive


def load_archive_or_abort() -> tuple[dict, int | None]:
    """Return (archive, error_rc). error_rc is 1 if corrupt existing file."""
    if ARCHIVE_FILE.exists():
        try:
            archive = json.loads(ARCHIVE_FILE.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            print(
                f"[update_joy] ERROR: {ARCHIVE_FILE.relative_to(ROOT)} is corrupt "
                f"({exc}); refusing to overwrite and lose history",
                file=sys.stderr,
            )
            return {}, 1
        if not isinstance(archive, dict) or not isinstance(archive.get("days"), dict):
            print(
                f"[update_joy] ERROR: {ARCHIVE_FILE.relative_to(ROOT)} has an "
                "unexpected shape; refusing to overwrite and lose history",
                file=sys.stderr,
            )
            return {}, 1
        return archive, None
    return {"days": {}}, None


def main(force: bool = False) -> int:
    date = today_pt()
    date_iso = date.isoformat()
    line = pick_task(date)
    existing = load_json(JOY_FILE, default=None)

    # Idempotency: if today's new-shape card is already written, no-op
    # (Cloudflare cron + GH backup). --force overrides.
    if (not force and is_new_shape(existing)
            and existing.get("date") == date_iso):
        print(f"[update_joy] {date_iso} already current (new-shape) — nothing to do "
              "(use --force to override)")
        return 0

    archive, arch_err = load_archive_or_abort()
    if arch_err:
        return arch_err

    # --- Research (one seed; miss allowed) ---------------------------------
    by_topic = fetch_all(FEEDS)
    recent = prune_recent(load_json(RECENT_FILE, default=[]), date)
    recent_urls = {e["url"] for e in recent if isinstance(e, dict) and "url" in e}
    seed = pick_seed(by_topic, recent_urls, date)
    if seed:
        recent.append({"url": seed["sourceUrl"], "date": date_iso})
        RECENT_FILE.write_text(json.dumps(recent, indent=2) + "\n", encoding="utf-8")
        print(f"[update_joy] research seed: {seed['sourceTitle'][:80]!r}")
    else:
        print("[update_joy] research miss — continuing with today's line only (no seed)")

    # --- Writer + grounding (optional AI) ----------------------------------
    paragraph = None
    used_seed = seed
    degrade_reason = None

    if not workers_ai_configured() and os.environ.get("GHI_LLM") != "off":
        actions_warning(
            "CLOUDFLARE_API_TOKEN or CLOUDFLARE_ACCOUNT_ID missing — "
            "skipping writer; taking degrade path (postcard still ships)"
        )
        degrade_reason = "missing-cloudflare-creds"
    else:
        try:
            draft = write_postcard_paragraph(line, seed)
            if not draft:
                degrade_reason = "writer-empty"
            else:
                verdict = review_postcard(draft, line=line, seed=seed)
                if not verdict.get("ok"):
                    degrade_reason = f"grounding:{verdict.get('reason', '')[:80]}"
                    actions_warning(
                        f"grounding rejected draft ({verdict.get('reason', '')}); "
                        "will reuse last-good or fallback"
                    )
                else:
                    paragraph = draft
        except Exception as exc:
            degrade_reason = f"writer-error:{type(exc).__name__}"
            actions_warning(
                f"writer/grounding failed ({type(exc).__name__}: {exc}); "
                "will reuse last-good or fallback"
            )

    joy = None
    if paragraph:
        joy = {"date": date_iso, "line": line, "paragraph": paragraph}
        if used_seed:
            joy["seed"] = used_seed
        print(f"[update_joy] wrote new matching card for {date_iso}")
    else:
        last = find_last_good_card(existing, archive)
        if last:
            joy = card_unit(last, date_iso)
            actions_warning(
                f"reuse last-good matching card (reason={degrade_reason or 'unknown'}); "
                f"date advanced to {date_iso}; line+paragraph+seed reused as a unit — "
                "share text will repeat intentionally"
            )
        else:
            # First-run / wipe / only old-shape history
            fb = render_voice_fallback(line)
            joy = {"date": date_iso, "line": line, "paragraph": fb}
            actions_warning(
                f"first-run / no last-good new-shape card (reason={degrade_reason or 'no-history'}); "
                f"using voice-fallback.md for line={line[:60]!r}"
            )

    # Never publish topNews / dailyTask / ps on new days
    for banned in ("topNews", "dailyTask", "ps", "lastUpdated"):
        joy.pop(banned, None)

    JOY_FILE.write_text(json.dumps(joy, indent=2) + "\n", encoding="utf-8")
    print(f"[update_joy] wrote {JOY_FILE.relative_to(ROOT)} — {date_iso}")

    write_archive_day(archive, date_iso, joy)
    print(f"[update_joy] archive: {len(archive['days'])} day(s) total")

    # ---- V3 story/kindness generation STOPPED (postcard pipeline v4) ----
    # Leave existing public/story and public/kindness files in place. Do NOT
    # call generate_story_pages / build_kindness_batch / write_today_pages from
    # the daily path. A later reader must not "fix" generation back on without
    # a new product decision — the live postcard no longer depends on V3 pages.
    # (Helpers remain in this file for offline tests and one-shot --build-kindness.)
    try:
        regenerate_plumbing(date)
    except Exception as exc:
        print(f"[update_joy] plumbing refresh error (postcard unaffected): {exc}",
              file=sys.stderr)
    return 0


if __name__ == "__main__":
    args = sys.argv[1:]
    if "--build-kindness" in args:
        # Manual one-shot only; not part of the daily postcard path (v4).
        sys.exit(0 if build_kindness_batch() >= 0 else 1)
    sys.exit(main(force="--force" in args))
