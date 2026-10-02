"""Read-only presentation of saved season simulations; never invokes the simulator."""

import hashlib
import json
import math
import tomllib
from datetime import UTC, datetime
from pathlib import Path

from season_simulation import digest, evidence_signature

CODE_ROOT = Path(__file__).resolve().parents[1]
CONFIG = CODE_ROOT / "configs/season_simulation.toml"
TOTALS = {"playoffs": 14, "division": 8, "one_seed": 2, "conference": 2, "super_bowl": 1}


def current_teams(view, cfg):
    teams = {
        team: {"team": team, "name": team, "conference": division.split("_")[0],
               "division": division.replace("_", " "), "wins": 0, "losses": 0, "ties": 0}
        for division, members in cfg["divisions"].items() for team in members
    }
    for game in view["games"]:
        for side in ("home", "away"):
            teams[game[side]]["name"] = game.get(side + "_name") or teams[game[side]]["name"]
        if not game.get("outcomes"):
            if game.get("status") == "STATUS_FINAL":
                raise ValueError("MISSING_FINAL_OUTCOME:" + game["game_id"])
            continue
        final = game["outcomes"][-1]
        if final["status"] != "FINAL" or datetime.fromisoformat(final["observed_at"]) > datetime.fromisoformat(view["updated_at"]):
            raise ValueError("INVALID_CURRENT_OUTCOME:" + game["game_id"])
        hs, aws = final["home_score"], final["away_score"]
        if type(hs) is not int or type(aws) is not int or min(hs, aws) < 0:
            raise ValueError("INVALID_FINAL_SCORE:" + game["game_id"])
        for side, won in (("home", hs > aws), ("away", aws > hs)):
            teams[game[side]]["ties" if hs == aws else "wins" if won else "losses"] += 1
    return list(teams.values())


def validate_probabilities(snapshot, teams):
    """Reject incomplete/corrupt distributions instead of manufacturing percentages."""
    probs = snapshot["team_probabilities"]
    if type(snapshot["samples"]) is not int or snapshot["samples"] < 1 or set(probs) != {t["team"] for t in teams}:
        raise ValueError("INVALID_SIMULATION_DISTRIBUTION")
    for row in probs.values():
        if any(type(row.get(k)) not in (int, float) or not math.isfinite(row[k]) or not 0 <= row[k] <= 1 for k in TOTALS):
            raise ValueError("INVALID_SIMULATION_PROBABILITY")
        if not (row["one_seed"] <= row["division"] <= row["playoffs"] and row["super_bowl"] <= row["conference"] <= row["playoffs"]):
            raise ValueError("INVALID_SIMULATION_EVENT_ORDER")
        if any(not math.isclose(row[k] * snapshot["samples"], round(row[k] * snapshot["samples"]), abs_tol=1e-7) for k in TOTALS):
            raise ValueError("INVALID_SIMULATION_SAMPLE_COUNTS")
    totals = {k: sum(p[k] for p in probs.values()) for k in TOTALS}
    if any(not math.isclose(totals[k], total, abs_tol=1e-8) for k, total in TOTALS.items()):
        raise ValueError("INVALID_SIMULATION_TOTALS")
    for conference in ("AFC", "NFC"):
        members = [t["team"] for t in teams if t["conference"] == conference]
        for metric, total in (("playoffs", 7), ("division", 4), ("one_seed", 1), ("conference", 1)):
            if not math.isclose(sum(probs[t][metric] for t in members), total, abs_tol=1e-8):
                raise ValueError("INVALID_CONFERENCE_TOTALS:" + conference)
    for division in {t["division"] for t in teams}:
        if not math.isclose(sum(probs[t["team"]]["division"] for t in teams if t["division"] == division), 1, abs_tol=1e-8):
            raise ValueError("INVALID_DIVISION_TOTALS:" + division)
    return totals


def saved_picture(view, root, cfg, clock):
    """Use exact evidence index, otherwise show the latest dated artifact as stale."""
    teams = current_teams(view, cfg)
    result = {
        "status": "ABSENT", "fresh": False, "freshness_reasons": [], "snapshot": None,
        "teams": teams, "favorite": [], "totals": None, "assessed_at": clock.isoformat(),
        "view_updated_at": view["updated_at"], "season": view["season"],
        "current_model_id": view["model"]["model_state_sha256"],
        "current_final_games": sum(bool(g.get("outcomes")) for g in view["games"]),
        "refresh_command": "uv run python ops/season_simulation.py --config configs/season_simulation.toml",
        "configured_samples": cfg["samples"], "configured_rating_sd": cfg["rating_sd"],
    }
    signature = evidence_signature(view, cfg)
    index = root / "simulation-index" / (signature + ".json")
    try:
        if index.exists():
            snapshot_id = json.loads(index.read_text())["snapshot_id"]
            if len(snapshot_id) != 64 or any(c not in "0123456789abcdef" for c in snapshot_id):
                raise ValueError("INVALID_SIMULATION_REFERENCE")
            path = root / "simulations" / (snapshot_id + ".json")
            paths = [path]
        else:
            paths = list((root / "simulations").glob("*.json"))
        if not paths:
            result["reason"] = "No saved simulation. Run the standalone refresh after a healthy source cycle."
            return result
        saved = []
        for path in paths:
            snapshot = json.loads(path.read_text())
            if digest(snapshot) != path.stem:
                raise ValueError("SIMULATION_HASH_MISMATCH:" + path.stem)
            saved.append((datetime.fromisoformat(snapshot["cutoff"]), path.stem, snapshot))
        _, snapshot_id, snapshot = max(saved, key=lambda item: (item[0], item[1]))
        reasons = []
        if snapshot.get("evidence_signature") != signature:
            reasons.append("Saved evidence differs from current results, schedule, inputs, configuration or engine.")
        if snapshot["model_state_sha256"] != view["model"]["model_state_sha256"]:
            reasons.append("Model snapshot has changed.")
        if snapshot.get("history_raw_sha256") != view["sources"]["nflverse_history"]["raw_sha256"]:
            reasons.append("Historical source snapshot has changed.")
        if datetime.fromisoformat(snapshot["cutoff"]) > datetime.fromisoformat(view["updated_at"]):
            reasons.append("Saved cutoff is ahead of the current season view.")
        if snapshot.get("elo_source_sha256") != hashlib.sha256((CODE_ROOT / "src/nfl_predictor/ratings/elo.py").read_bytes()).hexdigest():
            reasons.append("Elo implementation has changed.")
        result.update(snapshot={**snapshot, "snapshot_id": snapshot_id}, fresh=not reasons, freshness_reasons=reasons)
        if snapshot["status"] == "BLOCKED":
            result.update(status="BLOCKED", reason=snapshot["blocked_reason"])
            # A partial run must never expose an accumulated or earlier distribution.
            result["snapshot"]["team_probabilities"] = None
        elif snapshot["status"] == "COMPLETE":
            result["totals"] = validate_probabilities(snapshot, teams)
            result["status"] = "STALE" if reasons else "COMPLETE"
            probabilities = snapshot["team_probabilities"]
            best = max(p["super_bowl"] for p in probabilities.values())
            result["favorite"] = [t for t in teams if probabilities[t["team"]]["super_bowl"] == best]
            for team in teams:
                team["probabilities"] = probabilities[team["team"]]
        else:
            raise ValueError("INVALID_SIMULATION_STATUS")
    except (OSError, ValueError, KeyError, TypeError) as error:
        result.update(status="BLOCKED", fresh=False, reason=str(error), snapshot=None, favorite=[], totals=None)
    return result


def picture(config_path=CONFIG, clock=None):
    """Public endpoint adapter: reads only saved artifacts and current view."""
    clock = clock or datetime.now(UTC)
    try:
        cfg = tomllib.loads(Path(config_path).read_text())
        root = Path(cfg["data_root"]).expanduser().resolve()
        if root.is_relative_to(CODE_ROOT):
            raise ValueError("SIMULATION_DATA_MUST_BE_PRIVATE_OUTSIDE_CODE_ROOT")
        view = json.loads((root / "view.json").read_text())
        result = saved_picture(view, root, cfg, clock)
        live_cfg = tomllib.loads((CODE_ROOT / "configs/season_live.toml").read_text())
        age = (clock - datetime.fromisoformat(view["last_successful_source_check"])).total_seconds()
        result["source_status"] = "FRESH" if 0 <= age <= live_cfg["maximum_capture_age_seconds"] else "STALE"
        if result["source_status"] == "STALE":
            result["freshness_reasons"].append("Current season source check is stale; refresh it before simulating again.")
            result["fresh"] = False
            if result["status"] == "COMPLETE":
                result["status"] = "STALE"
        return result
    except (OSError, ValueError, KeyError, TypeError) as error:
        return {"status": "BLOCKED", "fresh": False, "reason": str(error), "teams": [],
                "snapshot": None, "favorite": [], "freshness_reasons": [], "assessed_at": clock.isoformat()}
