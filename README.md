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
