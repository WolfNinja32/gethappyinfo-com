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
scripts/render_share.py       Daily share image (public/share-vertical.png + fb-share.png, 1080x1080).
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
```

- **Caption:** `fb/captions/<date>.txt` (optional override, repo root, not served;
  see `fb/captions/README.md`), otherwise `public/joy.json` → `line` word for word.
- **When:** automatically, in `.github/workflows/daily.yml`, right after
  `scripts/render_share.py` bakes `public/share-vertical.png` + `public/fb-share.png`.
  joy.json, both PNGs, `public/fb/<date>.txt` and `public/fb/today.txt` land in
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
renders from the checked-out joy.json and uploads the PNGs + today.txt as an
artifact without committing.

```bash
pip install -r render/requirements.txt
python scripts/render_share.py --out-dir /tmp/share   # or no flag to write public/
```

## Run / test locally

```bash
python scripts/update_joy.py          # refresh public/joy.json from the live feed
python -m http.server -d public 8000  # then open http://localhost:8000
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
