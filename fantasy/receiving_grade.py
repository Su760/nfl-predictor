"""Grade immutable receiving calls; independently capture outcomes, never forecast."""
import argparse
from collections import Counter, defaultdict
from datetime import UTC, datetime
import hashlib
import json
import math
from pathlib import Path
import re
import tomllib
from zoneinfo import ZoneInfo

from fantasy.config import ROOT, configuration
from fantasy.receiving import METRICS, digest, policy
from fantasy.sources import atomic_json, fetch_source


def settings():
    return tomllib.loads((ROOT / "configs/fantasy_receiving_grade.toml").read_text())


def finite(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def valid_outcome(row):
    return (all(finite(row.get(k)) for k in METRICS) and row["targets"] >= row["receptions"] >= 0
            and all(row[k] == int(row[k]) for k in METRICS[:2]))


def aware(value):
    result = datetime.fromisoformat(value)
    if result.tzinfo is None:
        raise ValueError("Timestamp lacks timezone")
    return result


def write_version(directory, value):
    """Exclusive immutable bytes; repeat runs never replace an earlier capture."""
    raw = json.dumps(value, sort_keys=True, allow_nan=False).encode()
    sha = hashlib.sha256(raw).hexdigest()
    name = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ") + "-" + sha + ".json"
    directory.mkdir(parents=True, exist_ok=True)
    with (directory / name).open("xb") as output:
        output.write(raw)
    return {"file": name, "sha256": sha}


def read_version(directory, name):
    if not isinstance(name, str) or not re.fullmatch(r"\d{8}T\d{12}Z-[a-f0-9]{64}\.json", name):
        raise ValueError("Invalid version filename")
    path = directory / name
    if path.is_symlink():
        raise ValueError("Version symlink refused")
    raw = path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != name.split("-")[1][:-5]:
        raise ValueError("Saved version checksum mismatch")
    return json.loads(raw)


def normalize_outcomes(players, schedule, season, captured):
    aliases = policy()["team_aliases"]
    rows, games = [], []
    for row in players:
        if str(row.get("season")) != str(season) or row.get("season_type") != "REG":
            continue
        item = {k: row.get(k) for k in ("player_id", "game_id", "team", *METRICS)}
        item["team"] = aliases.get(item["team"], item["team"])
        item["invalid_fields"] = [k for k in METRICS if isinstance(item[k], float) and not math.isfinite(item[k])]
        for key in item["invalid_fields"]:
            item[key] = None  # Raw nonfinite bytes remain evidenced by the source hash.
        rows.append(item)
    for row in schedule:
        if str(row.get("season")) != str(season) or row.get("game_type") != "REG":
            continue
        def score(key):
            try:
                value = float(row.get(key))
                return value if math.isfinite(value) and value >= 0 else None
            except (TypeError, ValueError):
                return None
        try:
            date = datetime.fromisoformat(row["gameday"]).date()
            final = (date < captured.astimezone(ZoneInfo("America/New_York")).date()
                     and score("home_score") is not None and score("away_score") is not None)
        except (KeyError, TypeError, ValueError):
            final = False
        games.append({"game_id": row.get("game_id"), "completed": final,
                      "date": row.get("gameday"), "teams": [aliases.get(row.get(k), row.get(k))
                                                            for k in ("away_team", "home_team")]})
    return rows, games


def capture_outcomes(cfg, fetcher=fetch_source):
    """No team input, future-game test, forecast generation or model dependency."""
    root = cfg["cache"] / "receiving" / "grading"
    source_cfg = {**cfg, "cache": root / "inputs"}
    players, player_receipt = fetcher("players", cfg["sources"]["players"].format(season=cfg["season"]), source_cfg)
    schedule, schedule_receipt = fetcher("schedule", cfg["sources"]["schedule"].format(season=cfg["season"]), source_cfg)
    captured = datetime.now(UTC)
    sources = {"players": player_receipt, "schedule": schedule_receipt}
    if any(aware(r["captured_at"]) > captured for r in sources.values()):
        raise ValueError("Outcome receipt is after capture time")
    rows, games = normalize_outcomes(players, schedule, cfg["season"], captured)
    previous_name, previous = None, None
    if (root / "outcome-latest.json").exists():
        pointer = json.loads((root / "outcome-latest.json").read_text())
        previous_name = pointer["file"]
        previous = read_version(root / "outcomes", previous_name)
    changed = previous is not None and any(previous["sources"][k]["sha256"] != sources[k]["sha256"] for k in sources)
    value = {"version": 1, "season": cfg["season"], "captured_at": captured.isoformat(),
             "sources": sources, "rows": rows, "games": games, "previous_capture": previous_name,
             "capture_sequence": previous["capture_sequence"] + 1 if previous else 1,
             "source_revision": previous["source_revision"] + int(changed) if previous else 1,
             "source_bytes_changed": changed}
    receipt = write_version(root / "outcomes", value)
    atomic_json(root / "outcome-latest.json", receipt)
    return value, receipt


def load_archives(cfg, grade_cfg):
    primary = (ROOT / grade_cfg["primary_archive"]).resolve()
    if not primary.is_relative_to(ROOT):
        raise ValueError("Primary archive must be in this worktree")
    raw = primary.read_bytes()
    checksums = dict(line.split("  ", 1)[::-1] for line in (primary.parent / "SHA256SUMS").read_text().splitlines())
    if checksums.get(primary.name) != hashlib.sha256(raw).hexdigest():
        raise ValueError("Preserved primary archive checksum mismatch")
    archive = json.loads(raw)
    if len(archive["rows"]) != grade_cfg["primary_forecasts"] or archive["season"] != cfg["season"]:
        raise ValueError("Primary population/season mismatch")
    found, rejected, identities = [], [], {}
    for path in [primary, *sorted((cfg["cache"] / "receiving" / "forecasts").glob("*.json"))]:
        try:
            if path.is_symlink():
                raise ValueError("Forecast symlink refused")
            data = path.read_bytes()
            item = json.loads(data)
            if item["season"] != archive["season"] or item["week"] != archive["week"]:
                continue
            created, written = aware(item["input_cutoff"]), aware(item["archived_at"])
            if aware(item["generated_at"]) > written or created > written:
                raise ValueError("Archive cutoff/generation after write")
            if any(aware(r["captured_at"]) > created for r in item["sources"].values()):
                raise ValueError("Forecast input captured after cutoff")
            if any(aware(r["kickoff"]) <= written or aware(r["cutoff"]) != created for r in item["rows"]):
                raise ValueError("Forecast not sealed before kickoff / cutoff mismatch")
            keys = [(r["player_id"], r["game_id"]) for r in item["rows"]]
            if any(not isinstance(value, str) or not value for key in keys for value in key):
                raise ValueError("Missing stable player/game identity")
            for row in item["rows"]:
                if any(not isinstance(row.get(k), str) or not row[k] for k in ("name", "team", "position", "tier")):
                    raise ValueError("Missing archived grouping/identity fields")
            if len(keys) != len(set(keys)):
                raise ValueError("Duplicate player-game in forecast")
            identity = digest(item)
            if re.fullmatch(r"\d{8}T\d{12}Z-[a-f0-9]{64}\.json", path.name) and path.name.split("-")[1][:-5] != identity:
                raise ValueError("Forecast filename checksum mismatch")
            location = str(path.relative_to(ROOT)) if path.is_relative_to(ROOT) else path.name
            if identity in identities:
                identities[identity]["locations"].append(location)
                continue
            entry = {"id": identity, "primary": path == primary, "archive": item,
                     "archive_sha256": hashlib.sha256(data).hexdigest(), "locations": [location]}
            identities[identity] = entry
            found.append(entry)
        except (OSError, ValueError, KeyError, TypeError) as error:
            if path == primary:
                raise ValueError("Primary archive invalid: " + str(error)) from error
            rejected.append({"file": path.name, "reason": str(error)})
    return found, rejected


def horizon(hours, edges):
    low = 0
    for high in edges:
        if hours < high:
            return f"{low}–<{high}h"
        low = high
    return f"≥{low}h"


def grade_archive(entry, outcomes, grade_cfg):
    archive = entry["archive"]
    indexed, games = defaultdict(list), defaultdict(list)
    for row in outcomes["rows"]:
        indexed[row.get("player_id"), row.get("game_id")].append(row)
    for game in outcomes["games"]:
        games[game.get("game_id")].append(game)
    records = []
    for row in archive["rows"]:
        actual_rows = indexed[row["player_id"], row["game_id"]]
        game_rows = games[row["game_id"]]
        hours = (aware(row["kickoff"]) - aware(row["cutoff"])).total_seconds() / 3600
        state, reason, actual = "pending", "Game not yet verified complete", None
        if len(game_rows) > 1:
            state, reason = "invalid", "Duplicate schedule game"
        elif game_rows and game_rows[0]["completed"] and aware(row["kickoff"]) < aware(outcomes["captured_at"]):
            if row["team"] not in game_rows[0]["teams"]:
                state, reason = "invalid", "Scheduled team mismatch"
            elif not actual_rows:
                state, reason = "missing", "No outcome row; DNP/no-stat/unknown"
            elif len(actual_rows) > 1:
                state, reason = "invalid", "Duplicate player-game outcomes"
            elif not valid_outcome(actual_rows[0]) or actual_rows[0].get("team") != row["team"]:
                state, reason = "invalid", "Unavailable/invalid components or team mismatch"
            else:
                state, reason = "observed", "Numeric published outcome"
                actual = {k: actual_rows[0][k] for k in METRICS}
        estimates = {key: row[key] if isinstance(row.get(key), dict) else dict.fromkeys(METRICS)
                     for key in ("estimate", "rolling")}
        predictions_valid = all(finite(estimates[key].get(k)) for key in estimates for k in METRICS)
        if not predictions_valid:
            state, reason, actual = "invalid_forecast", "Saved paired estimate unavailable", None
        records.append({"player_id": row["player_id"], "name": row["name"], "game_id": row["game_id"],
                        "position": row["position"], "tier": row["tier"], "horizon": horizon(hours, grade_cfg["horizon_edges_hours"]),
                        "lead_hours": hours, "status": state, "reason": reason, "actual": actual,
                        **estimates,
                        **{key: row[key] if isinstance(row.get(key), dict) else {}
                           for key in ("interval", "rolling_interval")},
                        "published_outcome_rows": actual_rows})
    groups = defaultdict(list)
    for row in records:
        for group in ("ALL", "position/" + row["position"], "tier/" + row["tier"],
                      "horizon/" + row["horizon"], row["position"] + "/" + row["tier"],
                      row["position"] + "/" + row["horizon"]):
            groups[group].append(row)
    summaries = {}
    for name, rows in sorted(groups.items()):
        counts = Counter(r["status"] for r in rows)
        observed = [r for r in rows if r["status"] == "observed"]
        models = {}
        for label, key, intervals in (("model", "estimate", "interval"), ("rolling", "rolling", "rolling_interval")):
            models[label] = {}
            for metric in METRICS:
                errors = [r[key][metric] - r["actual"][metric] for r in observed]
                ranges = [r for r in observed if isinstance(r[intervals].get(metric), dict)
                          and finite(r[intervals][metric].get("low")) and finite(r[intervals][metric].get("high"))
                          and r[intervals][metric]["low"] <= r[intervals][metric]["high"]]
                n = len(errors)
                models[label][metric] = {"n": n, "mae": sum(abs(e) for e in errors) / n if n else None,
                    "rmse": math.sqrt(sum(e * e for e in errors) / n) if n else None,
                    "bias": sum(errors) / n if n else None, "interval_n": len(ranges),
                    "interval_missing": n - len(ranges),
                    "interval_coverage": sum(r[intervals][metric]["low"] <= r["actual"][metric] <= r[intervals][metric]["high"]
                                             for r in ranges) / len(ranges) if ranges else None,
                    "interval_width": sum(r[intervals][metric]["high"] - r[intervals][metric]["low"] for r in ranges) /
                                      len(ranges) if ranges else None}
        summaries[name] = {"forecasted": len(rows), **{k: counts[k] for k in
                            ("observed", "missing", "invalid", "pending", "invalid_forecast")},
                           "complete": len(observed) == len(rows), "models": models}
    return {k: v for k, v in entry.items() if k != "archive"} | {
        "generated_at": archive["generated_at"], "model_sha256": archive["model_sha256"],
        "method_sha256": archive["method_sha256"], "complete": all(r["status"] == "observed" for r in records),
        "groups": summaries, "records": records}


def run(refresh=False, week=None, cfg=None, fetcher=fetch_source):
    cfg = cfg or configuration()
    grade_cfg = settings()
    root = cfg["cache"] / "receiving" / "grading"
    archives, rejected = load_archives(cfg, grade_cfg)
    primary = next(v for v in archives if v["primary"])
    if week is not None and week != primary["archive"]["week"]:
        raise ValueError("Requested week differs from preserved primary week")
    if refresh:
        outcomes, receipt = capture_outcomes(cfg, fetcher)
    else:
        receipt = json.loads((root / "outcome-latest.json").read_text())
        outcomes = read_version(root / "outcomes", receipt["file"])
    versions = [grade_archive(v, outcomes, grade_cfg) for v in archives]
    card = {"version": 1, "season": cfg["season"], "week": primary["archive"]["week"],
            "graded_at": datetime.now(UTC).isoformat(), "outcome_capture": receipt,
            "outcome_captured_at": outcomes["captured_at"], "sources": outcomes["sources"],
            "capture_sequence": outcomes["capture_sequence"], "source_revision": outcomes["source_revision"],
            "source_bytes_changed": outcomes["source_bytes_changed"], "previous_capture": outcomes["previous_capture"],
            "primary_id": primary["id"], "versions": versions, "rejected_versions": rejected,
            "grading_policy": grade_cfg,
            "grader_sha256": digest({"code": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(), "policy": grade_cfg}),
            "complete": next(v["complete"] for v in versions if v["primary"])}
    saved = write_version(root / "scorecards", card)
    atomic_json(root / "latest.json", saved)
    return card, saved


def saved_scorecard(cfg, name=None):
    try:
        root = cfg["cache"] / "receiving" / "grading"
        pointer = json.loads((root / "latest.json").read_text())
        card = read_version(root / "scorecards", name or pointer["file"])
        if card["season"] != cfg["season"]:
            raise ValueError("Scorecard season mismatch")
        read_version(root / "outcomes", card["outcome_capture"]["file"])
        card["captures"] = [{"file": p.name, **{k: v[k] for k in
                               ("outcome_captured_at", "capture_sequence", "source_revision", "complete")}}
                            for p in sorted((root / "scorecards").glob("*.json"), reverse=True)
                            for v in [read_version(root / "scorecards", p.name)]]
        card["state"] = "complete" if card["complete"] else "incomplete"
        age = (datetime.now(UTC) - aware(card["outcome_captured_at"])).total_seconds() / 3600
        card["stale"] = age < 0 or age > cfg["stale_after_hours"]
        return card
    except (OSError, ValueError, KeyError, TypeError) as error:
        return {"state": "unavailable", "versions": [], "error": str(error)}


def latest_personal_outcomes(cfg):
    """Expose current independently captured outcomes without touching local calls."""
    root = cfg["cache"] / "receiving" / "grading"
    if not (root / "outcome-latest.json").exists():
        return None
    try:
        pointer = json.loads((root / "outcome-latest.json").read_text())
        captured = read_version(root / "outcomes", pointer["file"])
        grouped = defaultdict(list)
        for row in captured["rows"]:
            grouped[row.get("player_id"), row.get("game_id")].append(row)
        games = defaultdict(list)
        for game in captured["games"]:
            games[game.get("game_id")].append(game)
        rows = [{**members[0], "status": "observed" if len(members) == 1 and valid_outcome(members[0])
                 and len(games[members[0]["game_id"]]) == 1 and games[members[0]["game_id"]][0]["completed"]
                 and members[0].get("team") in games[members[0]["game_id"]][0]["teams"] else "invalid / unavailable"}
                for members in grouped.values()]
        return {"rows": rows, "source": captured["sources"]["players"], "captured_at": captured["captured_at"]}
    except (OSError, ValueError, KeyError, TypeError) as error:
        return {"rows": [], "state": "unavailable", "error": str(error)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--refresh-outcomes", action="store_true", help="Fetch only players/schedule; never generate forecasts")
    parser.add_argument("--week", type=int, help="Assert preserved primary week")
    args = parser.parse_args()
    card, saved = run(args.refresh_outcomes, args.week)
    primary = next(v for v in card["versions"] if v["primary"])
    print(json.dumps({"scorecard": saved["file"], "complete": card["complete"],
                      "primary_counts": {k: v for k, v in primary["groups"]["ALL"].items() if k != "models"}}))


if __name__ == "__main__":
    main()
