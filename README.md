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
4. Output Directory: leave empty
5. Install Command: leave empty

Keep the Framework **Other** (not FastAPI or Flask) so `api/*.py` become Vercel Functions. After you push, wait for the deployment, then open `/api/refresh` — GET should return `"has_key": true` if the env vars below are set.

The live page is `index.html` plus `data/snapshot.json`. **Refresh** on the deployed site calls `/api/refresh`. That updates kingdom boards and Top-5 rosters in about 15–30 seconds. Hero portraits, kills, and coords stay from the last committed snapshot until you run a full `python3 refresh.py` locally and push.

### Environment variables (Vercel → Settings → Environment Variables)

Add these for **Production**, **Preview**, and **Development**. Do **not** tick “Sensitive / Next.js public” and do **not** start any name with `NEXT_PUBLIC_`.

| Name | Value |
| --- | --- |
| `KINGSHOT_API_KEY` | your `kss_…` key from https://api.mightpulse.com |
| `KINGSHOT_API_BASE_URL` | `https://api.mightpulse.com/v1` |
| `KINGSHOT_KID` | `2362` |

Skip `NEXT_PUBLIC_SUPABASE_URL`, `NEXT_PUBLIC_SUPABASE_ANON_KEY`, and `SUPABASE_SERVICE_ROLE_KEY` — this repo is not the merge planner.

Redeploy after saving. The key stays on the server; it is never used in `index.html`.

## Open locally

```bash
cd "combat-stats"
python3 serve.py
# http://127.0.0.1:8765  — use Refresh on the page to re-fetch
```

Refresh from the page re-fetches boards and rosters (~15–30 seconds). A full player-page crawl still takes ~10 minutes via the CLI.

## Refresh data

Boards + rosters only (what the site button does):

```bash
export KINGSHOT_API_KEY='kss_…'   # from https://api.mightpulse.com (Discord)
python3 refresh.py --boards
```

Full player pages (heroes, kills, coords) — then commit `data/snapshot.json` and push so every visitor gets them:

```bash
export KINGSHOT_API_KEY='kss_…'
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
