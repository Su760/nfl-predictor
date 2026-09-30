"""Public usage snapshots with isolated receipts and fail-closed optional metrics."""
import csv
import hashlib
import io
import json
import math
import subprocess
import tempfile
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path

from fantasy.config import configuration
from fantasy.usage import number

REQUIRED = {
    "players": {"season", "season_type", "game_id", "player_id", "team", "position",
                "player_display_name", "targets", "carries", "receiving_air_yards"},
    "teams": {"season", "season_type", "game_id", "team", "targets", "carries"},
    "schedule": {"season", "game_type", "game_id", "week", "gameday", "home_team",
                 "away_team", "home_score", "away_score"},
    "snaps": {"season", "game_type", "game_id", "team", "pfr_player_id", "position",
              "player", "offense_snaps", "offense_pct"},
    "ids": {"gsis_id", "pfr_id"},
    "pbp": {"game_id", "play_id", "posteam", "play_type", "play_deleted", "desc",
            "two_point_attempt", "pass_attempt", "rush_attempt", "receiver_player_id",
            "rusher_player_id", "yardline_100"},
}


def snap_denominator(rows):
    """Find a unique integer compatible with every count and 2-decimal PFR fraction."""
    pairs = [(number(r.get("offense_snaps")), number(r.get("offense_pct"))) for r in rows]
    if not pairs or any(n is None or p is None or n < 0 or n != int(n) or not 0 <= p <= 1
                        for n, p in pairs):
        return None
    bounds = [math.floor(n / (p - .005)) for n, p in pairs if p > .005 and n > 0]
    if not bounds:
        return None
    low, high = int(max(n for n, _ in pairs)), min(bounds)
    candidates = []
    for total in range(max(1, low), high + 1):
        if all(abs(n / total - p) <= .005 + 1e-10 for n, p in pairs):
            candidates.append(total)
            if len(candidates) > 1:
                return None
    return candidates[0] if candidates else None


def red_zone(plays, player_id, team, targets, carries):
    if targets is None or carries is None or not any(
        str(p.get("desc", "")).strip().upper() == "END GAME" for p in plays
    ):
        return None
    keys = [p.get("play_id") for p in plays]
    if None in keys or len(keys) != len(set(keys)):
        return None
    opportunities, counted_targets, counted_carries = [], 0, 0
    for p in plays:
        if p.get("posteam") != team or p.get("play_type") not in {"pass", "run", "qb_kneel", "qb_spike"}:
            continue
        if number(p.get("play_deleted")) != 0 or number(p.get("two_point_attempt")) != 0:
            continue
        target = number(p.get("pass_attempt")) == 1 and p.get("receiver_player_id") == player_id
        carry = number(p.get("rush_attempt")) == 1 and p.get("rusher_player_id") == player_id
        if target or carry:
            counted_targets += int(target)
            counted_carries += int(carry)
            yardline = number(p.get("yardline_100"))
            if yardline is None or not 0 <= yardline <= 100:
                return None
            opportunities.append(int(yardline <= 20))
    if counted_targets != targets or counted_carries != carries:
        return None
    return sum(opportunities)


def unique_index(rows, fields):
    result = {}
    for r in rows:
        key = tuple(r.get(f) for f in fields)
        if any(v is None or v == "" for v in key):
            raise ValueError("missing identity: " + str(fields))
        if key in result:
            raise ValueError("duplicate source key: " + str(fields))
        result[key] = r
    return result


def normalize(data, season, today):
    def regular(name):
        return [r for r in data.get(name, []) if number(r.get("season")) == season
                and r.get("season_type", r.get("game_type")) == "REG"]

    schedule = regular("schedule")
    unique_index(schedule, ("game_id",))
    # Schedules have no explicit final flag. Only dated, scored games from previous
    # calendar days are eligible; live same-day games are deliberately excluded.
    games = [{"game_id": r["game_id"], "week": int(r["week"]), "date": r["gameday"],
              "teams": [r["away_team"], r["home_team"]]} for r in schedule
             if r["gameday"] < today and number(r.get("home_score")) is not None
             and number(r.get("away_score")) is not None]
    games_by_id = {g["game_id"]: g for g in games}
    players = [r for r in regular("players") if r["game_id"] in games_by_id
               and r.get("position") in {"WR", "RB"}]
    unique_index(players, ("player_id", "game_id"))
    team_index = unique_index(regular("teams"), ("game_id", "team"))
    pfr_to_gsis, gsis_to_pfr = defaultdict(set), defaultdict(set)
    for r in data.get("ids", []):
        if r.get("gsis_id") and r.get("pfr_id"):
            pfr_to_gsis[r["pfr_id"]].add(r["gsis_id"])
            gsis_to_pfr[r["gsis_id"]].add(r["pfr_id"])
    snaps = regular("snaps")
    snap_index = unique_index(snaps, ("game_id", "team", "pfr_player_id"))
    snap_groups, pbp_groups = defaultdict(list), defaultdict(list)
    for r in snaps:
        snap_groups[r["game_id"], r["team"]].append(r)
    totals = {k: snap_denominator(v) for k, v in snap_groups.items()}
    for r in data.get("pbp", []):
        pbp_groups[r.get("game_id")].append(r)
    # Include players with snap evidence but no stats row; targets/carries stay null.
    known = {(r["player_id"], r["game_id"]) for r in players}
    for s in snaps:
        ids = pfr_to_gsis[s["pfr_player_id"]]
        if len(ids) != 1 or s["game_id"] not in games_by_id or s.get("position") not in {"WR", "RB"}:
            continue
        pid = next(iter(ids))
        if len(gsis_to_pfr[pid]) == 1 and (pid, s["game_id"]) not in known:
            players.append({"player_id": pid, "game_id": s["game_id"], "team": s["team"],
                            "position": s["position"], "player_display_name": s["player"]})
            known.add((pid, s["game_id"]))
    rows = []
    for p in players:
        if p["position"] not in {"WR", "RB"}:
            continue
        gid, pid, team = p["game_id"], p["player_id"], p["team"]
        if team not in games_by_id[gid]["teams"]:
            raise ValueError("player team contradicts schedule")
        t = team_index.get((gid, team), {})
        pfr = next(iter(gsis_to_pfr[pid])) if len(gsis_to_pfr[pid]) == 1 else None
        snap = snap_index.get((gid, team, pfr), {}) if pfr and len(pfr_to_gsis[pfr]) == 1 else {}
        targets, carries = number(p.get("targets")), number(p.get("carries"))
        rows.append({"player_id": pid, "name": p["player_display_name"],
                     "position": p["position"], "team": team, "game_id": gid,
                     "targets": targets, "carries": carries,
                     "air_yards": number(p.get("receiving_air_yards")),
                     "team_targets": number(t.get("targets")), "team_carries": number(t.get("carries")),
                     "snaps": number(snap.get("offense_snaps")),
                     "team_snaps": totals.get((gid, team)) if snap else None,
                     "red_zone": red_zone(pbp_groups[gid], pid, team, targets, carries)})
    return {"season": season, "games": games, "rows": rows,
            "coverage": {"completed_games": len(games), "scheduled_games": len(schedule),
                         "observed_players": len({r["player_id"] for r in rows}),
                         "player_game_rows": len(rows),
                         "weeks": sorted({g["week"] for g in games}),
                         "null_counts": {k: sum(r[k] is None for r in rows) for k in
                             ("targets", "carries", "air_yards", "snaps", "team_snaps", "red_zone")}}}


def atomic_json(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(mode="w", dir=path.parent, delete=False) as f:
        json.dump(payload, f, allow_nan=False)
        temporary = Path(f.name)
    temporary.replace(path)


def fetch_source(name, url, cfg):
    """Only fresh successful bytes get a receipt; no implicit old-cache fallback."""
    import pyarrow.parquet as pq

    cache = cfg["cache"]
    cache.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=cache) as temporary:
        body, headers = Path(temporary) / "body", Path(temporary) / "headers"
        command = ["curl", "--fail", "--location", "--silent", "--show-error",
                   "--proto", "=https", "--proto-redir", "=https", "--max-time",
                   str(cfg["request_timeout_seconds"]), "--dump-header", str(headers),
                   "--output", str(body), url]
        fetched = subprocess.run(command, capture_output=True, text=True, check=False)
        if fetched.returncode:
            raise ValueError(f"curl {fetched.returncode}: {fetched.stderr.strip()}")
        raw = body.read_bytes()
        if url.endswith(".parquet"):
            frame = pq.read_table(io.BytesIO(raw))
            columns, rows = set(frame.column_names), frame.to_pylist()
        else:
            reader = csv.DictReader(io.StringIO(raw.decode("utf-8-sig")))
            columns, rows = set(reader.fieldnames or []), list(reader)
        if not REQUIRED[name].issubset(columns):
            raise ValueError("Provider schema missing: " + ", ".join(sorted(REQUIRED[name] - columns)))
        digest = hashlib.sha256(raw).hexdigest()
        captured = datetime.now(UTC).isoformat()
        modified = [line.split(":", 1)[1].strip() for line in headers.read_text().splitlines()
                    if line.lower().startswith("last-modified:")]
        receipt = {"name": name, "url": url, "status": "available", "captured_at": captured,
                   "provider_modified_at": modified[-1] if modified else None,
                   "sha256": digest, "source_rows": len(rows)}
        archive = cache / "raw" / digest
        archive.parent.mkdir(exist_ok=True)
        if not archive.exists():
            archive.write_bytes(raw)
        atomic_json(cache / "receipts" / (name + "-" + captured.replace(":", "") + ".json"), receipt)
        return rows, receipt


def refresh(cfg, fetcher=fetch_source):
    from concurrent.futures import ThreadPoolExecutor

    attempted = datetime.now(UTC).isoformat()
    data, receipts = {}, {}
    with ThreadPoolExecutor(max_workers=len(cfg["sources"])) as pool:
        futures = {name: pool.submit(fetcher, name, template.format(season=cfg["season"]), cfg)
                   for name, template in cfg["sources"].items()}
        for name, future in futures.items():
            try:
                data[name], receipts[name] = future.result()
            except (OSError, ValueError) as error:
                data[name] = []
                receipts[name] = {"name": name, "status": "unavailable", "attempted_at": attempted,
                                  "error": str(error), "url": cfg["sources"][name].format(season=cfg["season"])}
    try:
        if any(receipts[n]["status"] != "available" for n in ("players", "schedule")):
            raise ValueError("Required player/schedule source failed; previous snapshot retained")
        snapshot = normalize(data, cfg["season"], attempted[:10])
        snapshot.update(updated_at=datetime.now(UTC).isoformat(), sources=receipts)
        atomic_json(cfg["cache"] / "snapshot.json", snapshot)
        status = {"status": "success", "attempted_at": attempted, "sources": receipts}
    except (ValueError, KeyError) as error:
        status = {"status": "failed", "attempted_at": attempted, "error": str(error), "sources": receipts}
    atomic_json(cfg["cache"] / "refresh.json", status)
    return status


if __name__ == "__main__":
    result = refresh(configuration())
    print(json.dumps(result, indent=2))
    raise SystemExit(0 if result["status"] == "success" else 1)
