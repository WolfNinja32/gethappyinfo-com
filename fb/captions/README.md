# Facebook caption overrides (optional)

By default the Facebook caption is `public/joy.json` → `line`, word for word.
To override it for a given day, commit `fb/captions/YYYY-MM-DD.txt` here
(repo root — this folder is **not** served).

Format:

- Caption body only. Plain UTF-8 text, one or more lines.
- No sign-off ("Pass it on ✉"), no link, no "Image:" line — the builder adds those.
- Trailing whitespace / blank lines are trimmed and line endings normalised to LF.
- An empty file is ignored (falls back to joy.json `line`).

Then run `python scripts/build_fb_handoff.py` as part of the share-vertical bake
and commit `public/fb/<date>.txt` + `public/fb/today.txt` in the same PR as the PNGs.
