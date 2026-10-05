#!/usr/bin/env python3
"""Bake the dated share page public/<date>.html with per-day link-preview tags.

Why: today.txt (and the site's Send button) share https://gethappyinfo.com/YYYY-MM-DD.
The site is a Cloudflare Workers static-assets deploy of public/ (wrangler.jsonc,
no worker script) with not_found_handling = "single-page-application", so a dated
URL with no file behind it returns the homepage index.html (HTTP 200) with the
homepage's og:* tags. Crawlers (Facebook, iMessage, Slack, X...) don't run JS,
so per-day tags must be in the HTML returned for that URL.

How: with the default html_handling ("auto-trailing-slash"), a file
public/2026-10-04.html is served at /2026-10-04 with HTTP 200 and no redirect
(/2026-10-04.html and /2026-10-04/ 307 to /2026-10-04). This script copies
public/index.html byte for byte and only swaps the og:* / twitter:* <meta> block
in <head>. The page's JS already reads the date from the path
(/^\\/(\\d{4}-\\d{2}-\\d{2})\\/?$/) and loads that day from /joy-archive.json, exactly
as it does today under the SPA fallback; nothing else on the page changes.

Tags (absolute URLs):

    og:title        the day's line (public/joy.json "line" for the joy.json date,
                    else public/joy-archive.json days[date].line), word for word
    og:description  "Pass it on ✉" (the today.txt sign-off, build_fb_handoff.SIGN_OFF)
    og:type         website
    og:url          https://gethappyinfo.com/<date>
    og:image        https://gethappyinfo.com/art/<date>-landscape.png   (landscape)
    og:image:width  1200
    og:image:height 630
    twitter:card    summary_large_image
    twitter:image   same as og:image

Landscape tags are used ONLY if public/art/<date>-landscape.png really rendered:
the file exists, is a structurally valid PNG (signature, chunk CRCs, IHDR first,
IDAT inflates, IEND last) and is exactly 1200x630; with --git-index it must also
be in the git index with exactly those bytes, i.e. it ships in the same commit as
the page. Otherwise the page falls back to whatever og:image public/index.html
uses (today https://gethappyinfo.com/og-image.png) with that file's real
dimensions read from the PNG (width/height omitted if they can't be read).
If the day has no line, og:title / og:description stay index.html's own text
(nothing is invented).

Runs in the nightly workflow's commit step AFTER the share/landscape outputs are
staged (`--git-index`), so a landscape failure (file deleted by
scripts/landscape_step.sh) simply yields fallback tags, and a failure of this
script only warns: it can never block the joy.json / share / portrait / today.txt
commit.

Usage:
  python scripts/build_og_page.py                 # joy.json date + refresh every existing public/YYYY-MM-DD.html
  python scripts/build_og_page.py --git-index     # (workflow) landscape must be staged/tracked with the same bytes
  python scripts/build_og_page.py --date 2026-10-04          # just that date
  python scripts/build_og_page.py --out-dir /tmp/x --art-dir /tmp/x/art   # dry run: /tmp/x/<date>.html
  python scripts/build_og_page.py --no-landscape  # force the fallback tags
  python scripts/build_og_page.py --check         # exit 1 if any target page is stale; write nothing
Stdlib only. Idempotent: pages are only rewritten when their content changes.
"""
from __future__ import annotations

import argparse
import datetime
import html
import json
import os
import re
import struct
import subprocess
import sys
import zlib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from build_fb_handoff import SIGN_OFF  # noqa: E402  (single source for "Pass it on ✉")

REPO = Path(__file__).resolve().parent.parent
SITE = "https://gethappyinfo.com"
LANDSCAPE_SIZE = (1200, 630)
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
PAGE_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})\.html$")
# one og:* / twitter:* meta tag on its own line (index.html keeps one per line)
META_RE = re.compile(
    r'^[ \t]*<meta\s+(?:property|name)="(?:og|twitter):[^"]*"[^>]*>[ \t]*\r?\n', re.M)
OG_IMAGE_RE = re.compile(r'<meta\s+property="og:image"\s+content="([^"]*)"')
PNG_SIG = b"\x89PNG\r\n\x1a\n"


class PageError(Exception):
    pass


# ---------------------------------------------------------------- PNG check
def png_size(path: Path) -> tuple[int, int] | None:
    """(width, height) if `path` is a structurally valid PNG, else None."""
    try:
        data = path.read_bytes()
    except OSError:
        return None
    if not data.startswith(PNG_SIG):
        return None
    pos, size, seen_idat, inflater, first = len(PNG_SIG), None, False, zlib.decompressobj(), True
    try:
        while pos + 12 <= len(data):
            (length,) = struct.unpack(">I", data[pos:pos + 4])
            ctype = data[pos + 4:pos + 8]
            body = data[pos + 8:pos + 8 + length]
            if len(body) != length or pos + 12 + length > len(data):
                return None
            (crc,) = struct.unpack(">I", data[pos + 8 + length:pos + 12 + length])
            if zlib.crc32(ctype + body) & 0xFFFFFFFF != crc:
                return None
            if first:
                if ctype != b"IHDR" or length != 13:
                    return None
                size = struct.unpack(">II", body[:8])
                first = False
            elif ctype == b"IDAT":
                seen_idat = True
                inflater.decompress(body, 0)
            elif ctype == b"IEND":
                ok = seen_idat and inflater.eof and size and size[0] > 0 and size[1] > 0
                return tuple(size) if ok and pos + 12 + length == len(data) else None
            pos += 12 + length
    except (zlib.error, struct.error):
        return None
    return None  # no IEND


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


def landscape_status(repo: Path, art_dir: Path, date: str, git_index: bool) -> tuple[bool, str]:
    p = art_dir / f"{date}-landscape.png"
    if not p.is_file():
        return False, f"no {p.name}"
    size = png_size(p)
    if size is None:
        return False, f"{p.name} is not a valid PNG"
    if size != LANDSCAPE_SIZE:
        return False, f"{p.name} is {size[0]}x{size[1]}, not 1200x630"
    if git_index and not git_index_has(repo, p):
        return False, f"{p.name} is not staged/tracked with these bytes (would not ship in this commit)"
    return True, "landscape"


# ---------------------------------------------------------------- tags
def day_line(repo: Path, date: str) -> str | None:
    """The day's line, word for word: joy.json for its date, else joy-archive.json."""
    pub = repo / "public"
    try:
        joy = json.loads((pub / "joy.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        joy = {}
    if joy.get("date") == date and str(joy.get("line") or "").strip():
        return " ".join(str(joy["line"]).split())
    try:
        day = json.loads((pub / "joy-archive.json").read_text(encoding="utf-8"))["days"][date]
    except (OSError, ValueError, KeyError, TypeError):
        return None
    line = day.get("line") if isinstance(day, dict) else None
    return " ".join(str(line).split()) if line and str(line).strip() else None


def index_meta(index_html: str, key: str) -> str | None:
    m = re.search(r'<meta\s+(?:property|name)="%s"\s+content="([^"]*)"' % re.escape(key), index_html)
    return html.unescape(m.group(1)) if m else None


def fallback_image(repo: Path, index_html: str) -> tuple[str, tuple[int, int] | None]:
    """index.html's current og:image and its real pixel size (None if unreadable)."""
    url = index_meta(index_html, "og:image")
    if not url:
        raise PageError("public/index.html has no og:image to fall back to")
    size = None
    if url.startswith(SITE + "/"):
        size = png_size(repo / "public" / url[len(SITE) + 1:].split("?")[0])
    return url, size


def build_tags(date: str, line: str | None, image: str, size: tuple[int, int] | None,
               index_html: str) -> list[tuple[str, str, str]]:
    title = line if line else index_meta(index_html, "og:title")
    desc = SIGN_OFF if line else index_meta(index_html, "og:description")
    tags = []
    if title:
        tags.append(("property", "og:title", title))
    if desc:
        tags.append(("property", "og:description", desc))
    tags += [("property", "og:type", "website"),
             ("property", "og:url", f"{SITE}/{date}"),
             ("property", "og:image", image)]
    if size:
        tags += [("property", "og:image:width", str(size[0])),
                 ("property", "og:image:height", str(size[1]))]
    tags += [("name", "twitter:card", "summary_large_image"),
             ("name", "twitter:image", image)]
    return tags


def render_page(index_html: str, date: str, tags, mode: str) -> str:
    head_end = index_html.find("</head>")
    if head_end < 0:
        raise PageError("public/index.html has no </head>")
    head, rest = index_html[:head_end], index_html[head_end:]
    matches = list(META_RE.finditer(head))
    if not matches:
        raise PageError("no og:/twitter: meta tags in index.html <head> to replace")
    indent = re.match(r"[ \t]*", matches[0].group(0)).group(0)
    block = f"{indent}<!-- /{date} link preview ({mode}): baked by scripts/build_og_page.py -->\n"
    block += "".join(f'{indent}<meta {attr}="{key}" content="{html.escape(val, quote=True)}" />\n'
                     for attr, key, val in tags)
    start = matches[0].start()
    head = META_RE.sub("", head[:start]) + block + META_RE.sub("", head[start:])
    return head + rest


def bake(repo: Path, date: str, *, art_dir: Path | None = None, git_index: bool = False,
         no_landscape: bool = False) -> tuple[str, str]:
    """Return (page html, mode description) for `date`. Writes nothing."""
    if not DATE_RE.match(date):
        raise PageError(f"bad date {date!r}")
    datetime.date.fromisoformat(date)
    index_html = (repo / "public" / "index.html").read_text(encoding="utf-8")
    art_dir = art_dir if art_dir is not None else repo / "public" / "art"
    if no_landscape:
        ok, why = False, "--no-landscape"
    else:
        ok, why = landscape_status(repo, art_dir, date, git_index)
    if ok:
        image, size, mode = f"{SITE}/art/{date}-landscape.png", LANDSCAPE_SIZE, "landscape"
    else:
        image, size = fallback_image(repo, index_html)
        mode = f"fallback: {why}"
    line = day_line(repo, date)
    if not line:
        mode += "; no line for this date, index.html title/description kept"
    page = render_page(index_html, date, build_tags(date, line, image, size, index_html),
                       "landscape" if ok else "fallback")
    return page, mode


def write_atomic(path: Path, text: str) -> bool:
    data = text.encode("utf-8")
    if path.is_file() and path.read_bytes() == data:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp")
    try:
        tmp.write_bytes(data)
        os.replace(tmp, path)
    finally:
        if tmp.exists():
            tmp.unlink()
    return True


def target_dates(repo: Path, date: str | None) -> list[str]:
    if date:
        return [date]
    joy = json.loads((repo / "public" / "joy.json").read_text(encoding="utf-8"))
    dates = {joy.get("date")} if joy.get("date") else set()
    for p in (repo / "public").iterdir():
        m = PAGE_RE.match(p.name)
        if m:
            dates.add(m.group(1))
    if not dates:
        raise PageError("public/joy.json has no date")
    return sorted(dates)


def main(argv: list[str] | None = None, repo: Path = REPO) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--date", help="only this YYYY-MM-DD (default: joy.json date + existing dated pages)")
    ap.add_argument("--out-dir", type=Path, default=None, help="write <out-dir>/<date>.html (default: public/)")
    ap.add_argument("--art-dir", type=Path, default=None,
                    help="where to look for <date>-landscape.png (default: public/art)")
    ap.add_argument("--git-index", action="store_true",
                    help="landscape counts only if it is in the git index with the same bytes")
    ap.add_argument("--no-landscape", action="store_true", help="force the fallback og:image")
    ap.add_argument("--check", action="store_true", help="exit 1 if any page is stale; write nothing")
    args = ap.parse_args(argv)
    try:
        dates = target_dates(repo, args.date)
        out_dir = args.out_dir or repo / "public"
        stale = []
        for d in dates:
            page, mode = bake(repo, d, art_dir=args.art_dir, git_index=args.git_index,
                              no_landscape=args.no_landscape)
            out = out_dir / f"{d}.html"
            if args.check:
                cur = out.read_bytes() if out.is_file() else None
                if cur != page.encode("utf-8"):
                    stale.append(out)
                print(f"[og_page] {d}: {mode}")
                continue
            changed = write_atomic(out, page)
            print(f"[og_page] {d}: {'wrote' if changed else 'no change'} {out} ({mode})")
    except (PageError, OSError, ValueError) as e:
        print(f"::warning::dated page bake failed: {type(e).__name__}: {e}", file=sys.stderr)
        return 1
    if args.check and stale:
        print(f"[og_page] STALE: {', '.join(str(p) for p in stale)}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
