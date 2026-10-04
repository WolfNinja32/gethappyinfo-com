# Facebook caption overrides (optional)

By default the Facebook caption is `public/joy.json` → `line`, word for word.
To override it for a given day, commit `fb/captions/YYYY-MM-DD.txt` here
(repo root — this folder is **not** served).

Format:

- Caption body only. Plain UTF-8 text, one or more lines.
- No sign-off ("Pass it on ✉"), no link, no "Image:" line — the builder adds those.
- Trailing whitespace / blank lines are trimmed and line endings normalised to LF.
- An empty file is ignored (falls back to joy.json `line`).

## Reel caption override

The reel caption (only emitted on days that have a portrait) defaults to:

```
<public/joy.json line>

Send this to someone who needs it today ✉

#kindness #gethappy
```

To override it, commit `fb/captions/YYYY-MM-DD.reel.txt` here. Its contents are
used **verbatim** as the whole reel caption (include your own sign-off/hashtags),
with the same trimming rules as above; an empty file is ignored. It ends up as
the `Reel caption:` section at the end of `/fb/today.txt` and in
`/fb/today.reel.txt`.

Back-filling an older date that has a portrait (`--date YYYY-MM-DD`) needs
both `YYYY-MM-DD.txt` and `YYYY-MM-DD.reel.txt` here, since joy.json only holds
today's line (the build fails rather than invent text).

The nightly "Daily joy update" workflow (~08:15 UTC = 1:15am PDT / 12:15am PST)
picks the overrides up automatically. Commit it **before** that run. If you add or
change it later, re-run the workflow (Actions → Daily joy update → Run workflow);
it re-renders because `public/fb/today.txt` is then stale.
