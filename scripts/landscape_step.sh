#!/usr/bin/env bash
# Nightly landscape step, called by .github/workflows/daily.yml AFTER the share
# render step (share-vertical / fb-share / portrait + fb handoff incl. reel) has
# fully succeeded. Kept separate so a landscape problem can never block, fail or
# roll back any of those files.
#
#   success: writes public/art/<date>-landscape.png (atomically) and rebuilds
#            public/fb/<date>.txt + today.txt so they carry the
#            "Landscape: https://gethappyinfo.com/art/<date>-landscape.png" line
#            (directly after the Portrait: line).
#   failure: deletes public/art/<date>-landscape.png (nothing written for the
#            landscape), rebuilds the fb text WITHOUT the Landscape line (same
#            inputs as the share step, so it is otherwise byte-identical; if that
#            somehow fails, restores the share step's copies with any Landscape
#            line stripped), adds a warning to the job summary, exits 1. The
#            workflow step is continue-on-error, so the run stays green and the
#            rest of the commit is untouched.
#
# The date is always public/joy.json's date (the only date today.txt describes).
# Env: PYTHON (default "python"), GITHUB_STEP_SUMMARY (optional).
set -uo pipefail
cd "$(dirname "$0")/.."
py="${PYTHON:-python}"
summary="${GITHUB_STEP_SUMMARY:-/dev/null}"
date=$("$py" -c 'import json;print(json.load(open("public/joy.json"))["date"])') || {
  echo "::warning::Landscape step: cannot read public/joy.json date; skipping"; exit 1; }
art="public/art/${date}-landscape.png"
line="Landscape: https://gethappyinfo.com/art/${date}-landscape.png"

snap=$(mktemp -d)
cp -a public/fb/. "$snap/"     # the share step's fb text, for the last-resort restore

fail() {
  echo "::warning::Landscape postcard for ${date} failed ($1); publishing without it today"
  rm -f "$art" "public/art/.${date}-landscape.png.tmp"
  if ! "$py" scripts/build_fb_handoff.py >/dev/null || ! "$py" scripts/build_fb_handoff.py --check >/dev/null; then
    echo "::warning::fb handoff rebuild failed during landscape restore; restoring the share step's fb text"
    rm -rf public/fb && mkdir -p public/fb && cp -a "$snap/." public/fb/
    sed -i '/^Landscape: /d' public/fb/today.txt "public/fb/${date}.txt" 2>/dev/null || true
  fi
  {
    echo "> **⚠️ Landscape postcard \`art/${date}-landscape.png\` skipped: $1.** No \`Landscape:\` line in /fb/today.txt today."
    echo "> share-vertical.png, fb-share.png, the portrait, the reel caption and today.txt are unaffected."
    echo
  } >> "$summary"
  exit 1
}

"$py" scripts/render_landscape.py || fail "render failed (LandscapeFitError = story too long even at the minimum size; see log)"
"$py" scripts/build_fb_handoff.py || fail "fb handoff rebuild failed"
"$py" scripts/build_fb_handoff.py --check || fail "fb handoff check failed"
grep -qxF "$line" public/fb/today.txt || fail "Landscape line missing from today.txt"
echo "[landscape_step] ${date}: wrote ${art}; today.txt has the Landscape line"
