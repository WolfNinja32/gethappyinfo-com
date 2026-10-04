#!/usr/bin/env python3
"""Build the daily Facebook handoff text: public/fb/<date>.txt + public/fb/today.txt.

Served as https://gethappyinfo.com/fb/today.txt (and /fb/YYYY-MM-DD.txt).
Format (UTF-8, LF, exactly one trailing newline):

    <caption body, one or more lines>

    Pass it on ✉
    https://gethappyinfo.com/YYYY-MM-DD

    Image: https://gethappyinfo.com/share-vertical.png
    Portrait: https://gethappyinfo.com/art/YYYY-MM-DD-portrait.png

    Reel caption:
    <reel caption block, one or more lines, through end of file>

The Portrait line and the Reel caption section are included ONLY when
public/art/<date>-portrait.png exists (no portrait -> no reel). The Reel caption
section is always the LAST section: everything after the "Reel caption:" line,
to end of file, is the reel caption (it may contain blank lines). The same
reel caption is also written standalone (caption block + one trailing newline) to
public/fb/<date>.reel.txt and public/fb/today.reel.txt.

The Portrait line is included ONLY when public/art/<date>-portrait.png exists
(rendered by scripts/render_share.py in the same step). The site serves assets
with not_found_handling = "single-page-application" (wrangler.jsonc), so a
missing /art/ file would come back as index.html with HTTP 200, not a 404;
gating the line on the file existing means consumers never get that URL for a
portrait that wasn't baked.

Caption precedence:
  1. fb/captions/<date>.txt at the repo root (NOT served), if it exists and is
     non-empty after normalisation (CRLF/CR -> LF, trailing whitespace stripped
     from every line, trailing blank lines dropped). Optional override.
  2. Otherwise public/joy.json "line", word for word.
  (joy.json is rewritten daily by scripts/update_joy.py, so no extra joy.json
  field is read or required.)

Reel caption precedence (same directory + same trimming rules as above):
  1. fb/captions/<date>.reel.txt, if it exists and is non-empty after
     normalisation. Used verbatim.
  2. Otherwise:
         <public/joy.json "line", word for word>

         Send this to someone who needs it today ✉

         #kindness #gethappy
  If neither is available (no override and joy.json is for another date or has
  no "line") the build fails, exactly like the photo caption.

WHEN IT RUNS — automatically, in the nightly "Daily joy update" workflow
(.github/workflows/daily.yml), right after scripts/render_share.py bakes
public/share-vertical.png + public/fb-share.png. The workflow commits
      public/joy.json (+ archive/recent)
      public/share-vertical.png
      public/fb-share.png
      public/art/<date>-portrait.png
      public/fb/<date>.txt
      public/fb/today.txt
      public/fb/<date>.reel.txt
      public/fb/today.reel.txt
in ONE commit, so today.txt can never point at a share-vertical.png that
hasn't been baked yet. If the render fails, none of the share files change.

Usage:
  python scripts/build_fb_handoff.py              # date = joy.json date
  python scripts/build_fb_handoff.py --date YYYY-MM-DD
  python scripts/build_fb_handoff.py --check      # exit 1 if outputs are stale
  python scripts/build_fb_handoff.py --dry-run    # print text, write nothing
  python scripts/build_fb_handoff.py --dry-run --art-dir /tmp/x/art --reel-out /tmp/x/today.reel.txt
                                                  # look for the portrait elsewhere and write the
                                                  # standalone reel caption to a scratch file (CI dry run)

Rules:
  * Idempotent: files are only rewritten when their content changes.
  * public/fb/<date>.txt only ever touches that one date's file.
  * public/fb/today.txt / today.reel.txt are only written when <date> == joy.json
    date (a back-fill for another date never flips them).
  * If the joy.json date has no portrait, a leftover public/fb/today.reel.txt is
    removed so it can never describe a previous day (dated files are kept).
  * Stdlib only.
"""
from __future__ import annotations

import argparse
import datetime
import json
import re
import sys
from pathlib import Path
from zoneinfo import ZoneInfo

REPO = Path(__file__).resolve().parent.parent
SITE = "https://gethappyinfo.com"
IMAGE_URL = f"{SITE}/share-vertical.png"
SIGN_OFF = "Pass it on ✉"
REEL_SIGN_OFF = "Send this to someone who needs it today ✉"
REEL_HASHTAGS = "#kindness #gethappy"
REEL_HEADER = "Reel caption:"
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def normalize(text: str) -> str:
    """CRLF/CR -> LF, strip trailing whitespace per line and trailing blank lines."""
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    lines = [ln.rstrip() for ln in text.split("\n")]
    while lines and not lines[-1]:
        lines.pop()
    while lines and not lines[0]:
        lines.pop(0)
    return "\n".join(lines)


def portrait_url(date: str) -> str:
    return f"{SITE}/art/{date}-portrait.png"


def render(caption: str, date: str, portrait: bool = False,
           reel: str | None = None) -> str:
    body = normalize(caption)
    if not body:
        raise ValueError("empty caption")
    text = f"{body}\n\n{SIGN_OFF}\n{SITE}/{date}\n\nImage: {IMAGE_URL}\n"
    if portrait:
        text += f"Portrait: {portrait_url(date)}\n"
        if reel is not None:
            text += f"\n{REEL_HEADER}\n{render_reel(reel)}"
    elif reel is not None:
        raise ValueError("reel caption requires a portrait")
    return text


def render_reel(reel: str) -> str:
    """Standalone reel caption file content: the block + exactly one trailing newline."""
    body = normalize(reel)
    if not body:
        raise ValueError("empty reel caption")
    return body + "\n"


def load_joy(repo: Path) -> dict:
    return json.loads((repo / "public" / "joy.json").read_text(encoding="utf-8"))


def resolve_caption(repo: Path, date: str, joy: dict) -> tuple[str, str]:
    """Return (caption, source-description)."""
    override = repo / "fb" / "captions" / f"{date}.txt"
    if override.is_file():
        cap = normalize(override.read_text(encoding="utf-8"))
        if cap:
            return cap, str(override.relative_to(repo))
    if joy.get("date") != date:
        raise SystemExit(
            f"[fb_handoff] no caption for {date}: fb/captions/{date}.txt missing/empty "
            f"and public/joy.json is for {joy.get('date')!r}"
        )
    line = normalize(str(joy.get("line") or ""))
    if not line:
        raise SystemExit("[fb_handoff] public/joy.json has no 'line'")
    return line, "public/joy.json line"


def resolve_reel_caption(repo: Path, date: str, joy: dict) -> tuple[str, str]:
    """Return (reel caption, source-description). Mirrors resolve_caption."""
    override = repo / "fb" / "captions" / f"{date}.reel.txt"
    if override.is_file():
        cap = normalize(override.read_text(encoding="utf-8"))
        if cap:
            return cap, str(override.relative_to(repo))
    if joy.get("date") != date:
        raise SystemExit(
            f"[fb_handoff] no reel caption for {date}: fb/captions/{date}.reel.txt missing/empty "
            f"and public/joy.json is for {joy.get('date')!r}"
        )
    line = normalize(str(joy.get("line") or ""))
    if not line:
        raise SystemExit("[fb_handoff] public/joy.json has no 'line' (needed for the reel caption)")
    return f"{line}\n\n{REEL_SIGN_OFF}\n\n{REEL_HASHTAGS}", "public/joy.json line + reel template"


def build(repo: Path, date: str | None = None, check: bool = False,
          dry_run: bool = False, out=sys.stdout, art_dir: Path | None = None,
          reel_out: Path | None = None) -> int:
    joy = load_joy(repo)
    joy_date = joy.get("date")
    date = date or joy_date
    if not date or not DATE_RE.match(date):
        raise SystemExit(f"[fb_handoff] bad date: {date!r}")
    datetime.date.fromisoformat(date)  # validates calendar date

    la_today = datetime.datetime.now(ZoneInfo("America/Los_Angeles")).date().isoformat()
    if date != la_today:
        print(f"[fb_handoff] note: building {date}; today in America/Los_Angeles is {la_today}",
              file=sys.stderr)

    caption, source = resolve_caption(repo, date, joy)
    art_dir = art_dir if art_dir is not None else repo / "public" / "art"
    has_portrait = (art_dir / f"{date}-portrait.png").is_file()
    reel = reel_source = None
    if has_portrait:
        reel, reel_source = resolve_reel_caption(repo, date, joy)
    text = render(caption, date, portrait=has_portrait, reel=reel)
    reel_text = render_reel(reel) if reel is not None else None

    fb_dir = repo / "public" / "fb"
    # (path, desired content); None = the file must not exist
    targets: list[tuple[Path, str | None]] = [(fb_dir / f"{date}.txt", text)]
    if reel_text is not None:
        targets.append((fb_dir / f"{date}.reel.txt", reel_text))
    if date == joy_date:
        targets.append((fb_dir / "today.txt", text))
        targets.append((fb_dir / "today.reel.txt", reel_text))
    else:
        print(f"[fb_handoff] {date} != joy.json date {joy_date}; today.txt / today.reel.txt "
              "left untouched", file=sys.stderr)

    if dry_run:
        out.write(text)
        if reel_out is not None:
            if reel_text is None:
                print("[fb_handoff] no portrait -> no reel caption; --reel-out not written",
                      file=sys.stderr)
            else:
                reel_out.parent.mkdir(parents=True, exist_ok=True)
                reel_out.write_bytes(reel_text.encode("utf-8"))
        return 0

    stale = []
    removed = []
    for path, want in targets:
        current = path.read_text(encoding="utf-8") if path.is_file() else None
        if current == want:
            continue
        stale.append(path)
        if check:
            continue
        if want is None:
            path.unlink()
            removed.append(path)
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(want.encode("utf-8"))

    rel = ", ".join(str(p.relative_to(repo)) for p, want in targets if want is not None)
    if check:
        if stale:
            print(f"[fb_handoff] STALE: {', '.join(str(p.relative_to(repo)) for p in stale)}",
                  file=sys.stderr)
            return 1
        print(f"[fb_handoff] up to date: {rel}")
        return 0
    for path in removed:
        print(f"[fb_handoff] removed stale {path.relative_to(repo)} (no portrait -> no reel)")
    written = [p for p in stale if p not in removed]
    if written:
        print(f"[fb_handoff] wrote {', '.join(str(p.relative_to(repo)) for p in written)} "
              f"(caption from {source}"
              + (f"; reel caption from {reel_source})" if reel_source else "; no portrait, no reel)"))
    elif not removed:
        print(f"[fb_handoff] no change: {rel}")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--date", help="YYYY-MM-DD (default: public/joy.json date)")
    ap.add_argument("--check", action="store_true", help="exit 1 if outputs are stale; write nothing")
    ap.add_argument("--dry-run", action="store_true", help="print the text; write nothing")
    ap.add_argument("--art-dir", type=Path, default=None,
                    help="where to look for <date>-portrait.png (default: public/art)")
    ap.add_argument("--reel-out", type=Path, default=None,
                    help="with --dry-run: also write the standalone reel caption to this file")
    args = ap.parse_args(argv)
    if args.reel_out is not None and not args.dry_run:
        ap.error("--reel-out requires --dry-run")
    return build(REPO, args.date, check=args.check, dry_run=args.dry_run,
                 art_dir=args.art_dir, reel_out=args.reel_out)


if __name__ == "__main__":
    sys.exit(main())
