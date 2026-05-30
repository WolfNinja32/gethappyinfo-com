# gethappyinfo.com V2 — Multi-Source Curation + Topic Filter
*Spec · 2026-05-30 · approved by Gregory*

## What we're building (one sentence)
Expand the daily picker from one firehose feed to a curated set of **topic-clean
category feeds**, guarantee an animal story every day, weight the rest by audience
demand, and harden the occult/horoscope exclusion at the source.

## Who it's for
The daily reader who wants a reliable hit of genuinely uplifting news — heavy on
animals — with zero mystical/astrology content. Same audience, sharper product.

## Core behavior
1. **Source = allowlist by category feed**, not one firehose. Pull only from feeds
   whose *topic* we want. Astrology/horoscope columns are never ingested because we
   never subscribe to those sections.
2. **Slot policy A — guaranteed animal lead.** Of 3 daily headlines: **slot 1 is
   always an animal story**; slots 2–3 are filled from the other topic feeds.
3. **Slots 2–3 weighted by demand.** Non-animal topics are picked via a
   **deterministic, date-seeded weighted rotation** (no randomness — reproducible),
   so over time each topic's share of slots ≈ its weight. Topic diversity enforced:
   don't fill both remaining slots from the same topic unless the pool is exhausted.
4. **Denylist stays as backstop.** Keep the fail-closed keyword denylist and **add
   occult/horoscope terms**: `horoscope, zodiac, astrology, astrologer, tarot,
   psychic, clairvoyant, occult, séance, ouija, "star sign", "mercury retrograde",
   crystal healing, manifestation ritual`. Catches leaks in non-clean feeds and
   mis-filed pieces.
5. **Cross-feed dedup** — the existing 14-day URL window + similarity check now also
   dedup *across* feeds (same story appearing in two categories).
6. **Degrade-safe, unchanged** — if a feed is down or a slot can't be filled, fall
   back to other feeds, then to the last good set. The site never blanks.

## Feed list + weights
GNN is WordPress, so category feeds follow `…/category/news/<slug>/feed/`.
**Each URL is validated (returns items) before we trust it** — dead/empty feeds are
dropped.

| Slot | Topic | Feed (to validate) | Weight |
|---|---|---|---|
| 1 (guaranteed) | **Animals** | GNN `…/animals/feed/` | — (always 1) |
| 2–3 pool | Inspiring / kindness / heroes | GNN `…/inspiring/feed/` | **36** |
| 2–3 pool | Health & wellness | GNN `…/health/feed/` | 30 |
| 2–3 pool | Environment / conservation | GNN `…/earth/feed/` | 27 |
| 2–3 pool | Science & tech | GNN `…/science/feed/` | 23 |

Weights seed from the Reuters Institute Digital News Report 2024 topic-interest
figures (Health 30, Science 29, Lifestyle/culture 28, Environment 27), then adjusted
per Gregory: Kindness/Inspiring boosted +20% (→36), Science/Tech cut −20% (→23).
They live in a tunable config dict at the top of the script — adjust anytime.

Second sources (Positive News, Reasons to be Cheerful) are **deferred** until the GNN
category-feed mix is proven.

## What it doesn't do (scope cuts)
- **No LLM / no AI ranking.** Source curation + slot weighting + denylist gets the
  goal deterministically and free. LLM ranking is parked for later (it would only buy
  ranking-by-heartwarming-ness and catching leaks in non-clean feeds).
- **No Cloudflare migration.** Stays on the working GitHub Actions cron + git-committed
  JSON. Moving compute to Cloudflare (Workers AI / Cron Triggers) is a separate later
  project.
- **No new storage** — keeps `joy.json` / `joy-archive.json` / `recent.json` as-is.
- **No per-topic pages or tags** on the site. The `joy.json` contract is unchanged;
  this is a backend-only change.

## Success criteria
- An animal story appears as slot 1 **every day** (or a logged, graceful fallback when
  the animals feed is genuinely empty).
- Over a 2-week window, non-animal slots roughly track the weights (kindness most,
  science least).
- **Zero** horoscope/occult items reach `joy.json` across a multi-week soak.
- Pipeline still runs free, automatic, stdlib-only, and degrade-safe — no new failure
  mode that can blank the site.

## Next immediate action
Validate the five GNN category-feed URLs return items, then write the `update_joy.py`
changes. Single-file change, no contract break.
