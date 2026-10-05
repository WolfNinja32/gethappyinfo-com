# gethappyinfo.com

A "dead simple" joy center: one screen, one daily act of kindness, and a few
pieces of good news — refreshed automatically every day.

**Live:** https://gethappyinfo.com — **Source:** [Good News Network](https://www.goodnewsnetwork.org/)

## How it works

```
public/index.html      Served. Vanilla HTML/CSS; fetches /joy.json on load.
public/joy.json        Served. Rewritten daily by the updater (committed).
data/tasks.txt         Micro-joy pool (one per line; # comments + blanks ignored).
data/recent.json       Rolling 14-day list of shown URLs (cross-day dedup).
scripts/update_joy.py  The daily updater — Python stdlib only, no dependencies.
.github/workflows/daily.yml   Daily GitHub Actions cron that runs the updater.
scripts/build_fb_handoff.py   Facebook handoff text (public/fb/), run by the daily workflow after the render.
scripts/render_share.py       Daily share image (public/share-vertical.png + fb-share.png, 1080x1080) + portrait.
scripts/render_portrait.py    Daily 1080x1920 portrait postcard (public/art/<date>-portrait.png), called by render_share.py.
render/                       Pinned fonts + requirements.txt for render_share.py (not served).
```

Once a day, GitHub Actions runs `update_joy.py`, which:

1. Fetches the Good News Network RSS feed.
2. **Filters** out anything matching a fail-closed grim/profanity denylist.
3. **Dedups** against the last 14 days (`recent.json`) and within the run
   (canonical URL + title similarity).
4. Picks the top 3 surviving headlines (feed order = editorial ranking).
5. Picks a **date-seeded** micro-joy task (stable per Pacific day).
6. Writes `public/joy.json`. **Degrade-safe:** the task always rotates; if no
   fresh headlines are found, the last good set carries over — the site never
   blanks.

No LLM, no build step, no server. Cloudflare Pages serves `public/` statically
and redeploys on every push.

## Facebook handoff (`/fb/today.txt`)

`https://gethappyinfo.com/fb/today.txt` (plus a dated copy `public/fb/YYYY-MM-DD.txt`)
holds the ready-to-post Facebook text:

```
<caption>

Pass it on ✉
https://gethappyinfo.com/YYYY-MM-DD

Image: https://gethappyinfo.com/share-vertical.png
Portrait: https://gethappyinfo.com/art/YYYY-MM-DD-portrait.png
Landscape: https://gethappyinfo.com/art/YYYY-MM-DD-landscape.png

Reel caption:
<reel caption, one or more lines (may include blank lines), through end of file>
```

The `Portrait:` line appears only when `public/art/<date>-portrait.png` exists,
and the `Reel caption:` section appears only together with it (no portrait → no
reel). The reel section is always **last**: everything after the `Reel caption:`
line up to end of file is the reel caption, so parsers should split on the first
`Reel caption:` line. All lines above it are unchanged from the photo format.
The `Landscape:` line appears only when `public/art/<date>-landscape.png` exists
(it ships in the same commit), always directly after the `Portrait:` line and
before the blank line + `Reel caption:` block (after `Image:` on a day with no
portrait). It is optional: on a day the landscape render fails, the line is simply
absent and everything else is identical.
(The site uses SPA not-found handling, so a missing `/art/...png` would return
`index.html` with HTTP 200 rather than a 404; consumers should only use URLs
listed in today.txt.)

- **Caption:** `fb/captions/<date>.txt` (optional override, repo root, not served;
  see `fb/captions/README.md`), otherwise `public/joy.json` → `line` word for word.
- **Reel caption:** `fb/captions/<date>.reel.txt` (optional override, same folder,
  same trimming rules, used verbatim), otherwise:

  ```
  <public/joy.json line>

  Send this to someone who needs it today ✉

  #kindness #gethappy
  ```

  Also written standalone (caption only, one trailing newline) to
  `public/fb/<date>.reel.txt` and `public/fb/today.reel.txt`
  (`https://gethappyinfo.com/fb/today.reel.txt`). If the joy.json date has no
  portrait, a leftover `today.reel.txt` is deleted so it never shows a previous day.
  No text is ever invented: if there is no reel override and joy.json has no
  `line` for that date, the build fails (same as the photo caption).
- **When:** automatically, in `.github/workflows/daily.yml`, right after
  `scripts/render_share.py` bakes `public/share-vertical.png` + `public/fb-share.png`.
  joy.json, the PNGs, `public/fb/<date>.txt`, `public/fb/today.txt` and the
  `.reel.txt` files land in
  **one commit** (one Cloudflare deploy), so today.txt never flips before its image.
  Idempotent; `--check` / `--dry-run` available.
- **Headers:** `public/_headers` serves `/fb/*` as `text/plain; charset=utf-8`
  with `Cache-Control: no-cache`.

## Daily share render (`/share-vertical.png`, `/fb-share.png`)

`scripts/render_share.py` draws the 1080x1080 postcard image from `public/joy.json`
(line, paragraph, date → postmark "OCT 1", edition number) and the inline
`<svg class="stamp-svg">` in `public/index.html`. Fonts are pinned in `render/fonts/`
(fontconfig is pointed only at that folder); Python deps are pinned in
`render/requirements.txt` (needs system `libcairo2`).

Schedule (all after Pacific midnight year-round):

| Trigger | UTC | Summer (PDT) | Winter (PST) |
|---|---|---|---|
| Cloudflare Worker `daily-trigger` (primary, dispatches the workflow; source not in this repo) | 12:00 today → intended 08:15 | 5:00am → 1:15am | 4:00am → 12:15am |
| GitHub schedule (early) | 08:15 | 1:15am | 12:15am |
| GitHub schedule (backup) | 09:30 | 2:30am | 1:30am |

The workflow renders only when the card changed (date/line/paragraph/seed), on
`force`, or when `public/fb/today.txt` is stale. If the render fails, joy.json is
still committed, the previous image + today.txt stay untouched, and the run is
marked failed with a job-summary warning. `workflow_dispatch` with `dry_run`
renders from the checked-out joy.json and uploads the PNGs (incl. `art/<date>-portrait.png`
and, if it rendered, `art/<date>-landscape.png`) + today.txt (+ today.reel.txt) as an
artifact without committing.

```bash
pip install -r render/requirements.txt
python scripts/render_share.py --out-dir /tmp/share   # or no flag to write public/
```

## Daily portrait postcard (`/art/<date>-portrait.png`)

`scripts/render_portrait.py` draws a 1080x1920 portrait version of the site's
postcard (phone-width single-column layout, Fraunces + IBM Plex Mono, stamp +
that day's postmark, the line, and the story paragraph). It is rendered by
`render_share.py` in the same step and committed in the same commit as
share-vertical.png. Everything except the background stays inside the Reels/Stories
safe zone (card at x 64–960, y 250–1570: 250px top, 350px bottom, 120px right).
The paragraph auto-fits 36 → 34 → 32 → 30 → 28px (line 56px, stepping down to 46px
only if the paragraph is already at 28px). If it still doesn't fit, the render
fails: no image is written, the previous share images stay, the job summary
shows the PortraitFitError, and the run fails. Dated files are permanent; only
the joy.json date's file is ever written.

## Daily landscape postcard (`/art/<date>-landscape.png`)

`scripts/render_landscape.py` draws a 1200x630 landscape version of the site's
postcard in its **desktop** layout (`public/index.html` without the ≤620px rules):
header row (POST · CARD, Get Happy Info*.com*, subtitle | the index.html stamp with
that day's postmark), double ocean rule, then the two-column `.letter` grid
(`1fr | dashed divider | 1.2fr`): *a note to you* / Dear friend / the line / Yours in
joy ~ Get Happy on the left, *a little more* + the story paragraph on the right,
and the `GETHAPPYINFO.COM · NO. NNN` footer with the ✉ PASS IT ON ↗ badge. It reuses
the portrait/share helpers: pinned fonts, palette, stamp extraction, postmark, and
the portrait's brand-red handling (premultiplied rotation, blended alpha fills).

- **Source line:** plain text under the paragraph, `Source: <seed.sourceTitle> ·
  <domain of seed.sourceUrl>` (either part alone if only one exists; omitted when
  neither does). Never invented; wraps, never truncated.
- **Auto-fit:** the paragraph steps 21 → 20 → 19 → 18 → 17 → 16px; the line
  (34px) steps down to 26px only if the note column itself overflows. Nothing is
  cut off: if it still can't fit, `LandscapeFitError` → the failure path below.
  Across the archive (2026-09-04 … 2026-10-04) the line always stays 34px and the
  longest story (2026-09-22, 637 chars) is the only day at 16px.
- **Safe margins:** all text/stamp/postmark stays inside the card's inner box
  (≥ 70px from the left/right edges, ≥ 30px from top/bottom); only the table
  background and card shadow reach the edges.
- **Isolated workflow step:** `Render landscape postcard (optional)` runs
  `scripts/landscape_step.sh` only after the share render step succeeded, with
  `continue-on-error`. Success: writes the PNG (atomically) and rebuilds
  `public/fb/<date>.txt` / `today.txt` with the `Landscape:` line. Failure (any
  reason, incl. `LandscapeFitError`): deletes that date's landscape file, rebuilds
  the fb text without the line, adds a ⚠️ warning to the job summary; the run
  stays green and share-vertical / fb-share / portrait / reel / today.txt are
  committed exactly as the share step produced them. The landscape never triggers
  a re-render on its own; to retry, run the workflow with `force`.
- **Not used for** the FB photo post (`Image:` stays share-vertical.png) or
  `og:image`.

```bash
python scripts/render_landscape.py --out /tmp/landscape.png   # from public/joy.json
python scripts/render_landscape.py --joy some/joy.json --out-dir /tmp/x   # → /tmp/x/art/<date>-landscape.png
```

## Run / test locally

```bash
python scripts/update_joy.py          # refresh public/joy.json from the live feed
python -m http.server -d public 8000  # then open http://localhost:8000
python -m unittest discover -s test -t .   # full suite (render tests need render/requirements.txt)
```

## Deploy notes (one-time, owner-side)

- **GitHub:** Settings → Actions → General → Workflow permissions → **Read and
  write** (otherwise the daily commit 403s).
- **Cloudflare Pages:** connect this repo, framework preset **None**, build
  command empty, output directory **`public`**.
- **DNS:** point the `gethappyinfo.com` nameservers (at Hover) to Cloudflare,
  then add the apex + `www` custom domain to the Pages project.

## Design choices

- **GitHub-direct** (not Gitea-mirrored) so the daily bot commit isn't clobbered
  by a force-mirror.
- **Single curated source + classic filtering** instead of an LLM: the source's
  editors already rank for positivity; safety and dedup are solved with a
  denylist and string similarity. A future multi-source version could add a
  local-model curation gate.
