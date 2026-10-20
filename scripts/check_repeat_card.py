#!/usr/bin/env python3
"""Repeat-card guard: compare public/joy.json to the previous day's archived entry.

Exit 0 = fresh card, exit 3 = REPEAT (line or story matches the previous day).
Used by daily.yml to withhold public/fb/today.txt (+ today.reel.txt) so Martha's
6am date guard skips instead of re-posting yesterday's card under today's date.
"""
import json, sys
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _norm(s):
    return " ".join(str(s or "").split()).casefold()


def find_repeat(today, prev):
    """Return list of matching fields ('line', 'story') between two joy entries."""
    if not today or not prev:
        return []
    hits = []
    if _norm(today.get("line")) and _norm(today.get("line")) == _norm(prev.get("line")):
        hits.append("line")
    ts, ps = today.get("seed") or {}, prev.get("seed") or {}
    same_url = _norm(ts.get("sourceUrl")) and _norm(ts.get("sourceUrl")) == _norm(ps.get("sourceUrl"))
    same_sum = _norm(ts.get("summary")) and _norm(ts.get("summary")) == _norm(ps.get("summary"))
    same_par = _norm(today.get("paragraph")) and _norm(today.get("paragraph")) == _norm(prev.get("paragraph"))
    if same_url or same_sum or same_par:
        hits.append("story")
    return hits


def main():
    joy = json.loads((ROOT / "public/joy.json").read_text())
    days = json.loads((ROOT / "public/joy-archive.json").read_text()).get("days", {})
    prev_date = (date.fromisoformat(joy["date"]) - timedelta(days=1)).isoformat()
    hits = find_repeat(joy, days.get(prev_date))
    if hits:
        print(f"REPEAT CARD {joy['date']}: {' + '.join(hits)} matches {prev_date}")
        return 3
    print(f"fresh card {joy['date']} (vs {prev_date})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
