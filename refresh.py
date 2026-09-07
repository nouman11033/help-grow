#!/usr/bin/env python3
"""Refresh kingdom stats from MightPulse.

Pass a kingdom number from the dashboard. Rankings and rosters are the official
top 5 alliances only.
"""

from __future__ import annotations

import json
import os
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
class RecallKilled(RuntimeError):
    """Raised when the user hits Kill during a recall."""
BOARD_NAMES = (
    "personal_power",
    "kills",
    "troop_power",
    "building_power",
    "alliance_power",
    "hero_total",
    "hero_equip",
    "gov_gear",
    "gov_charm",
)
HIDDEN_BOARDS = {
    "town_center",
    "gov_charm",
    "island_prosperity",
    "migrant_score",
    "mystic_trial",
    "coliseum",
    "crystal_cave",
    "knowledge_nexus",
    "molten_fort",
    "radiant_spire",
    "rebel_conquest",
    "pet_power",
    "forest_of_life",
}
PLAYER_BOARD_ORDER = (
    "personal_power",
    "combat",
    "troop_power",
    "building_power",
    "kills",
    "town_center",
    "hero_total",
    "hero_equip",
    "hero_no_equip",
    "single_hero",
    "gov_gear",
    "gov_charm",
    "research_power",
    "island_prosperity",
    "migrant_score",
    "mystic_trial",
    "coliseum",
    "crystal_cave",
    "knowledge_nexus",
    "molten_fort",
    "radiant_spire",
    "rebel_conquest",
    "pet_power",
    "master_power",
    "forest_of_life",
)
BOARD_LABELS = {
    "personal_power": "Personal power",
    "combat": "Combat",
    "troop_power": "Troop",
    "building_power": "Building",
    "kills": "Kills",
    "town_center": "Town center",
    "hero_total": "Hero total",
    "hero_equip": "Hero gear",
    "hero_no_equip": "Hero without gear",
    "single_hero": "Single hero",
    "gov_gear": "Lord gear",
    "gov_charm": "Gems",
    "research_power": "Research",
    "island_prosperity": "Island",
    "migrant_score": "Migrant score",
    "mystic_trial": "Mystic Trial",
    "coliseum": "Coliseum",
    "crystal_cave": "Crystal Cave",
    "knowledge_nexus": "Knowledge Nexus",
    "molten_fort": "Molten Fort",
    "radiant_spire": "Radiant Spire",
    "rebel_conquest": "Rebel Conquest",
    "pet_power": "Pets",
    "master_power": "Master power",
    "forest_of_life": "Forest of Life",
}
EVENT_BOARDS = (
    ("Coliseum", "coliseum"),
    ("Crystal Cave", "crystal_cave"),
    ("Knowledge Nexus", "knowledge_nexus"),
    ("Molten Fort", "molten_fort"),
    ("Radiant Spire", "radiant_spire"),
)
REQUEST_GAP = 1.1  # stay under 60/minute

TOP_ALLIANCE_COUNT = 5
# Stable MightPulse alliance id. Tag/name can change (RCB → SUN SuperUnitedNexus).
AID_IDENTITY = {
    237100006: ("SUN", "SuperUnitedNexus"),
}
# Extra tags to try when the live board tag 404s or hits a different alliance.
AID_TAG_FALLBACKS = {
    237100006: ("RCB", "SUN"),
}


def parse_kid(value) -> str:
    text = str(value or "").strip()
    if not text.isdigit():
        raise ValueError("Enter a kingdom number.")
    number = int(text)
    if number < 1 or number > 99_999:
        raise ValueError("Enter a valid kingdom number.")
    return str(number)


def set_kid(value=None) -> str:
    global KID
    KID = parse_kid(KID if value is None else value)
    return KID


def identity_locked() -> bool:
    return str(KID) == "2362"


def cancel_flag_path() -> Path:
    if on_vercel():
        return Path("/tmp") / f"help-grow-cancel-{KID}"
    return DATA / "refresh-cancel.flag"


def clear_cancel() -> None:
    try:
        cancel_flag_path().unlink(missing_ok=True)
    except OSError:
        pass


def request_cancel() -> None:
    path = cancel_flag_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("1", encoding="utf-8")
    except OSError:
        pass


def kill_runtime(kid=None) -> str:
    """Stop an in-flight recall and drop the live status so clients do not reload it."""
    if kid is not None and str(kid).strip():
        set_kid(kid)
    request_cancel()
    write_status(running=False, phase="killed", error="Recall killed.", kid=KID)
    return KID


def check_cancel() -> None:
    if cancel_flag_path().exists():
        raise RecallKilled("Recall killed.")


def snapshot_file(kid=None) -> Path:
    current = str(kid or KID)
    if current == "2362":
        return DATA / "snapshot.json"
    return DATA / f"snapshot-{current}.json"


def as_aid(value) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def alliance_aid(obj) -> int | None:
    if not isinstance(obj, dict):
        return None
    return as_aid(obj.get("aid")) or alliance_aid(obj.get("alliance"))


def ranked_alliance_rows(ally_rows: list) -> list:
    """Official kingdom alliance-power board, ranks 1–5, unique by aid."""
    rows = [row for row in ally_rows if as_aid(row.get("rank")) is not None]
    rows.sort(key=lambda row: (int(row.get("rank") or 999), -int(row.get("score") or 0)))
    out = []
    seen: set[int] = set()
    for row in rows:
        aid = alliance_aid(row)
        if aid is None or aid in seen:
            continue
        seen.add(aid)
        out.append(row)
        if len(out) == TOP_ALLIANCE_COUNT:
            break
    return out


def display_identity(aid, tag=None, name=None) -> tuple[str, str]:
    if identity_locked() and as_aid(aid) in AID_IDENTITY:
        return AID_IDENTITY[int(aid)]
    text = str(tag or "").strip()
    given = str(name or "").strip()
    return text, given or text


def roster_for_aid(aid, rosters: dict):
    key = as_aid(aid)
    if key is None:
        return None
    return rosters.get(key) or rosters.get(str(key))


def roster_lookup_tags(row: dict) -> list[str]:
    """Tags MightPulse will accept. Never put the numeric aid in the URL."""
    tags = []
    official = str(row.get("abbr") or "").strip()
    if official:
        tags.append(official)
    if identity_locked():
        known = AID_IDENTITY.get(as_aid(alliance_aid(row)))
        if known and known[0] and known[0] not in tags:
            tags.append(known[0])
        for extra in AID_TAG_FALLBACKS.get(as_aid(alliance_aid(row)) or -1, ()):
            if extra and extra not in tags:
                tags.append(extra)
    return tags


def roster_member_count(rost: dict) -> int:
    members = rost.get("members")
    if isinstance(members, list):
        return len(members)
    alliance = rost.get("alliance") if isinstance(rost.get("alliance"), dict) else {}
    return int(alliance.get("member_count") or rost.get("member_count") or 0)


def cached_roster_for_aid(aid: int | None):
    if aid is None:
        return None
    paths = [DATA / f"roster-{aid}.json", *sorted(DATA.glob("roster-*.json"))]
    seen: set[Path] = set()
    for path in paths:
        if path in seen or not path.exists():
            continue
        seen.add(path)
        try:
            rost = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if alliance_aid(rost) != aid:
            continue
        rost_kid = as_aid(rost.get("kid")) or as_aid((rost.get("alliance") or {}).get("kid"))
        if rost_kid is not None and str(rost_kid) != str(KID):
            continue
        return rost
    return None


def fetch_alliance_roster(row: dict, key: str) -> dict:
    """GET /alliances/{kid}/{tag} only. Aid in that slot returns alliance_not_found."""
    aid = alliance_aid(row)
    official_count = as_aid(row.get("member_count"))
    last_error = None
    for tag in roster_lookup_tags(row):
        path = f"/alliances/{KID}/{urllib.parse.quote(tag, safe='')}?include=info,roster"
        try:
            rost = api_get(path, key)
        except RuntimeError as exc:
            last_error = exc
            print(f"    [{tag}] {exc}")
            continue
        got = alliance_aid(rost)
        if aid is not None and got is not None and got != aid:
            print(f"    [{tag}] is aid {got}, wanted {aid} — skip")
            continue
        got_count = roster_member_count(rost)
        if (
            got is None
            and official_count
            and got_count
            and got_count < max(20, official_count // 3)
        ):
            print(
                f"    [{tag}] roster {got_count} too small vs official {official_count} — skip"
            )
            continue
        print(
            f"    [{tag}] age={rost.get('age_seconds')}s fresh={rost.get('fresh')} members={got_count}"
        )
        return rost
    cached = cached_roster_for_aid(aid)
    if cached:
        print(f"    using cached roster for aid {aid}")
        return cached
    raise last_error or RuntimeError(
        f"Could not load roster for rank #{row.get('rank')} aid={aid} [{row.get('abbr')}]"
    )


def plausible_kills(value) -> int | None:
    """Roster/player `kills` is sometimes a unix timestamp (~1.78e9), not a kill count."""
    try:
        n = int(value)
    except (TypeError, ValueError):
        return None
    if n < 0:
        return None
    if 1_700_000_000 <= n <= 2_000_000_000:
        return None
    if n > 80_000_000:
        return None
    return n


def member_tc(member) -> int | None:
    """Furnace / town-center level from a live roster row."""
    ranks = member.get("ranks") if isinstance(member.get("ranks"), dict) else {}
    for src in (member, ranks):
        if not isinstance(src, dict):
            continue
        for key in ("town_center_level", "tc_level", "furnace_level", "furnace", "tc"):
            value = src.get(key)
            if value is None or value == "":
                continue
            try:
                return int(value)
            except (TypeError, ValueError):
                continue
    return None


def history_tag(tag) -> str:
    text = str(tag or "").strip()
    if identity_locked() and text.upper() == "RCB":
        return "SUN"
    return text


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
    check_cancel()
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
            "alliance": row.get("alliance_abbr") or "—",
            "score": int(row.get("score") or 0),
            "rank": row.get("rank"),
            "avatar_url": row.get("avatar_url"),
        }
    return out


def board_rows(payload: dict) -> list:
    return (payload.get("board") or {}).get("rows") or []


def board_kind(payload: dict) -> str:
    return str((payload.get("board") or {}).get("kind") or "")


def board_label(key: str, payload: dict | None = None) -> str:
    name = ((payload or {}).get("board") or {}).get("name") if payload else None
    return BOARD_LABELS.get(key) or str(name or key).replace("_", " ").title()


def board_row_tag(row: dict, aid_to_tag: dict[int, str], tags: set[str]) -> str | None:
    aid = alliance_aid(row)
    if aid is not None:
        return aid_to_tag.get(aid)
    raw = row.get("alliance_abbr") or row.get("abbr")
    tag, _ = display_identity(None, raw)
    if tag in tags:
        return tag
    upper = {item.upper(): item for item in tags}
    if tag and tag.upper() in upper:
        return upper[tag.upper()]
    return None


def count_board_seats(rows: list, aid_to_tag: dict[int, str], tags: list[str]) -> dict:
    seats = [row for row in rows[:100] if isinstance(row, dict)]
    known = set(tags)
    counts = {tag: 0 for tag in tags}
    other = 0
    for row in seats:
        tag = board_row_tag(row, aid_to_tag, known)
        if tag in counts:
            counts[tag] += 1
        else:
            other += 1
    return {
        "seats": len(seats),
        "counts": counts,
        "other": other,
        "split": "/".join(str(counts[tag]) for tag in tags),
    }


def combat_board_rows(payloads: dict[str, dict]) -> list:
    """Governors on personal + troop + building boards, ranked by combat = total − troop − building."""
    power = {int(row["uid"]): row for row in board_rows(payloads.get("personal_power") or {}) if row.get("uid") is not None}
    troop = {int(row["uid"]): row for row in board_rows(payloads.get("troop_power") or {}) if row.get("uid") is not None}
    building = {int(row["uid"]): row for row in board_rows(payloads.get("building_power") or {}) if row.get("uid") is not None}
    ranked = []
    for uid, prow in power.items():
        trow = troop.get(uid)
        brow = building.get(uid)
        if trow is None or brow is None:
            continue
        combat = int(prow.get("score") or 0) - int(trow.get("score") or 0) - int(brow.get("score") or 0)
        row = dict(prow)
        row["score"] = combat
        ranked.append(row)
    ranked.sort(key=lambda row: -int(row.get("score") or 0))
    out = []
    for index, row in enumerate(ranked[:100], 1):
        item = dict(row)
        item["rank"] = index
        out.append(item)
    return out


def build_board_share(payloads: dict[str, dict], top5: list[dict]) -> list[dict]:
    tags = [str(alliance.get("tag") or "") for alliance in top5 if alliance.get("tag")]
    if not tags:
        return []
    aid_to_tag: dict[int, str] = {}
    for alliance in top5:
        aid = as_aid(alliance.get("aid"))
        tag = str(alliance.get("tag") or "")
        if aid is not None and tag:
            aid_to_tag[aid] = tag

    items: list[dict] = []

    def add(key: str, rows: list, *, payload=None, derived=False, note=None) -> None:
        if key in HIDDEN_BOARDS or not rows:
            return
        counted = count_board_seats(rows, aid_to_tag, tags)
        items.append({
            "key": key,
            "label": board_label(key, payload),
            "derived": derived,
            "note": note,
            "tags": tags,
            **counted,
        })

    add(
        "personal_power",
        board_rows(payloads.get("personal_power") or {}),
        payload=payloads.get("personal_power"),
    )
    add(
        "combat",
        combat_board_rows(payloads),
        derived=True,
        note="Not an official board. Ranked from governors on personal, troop, and building top 100: combat = total − troop − building.",
    )
    seen = {"personal_power", "combat", "alliance_power", "alliance_kills"}
    rest = []
    for key, payload in payloads.items():
        if key in seen or key in HIDDEN_BOARDS or board_kind(payload) == "alliance":
            continue
        rest.append(key)
    rest.sort(key=lambda key: (PLAYER_BOARD_ORDER.index(key) if key in PLAYER_BOARD_ORDER else 99, key))
    for key in rest:
        payload = payloads[key]
        add(key, board_rows(payload), payload=payload)
        seen.add(key)
    return items


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
    payloads = payloads_from_kingdom(kd)
    for name in BOARD_NAMES:
        if name in payloads:
            continue
        path = DATA / f"{name}-raw.json"
        if path.exists():
            payloads[name] = json.loads(path.read_text(encoding="utf-8"))
        else:
            payloads[name] = {"board": {"rows": []}}
    rosters = {}
    for path in sorted(DATA.glob("roster-*.json")):
        rost = json.loads(path.read_text(encoding="utf-8"))
        aid = alliance_aid(rost)
        if aid is None:
            continue
        rosters[aid] = rost
    return kd, payloads, rosters


def payloads_from_kingdom(kd: dict) -> dict[str, dict]:
    """Wrap kingdom `include=boards` rows so board_index/board_rows can read them."""
    out = {}
    for board in kd.get("boards") or []:
        key = board.get("key")
        if key and board.get("rows"):
            out[key] = {"board": board}
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
    ranked = ranked_alliance_rows(ally_rows)
    print(
        "Top 5 by official rank:",
        [(row.get("rank"), row.get("abbr"), row.get("aid")) for row in ranked],
    )
    rosters = {}
    for row in ranked:
        check_cancel()
        aid = alliance_aid(row)
        if aid is None:
            raise RuntimeError(f"Alliance rank #{row.get('rank')} has no aid.")
        print(f"  roster #{row.get('rank')} aid={aid} [{row.get('abbr')}] …")
        rost = fetch_alliance_roster(row, key)
        persist_json(DATA / f"roster-{aid}.json", rost)
        rosters[aid] = rost
    return kd, payloads, rosters


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
        "alliance_abbr": (pl.get("alliance") or {}).get("abbr"),
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


def current_members(rost: dict, official: dict | None, players: dict[int, dict] | None = None) -> tuple[list, list, int]:
    """Keep the live roster. Stale player pages must not drop people who are on it."""
    info = rost.get("alliance") or {}
    cap = (official or {}).get("member_count") or info.get("count") or ALLIANCE_CAP
    try:
        cap = min(int(cap), ALLIANCE_CAP)
    except (TypeError, ValueError):
        cap = ALLIANCE_CAP

    kept = list(rost.get("members") or [])
    kept.sort(key=lambda m: -int(m.get("power") or 0))
    if len(kept) > cap:
        kept = kept[:cap]
    return kept, [], cap


def build_snapshot(
    kd: dict,
    payloads: dict[str, dict],
    rosters: dict[str, dict],
    players: dict[int, dict],
) -> dict:
    by_power = board_index(payloads["personal_power"])
    by_kills = board_index(payloads.get("kills") or {"board": {"rows": []}})
    by_troop = board_index(payloads["troop_power"])
    by_building = board_index(payloads["building_power"])
    hero_idx = score_index(board_rows(payloads["hero_total"]))
    heq_idx = score_index(board_rows(payloads["hero_equip"]))
    lg_idx = score_index(board_rows(payloads["gov_gear"]))
    gem_idx = score_index(board_rows(payloads["gov_charm"]))
    ally_rows = board_rows(payloads["alliance_power"])
    ranked = ranked_alliance_rows(ally_rows)

    top5 = []
    all_members = []

    for off in ranked:
        aid = alliance_aid(off)
        rost = roster_for_aid(aid, rosters)
        if not rost:
            print(f"  rank #{off.get('rank')} aid={aid} has no roster — skipped")
            continue
        info = rost.get("alliance") or {}
        tag, alliance_name = display_identity(
            aid,
            info.get("abbr") or off.get("abbr"),
            info.get("name") or off.get("name"),
        )
        members, dropped, cap = current_members(rost, off, players)
        if dropped:
            print(
                f"  [{tag}] dropped {len(dropped)} from other alliances: "
                + ", ".join(f"{n} ({a})" for n, a, _ in dropped[:8])
            )
        print(
            f"  #{off.get('rank')} [{tag}] roster {len(rost.get('members') or [])} "
            f"→ {len(members)} (cap {cap})"
        )

        tc30 = sum(1 for m in members if (member_tc(m) or 0) >= 30)
        tc29 = sum(1 for m in members if (member_tc(m) or 0) == 29)

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
            # Roster has every member; kingdom boards are newer for people in top 100.
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

            tc = member_tc(m)
            board_kills = plausible_kills(by_kills[uid]["score"]) if uid in by_kills else None
            roster_kills = plausible_kills(m.get("kills"))
            extra_kills = plausible_kills((extra or {}).get("kills"))
            if board_kills is not None:
                kills_n = board_kills
            elif roster_kills is not None:
                kills_n = roster_kills
            else:
                kills_n = extra_kills or 0

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

            tier_key, tier_label, troop_each = troop_tier_for_tc(tc)
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
                "alliance": tag,
                "role": m.get("alliance_rank_label"),
                "tc": tc,
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
                "tag": tag,
                "alliance_name": alliance_name,
                "aid": aid,
                "roster_count": len(members),
                "member_cap": cap,
                "roster_raw": len(rost.get("members") or []),
                "players_fetched": players_fetched,
                "official_power": int(off["score"]) if off else int(info.get("power") or 0) or None,
                "official_rank": off.get("rank") or info.get("power_rank"),
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

    top5.sort(key=lambda alliance: alliance.get("official_rank") or 99)
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
        "board_share": build_board_share(payloads, top5),
        "official_alliance_power": [
            {
                "rank": r["rank"],
                "tag": display_identity(alliance_aid(r), r.get("abbr"), r.get("name"))[0] or r["abbr"],
                "name": display_identity(alliance_aid(r), r.get("abbr"), r.get("name"))[1],
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
            "alliance_abbr": member.get("alliance"),
        }
        if include_heroes:
            extra["heroes"] = member.get("heroes") or []
            extra["gov_gear"] = member.get("gov_gear") or {"hidden": True, "items": []}
        out[int(gid)] = extra
    return out


def extras_from_snapshot(path: Path | None = None, *, include_heroes: bool = True) -> dict[int, dict]:
    path = path or snapshot_file()
    if not path.exists():
        return {}
    try:
        snap = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    snap_kid = as_aid(snap.get("kid"))
    if snap_kid is not None and str(snap_kid) != str(KID):
        return {}
    return extras_from_members(snap.get("members") or [], include_heroes=include_heroes)


HEAVY_KEYS = ("heroes", "gov_gear")


def strip_heavy(row: dict) -> dict:
    return {key: value for key, value in row.items() if key not in HEAVY_KEYS}


def snapshot_for_web(snapshot: dict) -> dict:
    """Keep hero/gear on `members` only. Contributors were the same objects, so
    JSON duplicated ~3MB of blobs and the static page could sit blank while it parsed."""
    out = dict(snapshot)
    top5 = []
    for alliance in snapshot.get("top5") or []:
        item = dict(alliance)
        item["contributors"] = [strip_heavy(row) for row in alliance.get("contributors") or []]
        top5.append(item)
    out["top5"] = top5[:TOP_ALLIANCE_COUNT]
    out["top3"] = top5[:3]
    return out


def slim_client_snapshot(snapshot: dict) -> dict:
    """Drop hero/gear blobs so the Vercel response stays under the body limit."""
    top5 = []
    for alliance in snapshot.get("top5") or []:
        item = dict(alliance)
        item["contributors"] = [strip_heavy(row) for row in alliance.get("contributors") or []]
        top5.append(item)
    out = dict(snapshot)
    out["top5"] = top5[:TOP_ALLIANCE_COUNT]
    out["top3"] = top5[:3]
    out["members"] = [strip_heavy(row) for row in snapshot.get("members") or []]
    return out


def persist_snapshot(snapshot: dict) -> None:
    web = snapshot_for_web(snapshot)
    persist_json(snapshot_file(), web, indent=None)
    if str(KID) == "2362" and snapshot_file() != DATA / "snapshot.json":
        persist_json(DATA / "snapshot.json", web, indent=None)


def fast_refresh(*, persist: bool | None = None, kid=None) -> dict:
    """Live boards + top-5 rosters for the given kingdom."""
    if kid is not None:
        set_kid(kid)
    else:
        set_kid(KID)
    if persist is None:
        persist = not on_vercel()
    key = load_api_key()
    if not key:
        raise RuntimeError("KINGSHOT_API_KEY is not set.")
    clear_cancel()
    write_status(running=True, phase="boards", done=0, total=0, error=None, generated_at=None, kid=KID)
    try:
        kd, payloads, rosters = fetch_live(key)
        players = extras_from_snapshot(include_heroes=persist)
        if persist:
            players.update(fetch_players(key, rosters, refresh_existing=False))
        snapshot = build_snapshot(kd, payloads, rosters, players)
        snapshot["refresh_mode"] = "boards"
        if persist:
            persist_snapshot(snapshot)
            write_history(snapshot)
        write_status(
            running=False,
            phase="done",
            done=snapshot["totals"]["players_fetched"],
            total=snapshot["totals"]["alliance_members"],
            current=None,
            error=None,
            generated_at=snapshot["generated_at"],
            kid=KID,
        )
        return snapshot
    except RecallKilled as exc:
        write_status(running=False, phase="killed", error=str(exc), kid=KID)
        raise


def write_history(snapshot: dict) -> None:
    if on_vercel():
        return
    hist_path = DATA / "history.json"
    history = json.loads(hist_path.read_text()) if hist_path.exists() else {"kid": int(KID), "points": []}
    if history.get("kid") is not None and str(history.get("kid")) != str(KID):
        return
    for point in history.get("points") or []:
        combat = point.get("alliance_combat")
        if isinstance(combat, dict):
            point["alliance_combat"] = {
                history_tag(tag): value for tag, value in combat.items()
            }
        for group in ("top5", "top3"):
            for row in point.get(group) or []:
                if isinstance(row, dict) and row.get("tag"):
                    row["tag"] = history_tag(row["tag"])
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


def argv_value(flag: str) -> str | None:
    args = sys.argv[1:]
    if flag in args:
        index = args.index(flag)
        if index + 1 < len(args):
            return args[index + 1]
    prefix = flag + "="
    for arg in args:
        if arg.startswith(prefix):
            return arg[len(prefix):]
    return None


def main() -> int:
    cached = "--cached" in sys.argv
    offline = "--offline" in sys.argv
    refresh_players = "--refresh-players" in sys.argv
    boards_only = "--boards" in sys.argv
    kid_arg = argv_value("--kid")
    if kid_arg:
        set_kid(kid_arg)
    DATA.mkdir(parents=True, exist_ok=True)
    write_status(running=True, phase="start", done=0, total=0, error=None, generated_at=None)

    if boards_only:
        snapshot = fast_refresh(persist=True, kid=KID)
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
    persist_snapshot(snapshot)
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
    except RecallKilled as e:
        print(e, file=sys.stderr)
        write_status(running=False, phase="killed", error=str(e))
        raise SystemExit(1)
    except urllib.error.HTTPError as e:
        msg = f"HTTP {e.code}: {e.read().decode('utf-8', errors='replace')}"
        print(msg, file=sys.stderr)
        write_status(running=False, error=msg)
        raise SystemExit(1)
    except Exception as e:
        print(e, file=sys.stderr)
        write_status(running=False, error=str(e))
        raise SystemExit(1)
