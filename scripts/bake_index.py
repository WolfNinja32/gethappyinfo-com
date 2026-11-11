#!/usr/bin/env python3
"""Bake today's card into public/index.html as static HTML + JSON-LD (no-JS readable).

Why (Casey's weekly site check, 2026-11-11, P1): a no-JS fetch of gethappyinfo.com saw
only ~370 characters of text ("Loading a little joy…"), and the page had no JSON-LD.
Crawlers and agents don't run the page's JS, so the day's content has to be in the HTML.

What: regions of public/index.html, each between marker comments, are rewritten
from public/joy.json (fields copied word for word, HTML-escaped; nothing is invented):

    <!-- joy:jsonld:start --> ... <!-- joy:jsonld:end -->   in <head>
        <script type="application/ld+json"> @graph: WebSite + Organization ("Get Happy",
        https://gethappyinfo.com/) + the day's CreativeWork (headline = line,
        text/description = paragraph, datePublished = date, url = /<date>,
        image = art/<date>-landscape.png / -portrait.png only if those files exist,
        isBasedOn = seed sourceTitle/sourceUrl if present).
    <!-- joy:line:start --> ... <!-- joy:line:end -->       inside <p id="task">
        the day's line (the JS sets the same text with textContent).
    <!-- joy:story:start --> ... <!-- joy:story:end -->     inside <ul id="news">
        the exact <li> markup the JS renderNew() writes: paragraph + "Story source" link.
    <!-- joy:postmark:start --> ... <!-- joy:postmark:end -->   inside #postmark-date
        month + day in the same format as the page JS dateParts()/applyPostmark()
        (JAN–DEC, day with no leading zero, e.g. "NOV 11" or "MAY 7") from joy.json "date".
    <!-- joy:postmark-year:start --> ... <!-- joy:postmark-year:end -->  inside #postmark-year
        the four-digit year from that same date.

The JS still fetches joy.json / joy-archive.json and overwrites #task, #news, #postmark-date
and #postmark-year exactly as before, so with JS nothing changes visually (it replaces
identical content); without JS the reader sees the real card and today's postmark instead
of "Loading a little joy…" and the launch-day stamp "MAY 27".

If the day has no line+paragraph (old-shape or missing data), the line region falls back
to the original placeholder, the story region is emptied and no CreativeWork is emitted.

scripts/build_og_page.py reuses apply_day() so each dated page /<date> carries ITS OWN
day's content, CreativeWork, canonical and postmark (never today's).

Usage:
  python scripts/bake_index.py               # rewrite public/index.html from joy.json (only if changed)
  python scripts/bake_index.py --git-index   # (workflow) images count only if staged/tracked with same bytes
  python scripts/bake_index.py --check       # exit 1 if index.html is stale; write nothing
Stdlib only. Idempotent.
"""
from __future__ import annotations

import argparse
import html
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlparse

REPO = Path(__file__).resolve().parent.parent
SITE = "https://gethappyinfo.com"
SITE_NAME = "Get Happy"
SITE_TITLE = "Get Happy Info"
PLACEHOLDER_LINE = "Loading a little joy…"
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
CANONICAL_RE = re.compile(r'<link\s+rel="canonical"\s+href="[^"]*"\s*/?>')

# Same month names and day formatting as the page JS dateParts() (no timezone).
POSTMARK_MONTHS = ("JAN", "FEB", "MAR", "APR", "MAY", "JUN",
                   "JUL", "AUG", "SEP", "OCT", "NOV", "DEC")


def postmark_labels(date: str | None) -> tuple[str, str] | None:
    """(month-day, year) matching JS applyPostmark(), or None if `date` is not YYYY-MM-DD.

    JS: months = ['JAN',..,'DEC']; day = parseInt(m[3], 10) (no leading zero);
    #postmark-date = monthShort + ' ' + day; #postmark-year = year.
    """
    if not (isinstance(date, str) and DATE_RE.match(date)):
        return None
    year, month, day = date.split("-")
    mi, di = int(month), int(day)
    if not (1 <= mi <= 12 and 1 <= di <= 31):
        return None
    return f"{POSTMARK_MONTHS[mi - 1]} {di}", year



class BakeError(Exception):
    pass


def _region_re(name: str) -> re.Pattern:
    return re.compile(r"(<!-- joy:%s:start -->)(.*?)(<!-- joy:%s:end -->)" % (name, name), re.S)


def is_new_shape(d) -> bool:
    """Same gate as the page JS isNewShape(): non-empty string line AND paragraph."""
    return bool(isinstance(d, dict)
                and isinstance(d.get("line"), str) and d["line"].strip()
                and isinstance(d.get("paragraph"), str) and d["paragraph"].strip())


def day_data(repo: Path, date: str) -> dict | None:
    """The day's card: public/joy.json if it is that date, else joy-archive.json days[date]."""
    pub = repo / "public"
    try:
        joy = json.loads((pub / "joy.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        joy = {}
    if isinstance(joy, dict) and joy.get("date") == date:
        return joy
    try:
        day = json.loads((pub / "joy-archive.json").read_text(encoding="utf-8"))["days"][date]
    except (OSError, ValueError, KeyError, TypeError):
        return None
    return day if isinstance(day, dict) else None


# Whitespace (Unicode + \s) or C0 / DEL control characters — rejected in URLs.
_UNSAFE_URL_CHAR = re.compile(r"[\s\x00-\x1f\x7f]")
WORKFLOW_MSG_LIMIT = 300


def safe_url(u) -> str | None:
    """http(s) only, with a hostname; rejects whitespace and control characters.

    Like the page JS safeUrl(), but parsed with urllib.parse rather than a prefix
    match, so a newline or other control character cannot sneak past https://.
    Returns the original string (no normalization) when it is acceptable.
    """
    if u is None:
        return None
    u = str(u)
    if not u or _UNSAFE_URL_CHAR.search(u):
        return None
    try:
        parsed = urlparse(u)
    except ValueError:
        return None
    if parsed.scheme.lower() not in ("http", "https"):
        return None
    if not parsed.hostname:
        return None
    return u


def sanitize_workflow_message(msg: object, limit: int = WORKFLOW_MSG_LIMIT) -> str:
    """Make text safe to interpolate into a GitHub Actions workflow command.

    Collapse all whitespace (including newlines) to single spaces, then escape
    '%', leftover CR/LF, and '::' (command injection). Truncate to `limit`.
    """
    s = " ".join(str(msg).split())
    s = (s.replace("%", "%25")
          .replace("\r", "%0D")
          .replace("\n", "%0A")
          .replace("::", "%3A%3A"))
    return s[:limit] if len(s) > limit else s


def git_index_has(repo: Path, path: Path) -> bool:
    """True iff `path` is in the git index with exactly its working-tree bytes."""
    try:
        rel = path.resolve().relative_to(repo.resolve()).as_posix()
        staged = subprocess.run(["git", "ls-files", "-s", "--", rel], cwd=repo,
                                capture_output=True, text=True, check=True).stdout.split()
        disk = subprocess.run(["git", "hash-object", "--", rel], cwd=repo,
                              capture_output=True, text=True, check=True).stdout.strip()
    except (subprocess.CalledProcessError, OSError, ValueError):
        return False
    return len(staged) >= 2 and staged[1] == disk


def day_images(repo: Path, date: str, git_index: bool = False, art_dir: Path | None = None) -> list[str]:
    """Absolute URLs of this day's existing art files (landscape first)."""
    out = []
    art_dir = art_dir if art_dir is not None else repo / "public" / "art"
    for kind in ("landscape", "portrait"):
        p = art_dir / f"{date}-{kind}.png"
        if p.is_file() and p.stat().st_size > 0 and (not git_index or git_index_has(repo, p)):
            out.append(f"{SITE}/art/{date}-{kind}.png")
    return out


def json_ld(date: str | None, day: dict | None, images: list[str], description: str | None) -> str:
    website = {"@type": "WebSite", "@id": f"{SITE}/#website", "url": f"{SITE}/",
               "name": SITE_NAME, "alternateName": SITE_TITLE,
               "publisher": {"@id": f"{SITE}/#organization"}}
    if description:
        website["description"] = description
    org = {"@type": "Organization", "@id": f"{SITE}/#organization", "name": SITE_NAME, "url": f"{SITE}/"}
    graph = [website, org]
    if date and is_new_shape(day):
        line = " ".join(day["line"].split())
        para = " ".join(day["paragraph"].split())
        work = {"@type": "CreativeWork", "@id": f"{SITE}/{date}#card", "url": f"{SITE}/{date}",
                "headline": line, "text": para, "description": para, "datePublished": date,
                "inLanguage": "en", "isPartOf": {"@id": f"{SITE}/#website"},
                "publisher": {"@id": f"{SITE}/#organization"}}
        if images:
            work["image"] = images[0] if len(images) == 1 else images
        seed = day.get("seed") if isinstance(day.get("seed"), dict) else {}
        src = safe_url(seed.get("sourceUrl"))
        if src:
            based = {"@type": "CreativeWork", "url": src}
            if str(seed.get("sourceTitle") or "").strip():
                based["name"] = " ".join(str(seed["sourceTitle"]).split())
            work["isBasedOn"] = based
        graph.append(work)
    data = json.dumps({"@context": "https://schema.org", "@graph": graph},
                      ensure_ascii=False, indent=2)
    # never let content close the <script> element or open a comment
    data = data.replace("&", "\\u0026").replace("<", "\\u003c").replace(">", "\\u003e")
    return data


def story_html(day: dict | None) -> str:
    """Exactly the markup renderNew() puts into #news (paragraph + optional credit)."""
    if not is_new_shape(day):
        return ""
    para = '<p class="kind-paragraph">' + html.escape(day["paragraph"], quote=True) + "</p>"
    credit = ""
    seed = day.get("seed") if isinstance(day.get("seed"), dict) else {}
    src = safe_url(seed.get("sourceUrl"))
    if seed.get("sourceTitle") and src:
        credit = ('<p class="seed-credit"><a href="' + html.escape(src, quote=True)
                  + '" target="_blank" rel="noopener noreferrer">Story source</a></p>')
    return '<li style="border:0;padding:0;opacity:1;animation:none">' + para + credit + "</li>"


def _sub_region(text: str, name: str, body: str) -> str:
    rx = _region_re(name)
    if not rx.search(text):
        return text
    return rx.sub(lambda m: m.group(1) + body + m.group(3), text, count=1)


def apply_day(page: str, date: str | None, day: dict | None, *, images: list[str] | None = None,
              canonical: str | None = None) -> str:
    """Rewrite the joy:* marker regions (and canonical href, if given) for `date`/`day`.

    Regions whose markers are absent are left alone, so this is safe on any HTML.
    """
    m = re.search(r'<meta\s+name="description"\s+content="([^"]*)"', page)
    desc = html.unescape(m.group(1)) if m else None
    ld = json_ld(date, day, images or [], desc)
    indent = "  "
    page = _sub_region(page, "jsonld",
                       f'\n{indent}<script type="application/ld+json">\n{ld}\n{indent}</script>\n{indent}')
    line = " ".join(day["line"].split()) if is_new_shape(day) else PLACEHOLDER_LINE
    page = _sub_region(page, "line", html.escape(line, quote=False))
    page = _sub_region(page, "story", story_html(day))
    labels = postmark_labels(date)
    if labels:
        page = _sub_region(page, "postmark", html.escape(labels[0], quote=False))
        page = _sub_region(page, "postmark-year", html.escape(labels[1], quote=False))
    if canonical:
        page = CANONICAL_RE.sub(f'<link rel="canonical" href="{html.escape(canonical, quote=True)}" />',
                                page, count=1)
    return page


def bake_index(repo: Path, git_index: bool = False) -> tuple[str, str]:
    """Return (new index.html text, mode). Writes nothing."""
    path = repo / "public" / "index.html"
    page = path.read_text(encoding="utf-8")
    for name in ("jsonld", "line", "story", "postmark", "postmark-year"):
        if not _region_re(name).search(page):
            raise BakeError(f"public/index.html is missing the joy:{name} markers")
    joy = json.loads((repo / "public" / "joy.json").read_text(encoding="utf-8"))
    date = joy.get("date") if isinstance(joy, dict) else None
    if not (isinstance(date, str) and DATE_RE.match(date)):
        date = None
    day = joy if date and is_new_shape(joy) else None
    images = day_images(repo, date, git_index) if day else []
    mode = f"{date}: line+story+CreativeWork ({len(images)} image(s))" if day else \
        "joy.json has no line+paragraph; placeholder kept, WebSite+Organization only"
    return apply_day(page, date, day, images=images, canonical=f"{SITE}/"), mode


def write_if_changed(path: Path, text: str) -> bool:
    data = text.encode("utf-8")
    if path.is_file() and path.read_bytes() == data:
        return False
    tmp = path.with_name(f".{path.name}.tmp")
    try:
        tmp.write_bytes(data)
        os.replace(tmp, path)
    finally:
        if tmp.exists():
            tmp.unlink()
    return True


def main(argv: list[str] | None = None, repo: Path = REPO) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--git-index", action="store_true",
                    help="art images count only if staged/tracked with the same bytes")
    ap.add_argument("--check", action="store_true", help="exit 1 if index.html is stale; write nothing")
    args = ap.parse_args(argv)
    path = repo / "public" / "index.html"
    try:
        page, mode = bake_index(repo, git_index=args.git_index)
    except (BakeError, OSError, ValueError) as e:
        print(f"::warning::index bake failed: {sanitize_workflow_message(f'{type(e).__name__}: {e}')}", file=sys.stderr)
        return 1
    if args.check:
        stale = path.read_bytes() != page.encode("utf-8")
        print(f"[bake_index] {'STALE' if stale else 'fresh'}: {mode}")
        return 1 if stale else 0
    changed = write_if_changed(path, page)
    print(f"[bake_index] {'wrote' if changed else 'no change'} {path} ({mode})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
