"""Prior-game receiving baseline, separate from frozen retrospective points."""
from collections import defaultdict
from datetime import UTC, datetime
import hashlib
import json
import math
import tomllib
from zoneinfo import ZoneInfo

from fantasy.config import ROOT

METRICS = ("targets", "receptions", "receiving_yards")
ASSUMPTION = ("Assumes last observed team and participation pattern continue; availability is unverified. "
              "Missing games do not establish injury or DNP. No starters, routes or current roles inferred.")


def policy():
    return tomllib.loads((ROOT / "configs/fantasy_receiving.toml").read_text())


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, allow_nan=False).encode()).hexdigest()


def method_hash():
    return digest({n: hashlib.sha256((ROOT / n).read_bytes()).hexdigest() for n in
                   ("fantasy/receiving.py", "fantasy/receiving_data.py", "fantasy/receiving_eval.py",
                    "configs/fantasy_receiving.toml")})


def kickoff(row):
    if not row.get("gametime"):
        return None
    try:
        return datetime.fromisoformat(row["gameday"] + "T" + row["gametime"]).replace(
            tzinfo=ZoneInfo("America/New_York")).astimezone(UTC)
    except (ValueError, KeyError):
        return None


def valid(row):
    values = [row.get(k) for k in METRICS]
    return (all(isinstance(x, (int, float)) and math.isfinite(x) for x in values)
            and values[0] >= values[1] >= 0 and all(x == int(x) for x in values[:2]))


def position(row):
    return row.get("position") if row.get("position") in {"WR", "TE", "RB"} else "OTHER"


def fit_priors(players, teams, cfg):
    """Only designated training seasons enter global priors."""
    teams = [r for r in teams if r["season"] in cfg["train_seasons"]]
    players = [r for r in players if r["season"] in cfg["train_seasons"]]
    index = {(r["game_id"], r["team"]): r for r in teams}
    if not teams:
        raise ValueError("No training team volume")
    result = {"team_targets": sum(r["targets"] for r in teams) / len(teams),
              "team_attempts": sum(r["attempts"] for r in teams) / len(teams),
              "team_games": len(teams), "positions": {}}
    for pos in ("WR", "TE", "RB", "OTHER"):
        rows = [r for r in players if valid(r) and position(r) == pos
                and (pos != "OTHER" or r["targets"] > 0)
                and (r["game_id"], r["team"]) in index]
        tg = sum(r["targets"] for r in rows)
        catches = sum(r["receptions"] for r in rows)
        den = sum(index[r["game_id"], r["team"]]["targets"] for r in rows)
        if not tg or not catches or not den:
            raise ValueError("Insufficient historical position prior: " + pos)
        result["positions"][pos] = {"share": tg / den, "catch_rate": catches / tg,
                                     "yards_per_catch": max(0, sum(r["receiving_yards"] for r in rows) / catches),
                                     "rows": len(rows), "targets": tg, "receptions": catches}
    return {"policy": cfg, "priors": result, "intervals": {}}


def tier(targets, cfg):
    return "low" if targets < cfg["tier_edges"][0] else "medium" if targets < cfg["tier_edges"][1] else "high"


def ranges(values, model, pos, usage, kind="model"):
    cells = model.get("intervals", {}).get(kind, {})
    output = {}
    for metric in METRICS:
        cell = next((cells.get(k, {}).get(metric) for k in (pos + "/" + usage, pos, "ALL")
                     if cells.get(k, {}).get(metric)), None)
        if not cell:
            output[metric] = None
        else:
            lo = values[metric] - cell["radius"]
            output[metric] = {"low": lo if metric == "receiving_yards" else max(0, lo),
                              "high": values[metric] + cell["radius"], "calibration_n": cell["n"],
                              "calibration_group": cell["group"]}
    if output["targets"] and output["receptions"]:
        output["receptions"]["high"] = min(output["receptions"]["high"], output["targets"]["high"])
    return output


def forecast_game(game, cutoff, players, teams, schedule, model):
    """Actual game rows are never read here; all inputs restricted before cutoff."""
    cfg, priors = model["policy"], model["priors"]
    prior = {r["game_id"]: r for r in schedule if r["season"] == game["season"]
             and r["gameday"] < cutoff.astimezone(ZoneInfo("America/New_York")).date().isoformat()
             and r.get("completed") and kickoff(r) and kickoff(r) < cutoff}
    history = [r for r in players if r["game_id"] in prior]
    latest = {}
    for row in sorted(history, key=lambda r: (prior[r["game_id"]]["gameday"], r["game_id"])):
        latest[row["player_id"]] = row
    outputs, volumes = [], []
    for team, opponent in [(game["away_team"], game["home_team"]), (game["home_team"], game["away_team"])]:
        recent = sorted([r for r in prior.values() if team in [r["away_team"], r["home_team"]]],
                        key=lambda r: (r["gameday"], r["game_id"]))[-cfg["lookback_games"]:]
        gids = {r["game_id"] for r in recent}
        ts = [r for r in teams if r["game_id"] in gids and r["team"] == team]
        if not gids or len(ts) != len(gids):
            volumes.append({"team": team, "status": "Prior team volume incomplete", "targets": None,
                            "attempts": None, "unallocated_targets": None})
            continue
        ti = {r["game_id"]: r for r in ts}
        group = [r for r in history if r["game_id"] in gids and r["team"] == team]
        den = sum(r["targets"] for r in ts)
        known = sum(r["targets"] for r in group if valid(r))
        if den <= 0 or any(sum(r["targets"] for r in group if r["game_id"] == gid and valid(r))
                           > ti[gid]["targets"] for gid in gids):
            volumes.append({"team": team, "status": "Prior target coverage incoherent", "targets": None,
                            "attempts": None, "unallocated_targets": None})
            continue
        weight = cfg["team_prior_games"]
        attempts = (sum(r["attempts"] for r in ts) + weight * priors["team_attempts"]) / (len(ts) + weight)
        volume = min(attempts, (den + weight * priors["team_targets"]) / (len(ts) + weight))
        candidates = defaultdict(list)
        for row in group:
            last = latest[row["player_id"]]
            if (valid(row) and last["team"] == team and
                    (last.get("position") in {"WR", "TE", "RB"} or row["targets"] > 0)):
                candidates[row["player_id"]].append(row)
        entries = []
        for pid, rows in sorted(candidates.items()):
            row = latest[pid]
            pos = position(row)
            parent = priors["positions"][pos]
            targets = sum(r["targets"] for r in rows)
            catches = sum(r["receptions"] for r in rows)
            yards = sum(r["receiving_yards"] for r in rows)
            share_den = sum(ti[r["game_id"]]["targets"] for r in rows)
            share = (targets + cfg["share_prior_targets"] * parent["share"]) / (share_den + cfg["share_prior_targets"])
            rate = (catches + cfg["catch_prior_targets"] * parent["catch_rate"]) / (targets + cfg["catch_prior_targets"])
            efficiency = max(0, (yards + cfg["yards_prior_receptions"] * parent["yards_per_catch"]) /
                             (catches + cfg["yards_prior_receptions"]))
            rolling = {k: sum(r[k] for r in rows) / len(rows) for k in METRICS}
            entries.append({"player_id": pid, "name": row["player_display_name"], "position": row.get("position"),
                            "team": team, "opponent": opponent, "game_id": game["game_id"], "week": game["week"],
                            "kickoff": kickoff(game).isoformat() if kickoff(game) else None,
                            "cutoff": cutoff.isoformat(),
                            "lead_hours": (kickoff(game) - cutoff).total_seconds() / 3600 if kickoff(game) else None,
                            "share": share, "catch_rate": rate,
                            "yards_per_catch": efficiency, "rolling": rolling, "tier": tier(rolling["targets"], cfg),
                            "sample": {"observed_games": len(rows), "expected_games": len(gids),
                                       "game_ids": sorted(r["game_id"] for r in rows),
                                       "targets": targets, "receptions": catches, "receiving_yards": yards,
                                       "team_targets": share_den}, "assumption": ASSUMPTION})
        coverage = known / den
        # Also retain targets belonging to past participants outside current candidates.
        candidate_coverage = sum(r["targets"] for rows in candidates.values() for r in rows) / den
        total_share = sum(r["share"] for r in entries)
        scale = min(1, candidate_coverage / total_share) if total_share else 1
        for entry in entries:
            target = volume * entry.pop("share") * scale
            caught = min(target, target * entry["catch_rate"])
            entry["estimate"] = {"targets": target, "receptions": caught,
                                 "receiving_yards": caught * entry["yards_per_catch"]}
            entry["interval"] = ranges(entry["estimate"], model, entry["position"], entry["tier"])
            entry["rolling_interval"] = ranges(entry["rolling"], model, entry["position"], entry["tier"], "rolling")
            outputs.append(entry)
        volumes.append({"team": team, "status": "Available", "targets": volume, "attempts": attempts,
                        "allocated_targets": sum(r["estimate"]["targets"] for r in entries),
                        "unallocated_targets": max(0, volume - sum(r["estimate"]["targets"] for r in entries)),
                        "historical_target_coverage": coverage, "candidate_target_coverage": candidate_coverage,
                        "allocation_positions": sorted({r["position"] for r in entries}), "history_games": len(ts)})
    return outputs, volumes
