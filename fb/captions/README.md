# Facebook caption overrides (optional)

By default the Facebook caption is `public/joy.json` → `line`, word for word.
To override it for a given day, commit `fb/captions/YYYY-MM-DD.txt` here
(repo root — this folder is **not** served).

Format:

- Caption body only. Plain UTF-8 text, one or more lines.
- No sign-off ("Pass it on ✉"), no link, no "Image:" line — the builder adds those.
- Trailing whitespace / blank lines are trimmed and line endings normalised to LF.
- An empty file is ignored (falls back to joy.json `line`).

The nightly "Daily joy update" workflow (~08:15 UTC = 1:15am PDT / 12:15am PST)
picks the override up automatically. Commit it **before** that run. If you add or
change it later, re-run the workflow (Actions → Daily joy update → Run workflow);
it re-renders because `public/fb/today.txt` is then stale.
