# Combat Power · Kingdom 2362

Static dashboard (same dark theme as the TFU roster) fed by the **MightPulse** API (`api.mightpulse.com`).

## What it shows

- **Every member** of the Top 5 alliances
- Alliance totals for **personal power, kills, TC 30s** across the full roster
- **Combat / troop / building / hero-gear** only for governors on the kingdom **top-100 boards** (count shown on each alliance card)
- Player pages: kills, VIP, coords, ranks past 100, mystic / event stages, defence **hero portraits**, **gear images + levels**, exclusive widgets, lord gear
- **One-on-one** compare — search two governors or click a name in the tables

MightPulse does not publish troop or building power below kingdom rank 100. Those cells are blank, and they are not included in alliance combat.

## Deploy on Vercel

This is a **static** site. Import [nouman11033/help-grow](https://github.com/nouman11033/help-grow) in Vercel:

1. Framework Preset: **Other**
2. Root Directory: leave empty (repo root)
3. Build Command: leave empty
4. Output Directory: leave empty (`.`)
5. Install Command: leave empty

The live page is `index.html` plus `data/snapshot.json`. The **Refresh** button only works when you run `python3 serve.py` locally (it needs the MightPulse API key). On Vercel it will fail until you re-export a snapshot and push.

Do **not** add `KINGSHOT_API_KEY` to Vercel unless you also add a server route for refresh. The key must never go in the frontend.

## Open locally

```bash
cd "combat-stats"
python3 serve.py
# http://127.0.0.1:8765  — use Refresh on the page to re-fetch
```

Refresh from the page runs in the background (player pages take ~10 minutes) and the button shows progress.

## Refresh data

```bash
export KINGSHOT_API_KEY='kss_…'   # from https://api.mightpulse.com (Discord)
python3 refresh.py
```

Reuse already-downloaded kingdom / roster files, and only fetch missing player pages:

```bash
python3 refresh.py --cached
```

Rebuild the dashboard from disk with no API calls:

```bash
python3 refresh.py --offline
```

Re-fetch every player page even if cached:

```bash
python3 refresh.py --cached --refresh-players
```

Optional:

```bash
export KINGSHOT_API_BASE_URL='https://api.mightpulse.com/v1'
export KINGSHOT_KID='2362'
```
