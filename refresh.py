#!/usr/bin/env python3
"""Refresh Kingdom 2362 stats from MightPulse.

Every alliance member is included. Player pages are fetched for kills, ranks,
events, VIP, coords, and defence heroes (with gear).
"""

from __future__ import annotations

import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
PLAYERS = DATA / "players"
STATUS = DATA / "refresh-status.json"
BASE = os.environ.get("KINGSHOT_API_BASE_URL", "https://api.mightpulse.com/v1").rstrip("/")
ASSET = "https://mightpulse.com"
KID = os.environ.get("KINGSHOT_KID", "2362")
BOARD_NAMES = (
    "personal_power",
    "troop_power",
    "building_power",
    "alliance_power",
    "hero_total",
    "hero_equip",
    "gov_gear",
    "gov_charm",
)
EVENT_BOARDS = (
    ("Coliseum", "coliseum"),
    ("Crystal Cave", "crystal_cave"),
    ("Knowledge Nexus", "knowledge_nexus"),
    ("Molten Fort", "molten_fort"),
    ("Radiant Spire", "radiant_spire"),
)
REQUEST_GAP = 1.1  # stay under 60/minute

# Kingdom 2362: RCB renamed to [SUN] SuperUnitedNexus.
TAG_RENAMES = {"RCB": "SUN"}
RENAMED_RCB_TAG = "SUN"
RENAMED_RCB_NAME = "SuperUnitedNexus"


def canonical_tag(tag) -> str:
    text = str(tag or "").strip()
    if not text:
        return ""
    return TAG_RENAMES.get(text) or TAG_RENAMES.get(text.upper()) or text


def tags_match(left, right) -> bool:
    a, b = canonical_tag(left), canonical_tag(right)
    return bool(a) and a == b


def alliance_display_name(tag, name=None) -> str:
    given = str(name or "").strip()
    if tags_match(tag, "RCB") or tags_match(tag, "SUN") or re.fullmatch(r"super\s*united\s*nexus", given, re.I):
        return RENAMED_RCB_NAME
    return given or canonical_tag(tag)


def tag_aliases(tag) -> list[str]:
    resolved = canonical_tag(tag)
    aliases = [resolved]
    if resolved == RENAMED_RCB_TAG:
        aliases.extend(old for old in TAG_RENAMES if old not in aliases)
    raw = str(tag or "").strip()
    if raw and raw not in aliases:
        aliases.append(raw)
    return aliases


def roster_for(tag: str, rosters: dict[str, dict]):
    for alias in tag_aliases(tag):
        if alias in rosters:
            return rosters[alias]
    return None


def remap_roster_keys(rosters: dict[str, dict]) -> dict[str, dict]:
    out = {}
    for tag, rost in rosters.items():
        key = canonical_tag(tag) or tag
        out[key] = rost
    return out


def load_api_key() -> str:
    key = os.environ.get("KINGSHOT_API_KEY", "").strip()
    if key:
        return key
    for candidate in (
        ROOT / ".env.local",
        ROOT / ".env",
        ROOT.parent / "merger" / ".env.local",
    ):
        if not candidate.exists():
            continue
        for line in candidate.read_text(encoding="utf-8").splitlines():
            if line.startswith("KINGSHOT_API_KEY="):
                return line.split("=", 1)[1].strip().strip("'\"")
    return ""


def on_vercel() -> bool:
    return os.environ.get("VERCEL") == "1"


def persist_json(path: Path, obj, *, indent: int | None = 2) -> None:
    if on_vercel():
        return
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        text = json.dumps(obj, ensure_ascii=False, indent=indent)
        path.write_text(text + ("" if text.endswith("\n") else "\n"))
    except OSError:
        return


def write_status(**kwargs) -> None:
    if on_vercel():
        return
    try:
        DATA.mkdir(parents=True, exist_ok=True)
        current = {}
        if STATUS.exists():
            try:
                current = json.loads(STATUS.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                current = {}
        current.update(kwargs)
        STATUS.write_text(json.dumps(current, ensure_ascii=False) + "\n")
    except OSError:
        return


def abs_url(path: str | None) -> str | None:
    if not path:
        return None
    if path.startswith("http://") or path.startswith("https://"):
        return path
    if not path.startswith("/"):
        path = "/" + path
    return ASSET + path


def equip_icon(eid) -> str | None:
    if eid is None or eid == "":
        return None
    return f"{ASSET}/assets/icons/equipment_icon_{eid}.png"


_last_request = 0.0


def api_get(path: str, key: str) -> dict:
    global _last_request
    wait = REQUEST_GAP - (time.time() - _last_request)
    if wait > 0:
        time.sleep(wait)
    url = f"{BASE}{path}"
    req = urllib.request.Request(
        url,
        headers={
            "Authorization": f"Bearer {key}",
            "Accept": "application/json",
            "User-Agent": "Mozilla/5.0 combat-stats/1.5",
        },
    )
    for attempt in range(6):
        _last_request = time.time()
        try:
            with urllib.request.urlopen(req, timeout=120) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", errors="replace")
            if e.code == 429:
                time.sleep(20 + attempt * 10)
                continue
            raise RuntimeError(f"HTTP {e.code} {path}: {body[:300]}") from e
    raise RuntimeError(f"Rate limited on {path}")


def board_index(payload: dict) -> dict[int, dict]:
    rows = (payload.get("board") or {}).get("rows") or []
    out = {}
    for row in rows:
        uid = row.get("uid")
        if uid is None:
            continue
        out[int(uid)] = {
            "uid": int(uid),
            "governor_id": row.get("governor_id"),
            "name": row.get("nick_name"),
            "alliance": canonical_tag(row.get("alliance_abbr")) or row.get("alliance_abbr") or "—",
            "score": int(row.get("score") or 0),
            "rank": row.get("rank"),
            "avatar_url": row.get("avatar_url"),
        }
    return out


def board_rows(payload: dict) -> list:
    return (payload.get("board") or {}).get("rows") or []


def score_index(rows: list) -> dict[int, int]:
    out = {}
    for row in rows:
        uid = row.get("uid")
        if uid is None:
            continue
        out[int(uid)] = int(row.get("score") or 0)
    return out


def load_cached_payloads() -> tuple[dict, dict[str, dict], dict[str, dict]]:
    kd = json.loads((DATA / "kingdom-raw.json").read_text(encoding="utf-8"))
    payloads = {
        name: json.loads((DATA / f"{name}-raw.json").read_text(encoding="utf-8"))
        for name in BOARD_NAMES
    }
    rosters = {}
    for path in sorted(DATA.glob("roster-*.json")):
        tag = canonical_tag(path.stem.split("roster-", 1)[-1])
        rosters[tag] = json.loads(path.read_text(encoding="utf-8"))
    return kd, payloads, remap_roster_keys(rosters)


def payloads_from_kingdom(kd: dict) -> dict[str, dict]:
    """Wrap kingdom `include=boards` rows so board_index/board_rows can read them."""
    by_key = {}
    for board in kd.get("boards") or []:
        key = board.get("key")
        if key:
            by_key[key] = board
    out = {}
    for name in BOARD_NAMES:
        board = by_key.get(name)
        if board and board.get("rows"):
            out[name] = {"board": board}
    return out


def fetch_live(key: str) -> tuple[dict, dict[str, dict], dict[str, dict]]:
    print(f"Fetching kingdom {KID} from {BASE} …")
    write_status(running=True, phase="boards", done=0, total=0, error=None)
    kd = api_get(f"/kingdoms/{KID}?include=boards&limit=100", key)
    if not kd.get("ok"):
        raise RuntimeError(f"Kingdom fetch failed: {kd}")

    payloads = payloads_from_kingdom(kd)
    for name in BOARD_NAMES:
        if name in payloads:
            continue
        limit = 10 if name == "alliance_power" else 100
        payloads[name] = api_get(f"/kingdoms/{KID}/ranks?board={name}&limit={limit}", key)

    persist_json(DATA / "kingdom-raw.json", kd)
    for name, payload in payloads.items():
        persist_json(DATA / f"{name}-raw.json", payload)

    ally_rows = board_rows(payloads["alliance_power"])
    top5_tags = []
    seen = set()
    for row in ally_rows:
        tag = canonical_tag(row.get("abbr"))
        if not tag or tag in seen:
            continue
        seen.add(tag)
        top5_tags.append(tag)
        if len(top5_tags) == 5:
            break
    print("Top 5 by alliance power:", top5_tags)
    rosters = {}
    for tag in top5_tags:
        print(f"  roster {tag} …")
        row = next((r for r in ally_rows if tags_match(r.get("abbr"), tag)), None)
        aid = row.get("aid") if row else None
        rost = None
        if aid:
            try:
                rost = api_get(f"/alliances/{KID}/{aid}?include=info,roster", key)
            except Exception as exc:
                print(f"    aid fetch failed ({exc}); trying tag {tag!r}")
        if not rost:
            last_error = None
            for alias in tag_aliases(tag):
                try:
                    rost = api_get(f"/alliances/{KID}/{urllib.parse.quote(alias)}?include=info,roster", key)
                    break
                except Exception as exc:
                    last_error = exc
            if not rost:
                raise RuntimeError(f"Could not fetch roster for [{tag}]: {last_error}")
        persist_json(DATA / f"roster-{tag}.json", rost)
        rosters[tag] = rost
    return kd, payloads, remap_roster_keys(rosters)


def slim_hero(hero: dict) -> dict:
    widget = hero.get("exclusive_gear") or None
    widget_out = None
    if isinstance(widget, dict) and widget.get("name"):
        widget_out = {
            "id": widget.get("id"),
            "name": widget.get("name"),
            "level": widget.get("level"),
            "icon": equip_icon(widget.get("id")),
        }
    gear = []
    for item in hero.get("gear") or []:
        eid = item.get("eid")
        gear.append(
            {
                "slot": item.get("slot"),
                "name": item.get("name"),
                "enhancement": item.get("enhancement_level"),
                "refine": item.get("refine_level"),
                "quality": item.get("quality"),
                "quality_label": item.get("quality_label") or item.get("quality_key"),
                "red": bool(item.get("red")),
                "troop": item.get("troop_label") or item.get("troop"),
                "eid": eid,
                "icon": equip_icon(eid),
            }
        )
    return {
        "id": hero.get("id"),
        "name": hero.get("name"),
        "level": hero.get("level"),
        "star": hero.get("star"),
        "stars": hero.get("stars"),
        "star_label": hero.get("star_label"),
        "quality": hero.get("quality"),
        "icon": abs_url(hero.get("icon")),
        "widget": widget_out,
        "gear": gear,
    }


def slim_gov_gear(payload: dict) -> dict:
    gg = payload.get("gov_gear") or {}
    if gg.get("hidden"):
        return {"hidden": True, "items": []}
    items = []
    for item in gg.get("items") or []:
        items.append(
            {
                "slot": item.get("slot"),
                "name": item.get("name"),
                "tier": item.get("tier"),
                "star": item.get("star"),
                "level": item.get("strength_level"),
                "score": item.get("score"),
                "icon": abs_url(item.get("icon")),
            }
        )
    return {"hidden": False, "items": items}


def slim_player(payload: dict) -> dict:
    pl = payload.get("player") or {}
    ranks = payload.get("ranks") or {}
    events = {}
    for name, key in EVENT_BOARDS:
        events[key] = None
    for row in ranks.get("leaderboards") or []:
        name = row.get("name")
        for label, key in EVENT_BOARDS:
            if name == label:
                events[key] = {
                    "value": row.get("value"),
                    "label": row.get("value_label"),
                    "rank": row.get("kingdom_rank"),
                    "rank_label": row.get("kingdom_rank_label"),
                }
    mystic_lb = next((r for r in (ranks.get("leaderboards") or []) if r.get("name") == "Mystic Trial"), None)
    return {
        "vip": pl.get("vip"),
        "x": pl.get("x"),
        "y": pl.get("y"),
        "kills": pl.get("kills") if pl.get("kills") is not None else ranks.get("kills"),
        "office": pl.get("office"),
        "online": pl.get("online"),
        "last_login": pl.get("last_login"),
        "last_active_at": pl.get("last_active_at"),
        "language": pl.get("language"),
        "shield_endtime": pl.get("shield_endtime"),
        "burn_endtime": pl.get("burn_endtime"),
        "avatar_url": abs_url(pl.get("avatar_url")),
        "power_rank": ranks.get("power_rank"),
        "kills_rank": ranks.get("kills_rank"),
        "tc_rank": ranks.get("town_center_rank"),
        "mystic": ranks.get("mystic_trial") if ranks.get("mystic_trial") is not None else (mystic_lb or {}).get("value"),
        "mystic_rank": ranks.get("mystic_rank") if ranks.get("mystic_rank") is not None else (mystic_lb or {}).get("kingdom_rank"),
        "events": events,
        "heroes": [slim_hero(h) for h in (payload.get("heroes") or []) if isinstance(h, dict)],
        "gov_gear": slim_gov_gear(payload),
        "alliance_aid": (pl.get("alliance") or {}).get("aid"),
        "alliance_abbr": canonical_tag((pl.get("alliance") or {}).get("abbr"))
        or (pl.get("alliance") or {}).get("abbr"),
    }


def player_cache_path(governor_id) -> Path:
    return PLAYERS / f"{governor_id}.json"


def load_player_cache(governor_id) -> dict | None:
    path = player_cache_path(governor_id)
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None


def fetch_players(key: str | None, rosters: dict[str, dict], refresh_existing: bool) -> dict[int, dict]:
    try:
        PLAYERS.mkdir(parents=True, exist_ok=True)
    except OSError:
        pass
    wanted = []
    seen = set()
    for rost in rosters.values():
        for m in rost.get("members") or []:
            gid = m.get("governor_id")
            if gid is None or gid in seen:
                continue
            seen.add(gid)
            wanted.append(m)

    out: dict[int, dict] = {}
    missing = []
    for m in wanted:
        gid = m["governor_id"]
        cached = load_player_cache(gid)
        if cached and not refresh_existing:
            out[int(gid)] = slim_player(cached) if "player" in cached else cached
            continue
        missing.append(m)

    total = len(wanted)
    done = total - len(missing)
    write_status(running=True, phase="players", done=done, total=total, error=None)
    print(f"Player pages: {done} cached, {len(missing)} to fetch", flush=True)

    if not missing:
        return out
    if not key:
        print("No API key — leaving uncached players empty.", file=sys.stderr)
        return out

    for i, m in enumerate(missing, 1):
        gid = m["governor_id"]
        name = m.get("nick_name")
        write_status(running=True, phase="players", done=done + i - 1, total=total, current=name, error=None)
        try:
            payload = api_get(f"/players/{gid}?include=base,heroes,ranks,gov_gear", key)
            persist_json(player_cache_path(gid), payload, indent=None)
            out[int(gid)] = slim_player(payload)
            print(f"  player {done + i}/{total} {name}", flush=True)
        except Exception as exc:
            print(f"  FAIL {name} ({gid}): {exc}", file=sys.stderr)
            write_status(error=str(exc))
    write_status(done=total, current=None)
    return out


def event_val(detail: dict | None, field: str):
    if not detail:
        return None
    return detail.get(field)


ALLIANCE_CAP = 100
# Kingshot troop power per unit. TC 26 is T9 (26–29), so T8 is TC 24–25.
TROOP_TIER_POWER = {"t10": 66, "t9": 50, "t8": 38}


def troop_tier_for_tc(tc) -> tuple[str | None, str | None, int | None]:
    try:
        level = int(tc)
    except (TypeError, ValueError):
        return None, None, None
    if level >= 30:
        return "t10", "T10", TROOP_TIER_POWER["t10"]
    if level >= 26:
        return "t9", "T9", TROOP_TIER_POWER["t9"]
    if level >= 24:
        return "t8", "T8", TROOP_TIER_POWER["t8"]
    return None, None, None


def current_members(rost: dict, official: dict | None, players: dict[int, dict]) -> tuple[list, list, int]:
    """Keep people who actually belong to this alliance, then cap at 100.

    MightPulse lookup-by-tag is case-insensitive, and [RCB] is now [SUN]
    SuperUnitedNexus. Player pages may still carry the old tag; treat those as
    the same alliance. Other tags are still dropped.
    """
    info = rost.get("alliance") or {}
    aid = (official or {}).get("aid") or info.get("aid")
    abbr = canonical_tag((official or {}).get("abbr") or info.get("abbr"))
    cap = (official or {}).get("member_count") or info.get("count") or ALLIANCE_CAP
    try:
        cap = min(int(cap), ALLIANCE_CAP)
    except (TypeError, ValueError):
        cap = ALLIANCE_CAP

    kept = []
    dropped = []
    for m in rost.get("members") or []:
        gid = m.get("governor_id")
        extra = players.get(int(gid)) if gid is not None else None
        their_aid = (extra or {}).get("alliance_aid")
        their_abbr = (extra or {}).get("alliance_abbr")
        if extra:
            if aid is not None and their_aid is not None and their_aid != aid:
                dropped.append((m.get("nick_name"), their_abbr, their_aid))
                continue
            if abbr and their_abbr and not tags_match(their_abbr, abbr):
                dropped.append((m.get("nick_name"), their_abbr, their_aid))
                continue
        kept.append(m)

    kept.sort(key=lambda m: -int(m.get("power") or 0))
    if len(kept) > cap:
        kept = kept[:cap]
    return kept, dropped, cap


def build_snapshot(
    kd: dict,
    payloads: dict[str, dict],
    rosters: dict[str, dict],
    players: dict[int, dict],
) -> dict:
    by_power = board_index(payloads["personal_power"])
    by_troop = board_index(payloads["troop_power"])
    by_building = board_index(payloads["building_power"])
    hero_idx = score_index(board_rows(payloads["hero_total"]))
    heq_idx = score_index(board_rows(payloads["hero_equip"]))
    lg_idx = score_index(board_rows(payloads["gov_gear"]))
    gem_idx = score_index(board_rows(payloads["gov_charm"]))
    ally_rows = board_rows(payloads["alliance_power"])
    ally_by_tag = {}
    for row in ally_rows:
        key = canonical_tag(row.get("abbr"))
        if key and key not in ally_by_tag:
            ally_by_tag[key] = row

    top5_tags = []
    seen = set()
    for row in ally_rows:
        tag = canonical_tag(row.get("abbr"))
        if not tag or tag in seen:
            continue
        if roster_for(tag, rosters) is None:
            continue
        seen.add(tag)
        top5_tags.append(tag)
        if len(top5_tags) == 5:
            break
    if not top5_tags:
        top5_tags = [canonical_tag(tag) or tag for tag in list(rosters.keys())[:5]]

    top5 = []
    all_members = []

    for tag in top5_tags:
        rost = roster_for(tag, rosters)
        if not rost:
            continue
        info = rost.get("alliance") or {}
        off = ally_by_tag.get(tag)
        members, dropped, cap = current_members(rost, off, players)
        if dropped:
            print(
                f"  [{tag}] dropped {len(dropped)} from other tags: "
                + ", ".join(f"{n} ({a})" for n, a, _ in dropped[:8])
            )
        print(f"  [{tag}] roster {len(rost.get('members') or [])} → {len(members)} (cap {cap})")

        tc30 = sum(1 for m in members if int(m.get("town_center_level") or 0) >= 30)
        tc29 = sum(1 for m in members if int(m.get("town_center_level") or 0) == 29)

        contributors = []
        n = len(members) or 1
        total_all = troop_all = building_all = combat_all = 0
        hero_all = gear_all = lord_all = gem_all = 0
        kills_all = 0
        players_fetched = 0
        troop_known_n = building_known_n = combat_known_n = 0
        hero_known_n = hero_gear_known_n = lord_gear_known_n = lord_gem_known_n = 0
        t10_troops = t9_troops = t8_troops = 0
        t10_from = t9_from = t8_from = 0
        t10_members = t9_members = t8_members = 0

        for m in members:
            uid = int(m.get("uid") or 0)
            gid = m.get("governor_id")
            power = int(m.get("power") or 0)
            if uid in by_power:
                power = by_power[uid]["score"]
            troop_known = uid in by_troop
            building_known = uid in by_building
            combat_known = troop_known and building_known
            troop = by_troop[uid]["score"] if troop_known else 0
            building = by_building[uid]["score"] if building_known else 0
            combat = power - troop - building if combat_known else None
            hero_known = uid in hero_idx
            hero_gear_known = uid in heq_idx
            lord_gear_known = uid in lg_idx
            lord_gem_known = uid in gem_idx
            hero = hero_idx.get(uid, 0)
            hero_gear = heq_idx.get(uid, 0)
            lord_gear = lg_idx.get(uid, 0)
            lord_gem = gem_idx.get(uid, 0)
            extra = players.get(int(gid)) if gid is not None else None
            if extra:
                players_fetched += 1

            kills = (extra or {}).get("kills")
            if kills is None:
                kills = m.get("kills")
            kills_n = int(kills or 0)

            total_all += power
            if troop_known:
                troop_all += troop
                troop_known_n += 1
            if building_known:
                building_all += building
                building_known_n += 1
            if combat_known:
                combat_all += combat
                combat_known_n += 1
            if hero_known:
                hero_all += hero
                hero_known_n += 1
            if hero_gear_known:
                gear_all += hero_gear
                hero_gear_known_n += 1
            if lord_gear_known:
                lord_all += lord_gear
                lord_gear_known_n += 1
            if lord_gem_known:
                gem_all += lord_gem
                lord_gem_known_n += 1
            kills_all += kills_n

            tier_key, tier_label, troop_each = troop_tier_for_tc(m.get("town_center_level"))
            troop_count = None
            if tier_key == "t10":
                t10_members += 1
            elif tier_key == "t9":
                t9_members += 1
            elif tier_key == "t8":
                t8_members += 1
            if troop_known and troop_each:
                troop_count = int(round(troop / troop_each))
                if tier_key == "t10":
                    t10_troops += troop_count
                    t10_from += 1
                elif tier_key == "t9":
                    t9_troops += troop_count
                    t9_from += 1
                elif tier_key == "t8":
                    t8_troops += troop_count
                    t8_from += 1

            events = (extra or {}).get("events") or {}
            avatar = (extra or {}).get("avatar_url") or abs_url(m.get("avatar_url"))
            if uid in by_power and by_power[uid].get("avatar_url"):
                avatar = abs_url(by_power[uid]["avatar_url"]) or avatar

            row = {
                "uid": uid or None,
                "governor_id": gid,
                "name": m.get("nick_name"),
                "alliance": canonical_tag(tag) or tag,
                "role": m.get("alliance_rank_label"),
                "tc": m.get("town_center_level"),
                "kills": kills_n,
                "power": power,
                "troop": troop if troop_known else None,
                "building": building if building_known else None,
                "combat": combat,
                "hero": hero if hero_known else None,
                "hero_gear": hero_gear if hero_gear_known else None,
                "lord_gear": lord_gear if lord_gear_known else None,
                "lord_gem": lord_gem if lord_gem_known else None,
                "troop_known": troop_known,
                "building_known": building_known,
                "combat_known": combat_known,
                "troop_tier": tier_key,
                "troop_tier_label": tier_label,
                "troop_each": troop_each,
                "troop_count": troop_count,
                "vip": (extra or {}).get("vip"),
                "x": (extra or {}).get("x"),
                "y": (extra or {}).get("y"),
                "office": (extra or {}).get("office"),
                "online": (extra or {}).get("online", m.get("online")),
                "last_login": (extra or {}).get("last_login"),
                "language": (extra or {}).get("language"),
                "power_rank": (extra or {}).get("power_rank"),
                "kills_rank": (extra or {}).get("kills_rank"),
                "tc_rank": (extra or {}).get("tc_rank"),
                "mystic": (extra or {}).get("mystic"),
                "mystic_rank": (extra or {}).get("mystic_rank"),
                "coliseum": event_val(events.get("coliseum"), "value"),
                "coliseum_label": event_val(events.get("coliseum"), "label"),
                "crystal_cave": event_val(events.get("crystal_cave"), "value"),
                "crystal_cave_label": event_val(events.get("crystal_cave"), "label"),
                "knowledge_nexus": event_val(events.get("knowledge_nexus"), "value"),
                "knowledge_nexus_label": event_val(events.get("knowledge_nexus"), "label"),
                "molten_fort": event_val(events.get("molten_fort"), "value"),
                "molten_fort_label": event_val(events.get("molten_fort"), "label"),
                "radiant_spire": event_val(events.get("radiant_spire"), "value"),
                "radiant_spire_label": event_val(events.get("radiant_spire"), "label"),
                "avatar_url": avatar,
                "heroes": (extra or {}).get("heroes") or [],
                "gov_gear": (extra or {}).get("gov_gear") or {"hidden": True, "items": []},
                "fetched": bool(extra),
            }
            contributors.append(row)
            all_members.append(row)

        contributors.sort(key=lambda c: (-(c["power"] or 0), -(c["combat"] or 0)))
        for i, c in enumerate(contributors, 1):
            c["rank"] = i

        top5.append(
            {
                "tag": canonical_tag(tag) or tag,
                "alliance_name": alliance_display_name(
                    tag, info.get("name") or (off.get("name") if off else None)
                ),
                "roster_count": len(members),
                "member_cap": cap,
                "roster_raw": len(rost.get("members") or []),
                "players_fetched": players_fetched,
                "official_power": int(off["score"]) if off else int(info.get("power") or 0) or None,
                "official_rank": off["rank"] if off else info.get("power_rank"),
                "power": total_all,
                "combat": combat_all,
                "troop": troop_all,
                "building": building_all,
                "hero": hero_all,
                "hero_gear": gear_all,
                "lord_gear": lord_all,
                "lord_gem": gem_all,
                "kills": kills_all,
                "combat_known": combat_known_n,
                "troop_known": troop_known_n,
                "building_known": building_known_n,
                "hero_known": hero_known_n,
                "hero_gear_known": hero_gear_known_n,
                "lord_gear_known": lord_gear_known_n,
                "lord_gem_known": lord_gem_known_n,
                "avg_member_power": round(total_all / n),
                "avg_troop": round(troop_all / troop_known_n) if troop_known_n else None,
                "avg_building": round(building_all / building_known_n) if building_known_n else None,
                "avg_combat": round(combat_all / combat_known_n) if combat_known_n else None,
                "share_of_top5_total": 0,
                "tc30": tc30,
                "tc29": tc29,
                "t10_members": t10_members,
                "t9_members": t9_members,
                "t8_members": t8_members,
                "t10_troops": t10_troops,
                "t9_troops": t9_troops,
                "t8_troops": t8_troops,
                "t10_from": t10_from,
                "t9_from": t9_from,
                "t8_from": t8_from,
                "avg_hero_power": round(hero_all / hero_known_n) if hero_known_n else None,
                "avg_hero_gear": round(gear_all / hero_gear_known_n) if hero_gear_known_n else None,
                "avg_lord_gear": round(lord_all / lord_gear_known_n) if lord_gear_known_n else None,
                "avg_lord_gem": round(gem_all / lord_gem_known_n) if lord_gem_known_n else None,
                "contributors": contributors,
            }
        )

    top5_total = sum(a["power"] for a in top5) or 1
    for a in top5:
        a["share_of_top5_total"] = round(100 * a["power"] / top5_total, 1)

    all_members.sort(key=lambda c: (-(c["power"] or 0), -(c["combat"] or 0)))
    for i, c in enumerate(all_members, 1):
        c["overall_rank"] = i

    captured = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    kingdom = kd["kingdom"]
    return {
        "kid": int(KID),
        "generated_at": captured,
        "source": BASE,
        "metric": {
            "key": "combat",
            "label": "Combat Power",
            "formula": "combat = total − troop − building",
            "note": "Combat, troop, and building only exist for governors on the kingdom top-100 boards, not the full alliance.",
        },
        "defs": {
            "total": "Governor personal power summed across every roster member.",
            "troop": "Troop Power from the kingdom top-100 troop board only — not the full alliance.",
            "building": "Building Power from the kingdom top-100 building board only — not the full alliance.",
            "combat": "Combat = total − troop − building, summed only for governors who appear on both the troop and building top-100 boards.",
            "tc30": "Furnace / town-center level ≥ 30, counted on the full alliance roster.",
            "troops": "Exact troop count = troop power ÷ 66 (T10, TC 30), 50 (T9, TC 26–29), or 38 (T8, TC 24–25). Only governors on the troop top-100 board have troop power.",
            "gear": "Hero / governor gear from the kingdom top-100 boards only — not the full alliance.",
            "kills": "Kill count from each player page (available for the full roster).",
            "ranks": "Kingdom standing from each player page — not limited to top 100.",
            "events": "Mystic Trial and event stages from each player page, including ranks past 100.",
        },
        "kingdom": {
            "player_count": kingdom.get("player_count"),
            "alliance_count": kingdom.get("alliance_count"),
            "power": kingdom.get("power"),
            "gov_power": kingdom.get("gov_power"),
            "alliance_power": kingdom.get("alliance_power"),
            "troop_power": kingdom.get("troop_power"),
            "building_power": kingdom.get("building_power"),
            "power_gain_7d": kingdom.get("power_gain_7d"),
            "power_rank": kingdom.get("power_rank"),
            "activity_rank": kingdom.get("activity_rank"),
            "health": kingdom.get("health"),
            "age_days": kingdom.get("age_days"),
            "opened_on": kingdom.get("opened_on"),
            "active_7d": kingdom.get("active_7d"),
        },
        "members": all_members,
        "top5": top5,
        "top3": top5[:3],
        "official_alliance_power": [
            {
                "rank": r["rank"],
                "tag": canonical_tag(r["abbr"]) or r["abbr"],
                "name": alliance_display_name(r.get("abbr"), r.get("name")),
                "power": int(r["score"]),
            }
            for r in ally_rows
        ],
        "totals": {
            "alliances": len(top5),
            "alliance_members": sum(a["roster_count"] for a in top5),
            "players_fetched": sum(a["players_fetched"] for a in top5),
            "alliance_total": sum(a["power"] for a in top5),
            "alliance_combat": sum(a["combat"] for a in top5),
            "alliance_troop": sum(a["troop"] for a in top5),
            "alliance_building": sum(a["building"] for a in top5),
            "alliance_hero": sum(a["hero"] for a in top5),
            "alliance_kills": sum(a["kills"] for a in top5),
            "combat_known": sum(a["combat_known"] for a in top5),
            "troop_known": sum(a["troop_known"] for a in top5),
            "building_known": sum(a["building_known"] for a in top5),
            "t10_troops": sum(a.get("t10_troops") or 0 for a in top5),
            "t9_troops": sum(a.get("t9_troops") or 0 for a in top5),
            "t8_troops": sum(a.get("t8_troops") or 0 for a in top5),
            "t10_from": sum(a.get("t10_from") or 0 for a in top5),
            "t9_from": sum(a.get("t9_from") or 0 for a in top5),
            "t8_from": sum(a.get("t8_from") or 0 for a in top5),
        },
    }


def extras_from_members(members: list, *, include_heroes: bool = True) -> dict[int, dict]:
    """Rebuild player extras from a previous snapshot so a boards-only refresh keeps kills/VIP/coords."""
    out: dict[int, dict] = {}
    for member in members:
        gid = member.get("governor_id")
        if gid is None:
            continue

        def event(key: str):
            value = member.get(key)
            label = member.get(f"{key}_label")
            if value is None and not label:
                return None
            return {"value": value, "label": label}

        extra = {
            "vip": member.get("vip"),
            "x": member.get("x"),
            "y": member.get("y"),
            "kills": member.get("kills"),
            "office": member.get("office"),
            "online": member.get("online"),
            "last_login": member.get("last_login"),
            "last_active_at": member.get("last_active_at"),
            "language": member.get("language"),
            "shield_endtime": member.get("shield_endtime"),
            "burn_endtime": member.get("burn_endtime"),
            "avatar_url": member.get("avatar_url"),
            "power_rank": member.get("power_rank"),
            "kills_rank": member.get("kills_rank"),
            "tc_rank": member.get("tc_rank"),
            "mystic": member.get("mystic"),
            "mystic_rank": member.get("mystic_rank"),
            "events": {
                "coliseum": event("coliseum"),
                "crystal_cave": event("crystal_cave"),
                "knowledge_nexus": event("knowledge_nexus"),
                "molten_fort": event("molten_fort"),
                "radiant_spire": event("radiant_spire"),
            },
            "alliance_aid": member.get("alliance_aid"),
            "alliance_abbr": canonical_tag(member.get("alliance")) or member.get("alliance"),
        }
        if include_heroes:
            extra["heroes"] = member.get("heroes") or []
            extra["gov_gear"] = member.get("gov_gear") or {"hidden": True, "items": []}
        out[int(gid)] = extra
    return out


def extras_from_snapshot(path: Path | None = None, *, include_heroes: bool = True) -> dict[int, dict]:
    path = path or (DATA / "snapshot.json")
    if not path.exists():
        return {}
    try:
        snap = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return extras_from_members(snap.get("members") or [], include_heroes=include_heroes)


def slim_client_snapshot(snapshot: dict) -> dict:
    """Drop hero/gear blobs so the Vercel response stays under the body limit."""

    def strip_row(row: dict) -> dict:
        return {key: value for key, value in row.items() if key not in ("heroes", "gov_gear")}

    top5 = []
    for alliance in snapshot.get("top5") or []:
        item = dict(alliance)
        item["contributors"] = [strip_row(row) for row in alliance.get("contributors") or []]
        top5.append(item)
    out = dict(snapshot)
    out["top5"] = top5
    out["top3"] = top5[:3]
    out["members"] = [strip_row(row) for row in snapshot.get("members") or []]
    return out


def fast_refresh(*, persist: bool | None = None) -> dict:
    """Live boards + rosters. Reuse player pages from snapshot/cache (no 10-minute crawl)."""
    if persist is None:
        persist = not on_vercel()
    key = load_api_key()
    if not key:
        raise RuntimeError("KINGSHOT_API_KEY is not set.")
    write_status(running=True, phase="boards", done=0, total=0, error=None, generated_at=None)
    kd, payloads, rosters = fetch_live(key)
    players = extras_from_snapshot(include_heroes=persist)
    if persist:
        players.update(fetch_players(None, rosters, refresh_existing=False))
    snapshot = build_snapshot(kd, payloads, rosters, players)
    snapshot["refresh_mode"] = "boards"
    if persist:
        persist_json(DATA / "snapshot.json", snapshot, indent=None)
        write_history(snapshot)
    write_status(
        running=False,
        phase="done",
        done=snapshot["totals"]["players_fetched"],
        total=snapshot["totals"]["alliance_members"],
        current=None,
        error=None,
        generated_at=snapshot["generated_at"],
    )
    return snapshot


def write_history(snapshot: dict) -> None:
    if on_vercel():
        return
    hist_path = DATA / "history.json"
    history = json.loads(hist_path.read_text()) if hist_path.exists() else {"kid": int(KID), "points": []}
    for point in history.get("points") or []:
        combat = point.get("alliance_combat")
        if isinstance(combat, dict):
            point["alliance_combat"] = {
                canonical_tag(tag) or tag: value for tag, value in combat.items()
            }
        for group in ("top5", "top3"):
            for row in point.get(group) or []:
                if isinstance(row, dict) and row.get("tag"):
                    row["tag"] = canonical_tag(row["tag"]) or row["tag"]
    captured = snapshot["generated_at"]
    kingdom = snapshot["kingdom"]
    top5 = snapshot["top5"]
    point = {
        "at": captured,
        "alliance_total": snapshot["totals"]["alliance_total"],
        "alliance_combat": {a["tag"]: a["combat"] for a in top5},
        "alliance_kills": snapshot["totals"].get("alliance_kills"),
        "kingdom_power": kingdom.get("power"),
        "kingdom_power_gain_7d": kingdom.get("power_gain_7d"),
        "top5": [
            {
                "tag": a["tag"],
                "power": a["power"],
                "combat": a["combat"],
                "troop": a["troop"],
                "building": a["building"],
                "kills": a["kills"],
                "tc30": a["tc30"],
            }
            for a in top5
        ],
    }
    if (
        not history["points"]
        or history["points"][-1].get("alliance_total") != point["alliance_total"]
        or history["points"][-1].get("at", "")[:10] != captured[:10]
    ):
        history["points"].append(point)
    else:
        history["points"][-1] = point
    hist_path.write_text(json.dumps(history, ensure_ascii=False, indent=2) + "\n")


def main() -> int:
    cached = "--cached" in sys.argv
    offline = "--offline" in sys.argv
    refresh_players = "--refresh-players" in sys.argv
    boards_only = "--boards" in sys.argv
    DATA.mkdir(parents=True, exist_ok=True)
    write_status(running=True, phase="start", done=0, total=0, error=None, generated_at=None)

    if boards_only:
        snapshot = fast_refresh(persist=True)
        print(f"Members across Top 5: {snapshot['totals']['alliance_members']}")
        print(f"Player pages reused: {snapshot['totals']['players_fetched']}")
        for i, a in enumerate(snapshot["top5"], 1):
            print(
                f"  #{i} [{a['tag']}] members={a['roster_count']} fetched={a['players_fetched']} "
                f"total={a['power']:,} combat={a['combat']:,} "
                f"combat_from={a['combat_known']}/{a['roster_count']} kills={a['kills']:,}"
            )
        return 0

    key = None if offline else load_api_key()
    if cached or offline:
        kd, payloads, rosters = load_cached_payloads()
    else:
        if not key:
            print("Set KINGSHOT_API_KEY first (https://api.mightpulse.com).", file=sys.stderr)
            write_status(running=False, error="KINGSHOT_API_KEY is not set.")
            return 1
        kd, payloads, rosters = fetch_live(key)

    players = fetch_players(None if offline else key, rosters, refresh_existing=refresh_players and not offline)
    snapshot = build_snapshot(kd, payloads, rosters, players)
    snapshot["refresh_mode"] = "full"
    persist_json(DATA / "snapshot.json", snapshot, indent=None)
    write_history(snapshot)
    write_status(
        running=False,
        phase="done",
        done=snapshot["totals"]["players_fetched"],
        total=snapshot["totals"]["alliance_members"],
        current=None,
        error=None,
        generated_at=snapshot["generated_at"],
    )

    print(f"Members across Top 5: {snapshot['totals']['alliance_members']}")
    print(f"Player pages: {snapshot['totals']['players_fetched']}")
    for i, a in enumerate(snapshot["top5"], 1):
        print(
            f"  #{i} [{a['tag']}] members={a['roster_count']} fetched={a['players_fetched']} "
            f"total={a['power']:,} combat={a['combat']:,} "
            f"combat_from={a['combat_known']}/{a['roster_count']} kills={a['kills']:,}"
        )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except urllib.error.HTTPError as e:
        msg = f"HTTP {e.code}: {e.read().decode('utf-8', errors='replace')}"
        print(msg, file=sys.stderr)
        write_status(running=False, error=msg)
        raise SystemExit(1)
    except Exception as e:
        print(e, file=sys.stderr)
        write_status(running=False, error=str(e))
        raise SystemExit(1)
