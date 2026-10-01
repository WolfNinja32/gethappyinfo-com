#!/usr/bin/env python3
"""Build the daily Facebook handoff text: public/fb/<date>.txt + public/fb/today.txt.

Served as https://gethappyinfo.com/fb/today.txt (and /fb/YYYY-MM-DD.txt).
Format (UTF-8, LF, exactly one trailing newline):

    <caption body, one or more lines>

    Pass it on ✉
    https://gethappyinfo.com/YYYY-MM-DD

    Image: https://gethappyinfo.com/share-vertical.png

Caption precedence:
  1. fb/captions/<date>.txt at the repo root (NOT served), if it exists and is
     non-empty after normalisation (CRLF/CR -> LF, trailing whitespace stripped
     from every line, trailing blank lines dropped). Optional override.
  2. Otherwise public/joy.json "line", word for word.
  (joy.json is rewritten daily by scripts/update_joy.py, so no extra joy.json
  field is read or required.)

WHEN TO RUN — part of the manual daily share-vertical bake:
  Run this right after baking public/share-vertical.png + public/fb-share.png,
  and commit its outputs in the SAME PR. A bake PR therefore contains:
      public/share-vertical.png
      public/fb-share.png
      public/fb/<date>.txt
      public/fb/today.txt
  Because today.txt ships in the same commit as the image, it can never point
  at a share-vertical.png that hasn't been baked yet. There is deliberately no
  GitHub Actions workflow for this.

Usage:
  python scripts/build_fb_handoff.py              # date = joy.json date
  python scripts/build_fb_handoff.py --date YYYY-MM-DD
  python scripts/build_fb_handoff.py --check      # exit 1 if outputs are stale
  python scripts/build_fb_handoff.py --dry-run    # print text, write nothing

Rules:
  * Idempotent: files are only rewritten when their content changes.
  * public/fb/<date>.txt only ever touches that one date's file.
  * public/fb/today.txt is only written when <date> == joy.json date
    (a back-fill for another date never flips today.txt).
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


def render(caption: str, date: str) -> str:
    body = normalize(caption)
    if not body:
        raise ValueError("empty caption")
    return f"{body}\n\n{SIGN_OFF}\n{SITE}/{date}\n\nImage: {IMAGE_URL}\n"


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


def build(repo: Path, date: str | None = None, check: bool = False,
          dry_run: bool = False, out=sys.stdout) -> int:
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
    text = render(caption, date)

    fb_dir = repo / "public" / "fb"
    targets = [fb_dir / f"{date}.txt"]
    if date == joy_date:
        targets.append(fb_dir / "today.txt")
    else:
        print(f"[fb_handoff] {date} != joy.json date {joy_date}; today.txt left untouched",
              file=sys.stderr)

    if dry_run:
        out.write(text)
        return 0

    stale = []
    for path in targets:
        current = path.read_text(encoding="utf-8") if path.is_file() else None
        if current == text:
            continue
        stale.append(path)
        if not check:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(text.encode("utf-8"))

    rel = ", ".join(str(p.relative_to(repo)) for p in targets)
    if check:
        if stale:
            print(f"[fb_handoff] STALE: {', '.join(str(p.relative_to(repo)) for p in stale)}",
                  file=sys.stderr)
            return 1
        print(f"[fb_handoff] up to date: {rel}")
        return 0
    if stale:
        print(f"[fb_handoff] wrote {', '.join(str(p.relative_to(repo)) for p in stale)} "
              f"(caption from {source})")
    else:
        print(f"[fb_handoff] no change: {rel}")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--date", help="YYYY-MM-DD (default: public/joy.json date)")
    ap.add_argument("--check", action="store_true", help="exit 1 if outputs are stale; write nothing")
    ap.add_argument("--dry-run", action="store_true", help="print the text; write nothing")
    args = ap.parse_args(argv)
    return build(REPO, args.date, check=args.check, dry_run=args.dry_run)


if __name__ == "__main__":
    sys.exit(main())
