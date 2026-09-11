"""Real-clock Week 1 baseline lane; deliberately separate from the V2 champion ledger."""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import os
import tempfile
import time
import tomllib
import urllib.request
from datetime import UTC, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from nfl_predictor.models.tie import TieLayer, to_three_way
from nfl_predictor.ratings.base import CompletedGame
from nfl_predictor.ratings.elo import EloRater

CODE_ROOT = Path(__file__).resolve().parents[1]


def now():
    return datetime.now(UTC)


def stamp(value):
    return value.isoformat().replace("+00:00", "Z")


def digest(data):
    return hashlib.sha256(data).hexdigest()


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def configuration(path=None):
    cfg = tomllib.loads(Path(path or CODE_ROOT / "configs/week1_live.toml").read_text())
    if cfg["zero_dollar_mode"] is not True or cfg["allow_paid_usage"] is not False:
        raise ValueError("ZERO_DOLLAR_GUARD")
    root = Path(os.environ.get("NFL_WEEK1_DATA_DIR", cfg["data_root"])).expanduser()
    if not root.is_absolute() or root.resolve() == CODE_ROOT or CODE_ROOT in root.resolve().parents:
        raise ValueError("PRIVATE_ROOT_REQUIRED")
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    return cfg, root


def write_once(path, value, *, deadline=None):
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = value if isinstance(value, bytes) else canonical(value)
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as f:
        temporary = Path(f.name)
        f.write(payload)
        f.flush()
        os.fsync(f.fileno())
    try:
        if deadline is not None and now() >= deadline:
            return False
        try:
            os.link(temporary, path)
        except FileExistsError:
            if path.read_bytes() != payload:
                raise ValueError("IMMUTABLE_RECORD_CONFLICT") from None
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
        return True
    finally:
        temporary.unlink(missing_ok=True)


def replace_view(path, value):
    temporary = path.with_suffix(".tmp")
    temporary.write_bytes(canonical(value))
    temporary.replace(path)


def kickoff(row):
    return (
        datetime.fromisoformat(row["gameday"] + "T" + row["gametime"])
        .replace(tzinfo=ZoneInfo("America/New_York"))
        .astimezone(UTC)
    )


def capture(cfg, root):
    if not cfg["source_url"].startswith("https://raw.githubusercontent.com/nflverse/"):
        raise ValueError("UNAPPROVED_FREE_SOURCE")
    start = now()
    with urllib.request.urlopen(
        cfg["source_url"], timeout=cfg["request_timeout_seconds"]
    ) as response:
        body = response.read()
        source_modified = response.headers.get("Last-Modified")
    received = now()
    raw_hash = digest(body)
    raw = root / "raw" / (raw_hash + ".csv")
    raw.parent.mkdir(exist_ok=True)
    write_once(raw, body)
    if digest(raw.read_bytes()) != raw_hash:
        raise ValueError("RAW_HASH_MISMATCH")
    receipt = {
        "source_url": cfg["source_url"],
        "request_started_at": stamp(start),
        "captured_at": stamp(received),
        "raw_sha256": raw_hash,
        "source_last_modified": source_modified,
    }
    write_once(root / "captures" / (digest(canonical(receipt)) + ".json"), receipt)
    return list(csv.DictReader(io.StringIO(body.decode("utf-8-sig")))), receipt


def build_model(rows, cfg):
    policy_bytes = (CODE_ROOT / cfg["model_policy"]).read_bytes()
    policy = tomllib.loads(policy_bytes.decode())["elo"]
    rater = EloRater(**policy)
    games = []
    results = []
    source_rows = []
    for row in rows:
        season = int(row["season"])
        if not cfg["history_start"] <= season <= cfg["history_end"]:
            continue
        if not row["home_score"] or not row["away_score"]:
            raise ValueError("INCOMPLETE_HISTORICAL_RESULTS")
        home, away = int(row["home_score"]), int(row["away_score"])
        if home < 0 or away < 0 or row["home_team"] == row["away_team"]:
            raise ValueError("INVALID_HISTORICAL_GAME")
        home_team = cfg["team_aliases"].get(row["home_team"], row["home_team"])
        away_team = cfg["team_aliases"].get(row["away_team"], row["away_team"])
        games.append(
            CompletedGame(
                row["game_id"],
                season,
                home_team,
                away_team,
                home,
                away,
                row["location"] == "Neutral",
                kickoff(row),
            )
        )
        source_rows.append(
            {
                k: row[k]
                for k in (
                    "game_id",
                    "season",
                    "gameday",
                    "gametime",
                    "home_team",
                    "away_team",
                    "home_score",
                    "away_score",
                    "location",
                )
            }
        )
        if row["game_type"] == "REG":
            results.append("tie" if home == away else "home" if home > away else "away")
    if len({g.canonical_event_id for g in games}) != len(games):
        raise ValueError("DUPLICATE_HISTORY")
    if sum(g.season == cfg["history_end"] for g in games) < cfg["minimum_final_games_last_season"]:
        raise ValueError("LATEST_SEASON_HISTORY_MISSING")
    rater.snapshot(games, datetime(cfg["season"], 7, 1, tzinfo=UTC))
    rater.ratings = {
        team: policy["initial"] + (rating - policy["initial"]) * policy["offseason_retention"]
        for team, rating in rater.ratings.items()
    }
    if len(rater.ratings) != 32:
        raise ValueError("MISSING_TEAM_RATINGS")
    proof = {
        "model": cfg["model_label"],
        "policy_sha256": digest(policy_bytes),
        "history_sha256": digest(canonical(source_rows)),
        "history_games": len(games),
        "history_through_season": cfg["history_end"],
        "ratings": rater.ratings,
        "p_tie": TieLayer.fit(results).p_tie,
        "created_at": stamp(now()),
        "limitations": [
            "No current injuries, lineups, weather or odds",
            "Fixed Elo parameters; not a calibrated or promoted V2 champion",
            "Historical reconstruction is not historical pregame evidence",
        ],
        "code_sha256": digest(Path(__file__).read_bytes()),
    }
    return rater, proof


def origin_status(kick, origin, clock, minutes):
    target = kick - timedelta(hours=72) if origin == "T72" else kick - timedelta(minutes=60)
    if clock > target + timedelta(minutes=minutes) or clock >= kick:
        return "MISSED"
    if clock < target - timedelta(minutes=minutes):
        return "SCHEDULED"
    return "DUE"


def generate(cfg, root, *, on_demand=False):
    rows, receipt = capture(cfg, root)
    rater, model = build_model(rows, cfg)
    model_id = digest(canonical(model))
    write_once(root / "models" / (model_id + ".json"), model)
    selected = [
        r
        for r in rows
        if int(r["season"]) == cfg["season"]
        and int(r["week"]) == cfg["week"]
        and r["game_type"] == "REG"
    ]
    if len(selected) != 16 or len({r["game_id"] for r in selected}) != 16:
        raise ValueError("WEEK1_SCHEDULE_INCOMPLETE")
    output = []
    for row in sorted(selected, key=kickoff):
        kick = kickoff(row)
        clock = now()
        if row["location"] not in ("Home", "Neutral"):
            raise ValueError("UNKNOWN_VENUE_CONTEXT")
        statuses = {o: origin_status(kick, o, clock, cfg["window_minutes"]) for o in ("T72", "T60")}
        game_dir = root / "predictions" / row["game_id"]
        for origin in ("ON_DEMAND", "T72", "T60"):
            destination = game_dir / (origin + ".json")
            if destination.exists():
                continue
            if clock >= kick or (origin == "ON_DEMAND" and not on_demand):
                continue
            if origin != "ON_DEMAND" and statuses[origin] != "DUE":
                continue
            if clock - datetime.fromisoformat(receipt["captured_at"]) > timedelta(
                seconds=cfg["maximum_capture_age_seconds"]
            ):
                raise ValueError("CAPTURE_STALE")
            if row["home_score"] or row["away_score"]:
                continue
            if row["home_team"] not in rater.ratings or row["away_team"] not in rater.ratings:
                raise ValueError("UNKNOWN_TEAM")
            ph, pa, pt = to_three_way(
                rater.home_probability(
                    row["home_team"], row["away_team"], row["location"] == "Neutral"
                ),
                model["p_tie"],
            )
            record = {
                "game_id": row["game_id"],
                "home": row["home_team"],
                "away": row["away_team"],
                "kickoff": stamp(kick),
                "origin": origin,
                "mode": "LIVE_BASELINE",
                "predicted_winner": row["home_team"] if ph >= pa else row["away_team"],
                "win_probability": max(ph, pa),
                "p_home": ph,
                "p_away": pa,
                "p_tie": pt,
                "generated_at": stamp(now()),
                "capture": receipt,
                "model_id": model_id,
                "history_through_season": cfg["history_end"],
                "model_label": cfg["model_label"],
                "neutral_site": row["location"] == "Neutral",
            }
            publication = now()
            if publication >= kick or (
                origin != "ON_DEMAND"
                and origin_status(kick, origin, publication, cfg["window_minutes"]) != "DUE"
            ):
                continue
            deadline = kick
            if origin != "ON_DEMAND":
                target = kick - (timedelta(hours=72) if origin == "T72" else timedelta(minutes=60))
                deadline = min(kick, target + timedelta(minutes=cfg["window_minutes"]))
            write_once(destination, record, deadline=deadline)
        records = []
        for origin in ("ON_DEMAND", "T72", "T60"):
            path = game_dir / (origin + ".json")
            if path.exists():
                r = json.loads(path.read_text())
                if (
                    r["kickoff"] != stamp(kick)
                    or r["home"] != row["home_team"]
                    or r["away"] != row["away_team"]
                ):
                    raise ValueError("SCHEDULE_CHANGED_REQUIRES_NEW_VERSION")
                if datetime.fromisoformat(r["generated_at"]) >= kick:
                    raise ValueError("POST_KICKOFF_PREDICTION_REJECTED")
                records.append(r)
                if origin in statuses:
                    statuses[origin] = "COMPLETE"
        latest = max(records, key=lambda r: r["generated_at"]) if records else None
        status = "PREGAME_RECORDED" if latest else "MISSED" if clock >= kick else "BLOCKED"
        output.append(
            {
                "game_id": row["game_id"],
                "home": row["home_team"],
                "away": row["away_team"],
                "kickoff": stamp(kick),
                "status": status,
                "origins": statuses,
                "prediction": latest,
                "predictions": records,
                "reason": None if latest else "NO_VERIFIED_PREGAME_PREDICTION",
                "v2_status": "BLOCKED: reviewed champion registry and full inputs absent",
            }
        )
    view = {
        "updated_at": stamp(now()),
        "games": output,
        "capture": receipt,
        "model": model,
        "zero_dollar": True,
        "auto_origins": "Local worker: T72 / T60, ±10-minute windows",
        "private_cloud_automation": "BLOCKED: no private data repo or verified Actions allowance",
    }
    replace_view(root / "view.json", view)
    return view


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--config")
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--on-demand", action="store_true")
    args = parser.parse_args()
    cfg, root = configuration(args.config)
    while True:
        try:
            if not args.once and (root / "view.json").exists():
                previous = json.loads((root / "view.json").read_text())
                clock = now()
                last_kick = max(datetime.fromisoformat(g["kickoff"]) for g in previous["games"])
                if clock >= last_kick + timedelta(minutes=cfg["window_minutes"]):
                    replace_view(
                        root / "worker.json",
                        {"checked_at": stamp(clock), "status": "FINISHED", "pid": os.getpid()},
                    )
                    break
                due = any(
                    origin_status(
                        datetime.fromisoformat(g["kickoff"]), o, clock, cfg["window_minutes"]
                    )
                    == "DUE"
                    and g["origins"][o] != "COMPLETE"
                    for g in previous["games"]
                    for o in ("T72", "T60")
                )
                age = (
                    clock - datetime.fromisoformat(previous["capture"]["captured_at"])
                ).total_seconds()
                if not due and age < cfg["maximum_capture_age_seconds"]:
                    replace_view(
                        root / "worker.json",
                        {"checked_at": stamp(clock), "status": "HEALTHY", "pid": os.getpid()},
                    )
                    time.sleep(cfg["poll_seconds"])
                    continue
            view = generate(cfg, root, on_demand=args.on_demand)
            print(
                json.dumps(
                    [
                        {"game": g["game_id"], "status": g["status"], "prediction": g["prediction"]}
                        for g in view["games"]
                    ]
                ),
                flush=True,
            )
            replace_view(
                root / "worker.json",
                {"checked_at": stamp(now()), "status": "HEALTHY", "pid": os.getpid()},
            )
        except Exception as error:
            replace_view(
                root / "worker.json",
                {
                    "checked_at": stamp(now()),
                    "status": "BLOCKED",
                    "category": type(error).__name__,
                    "pid": os.getpid(),
                },
            )
            if args.once:
                raise
        if args.once:
            break
        time.sleep(cfg["poll_seconds"])


if __name__ == "__main__":
    main()
