"""One manual retrospective receiving comparison; never runs points experiments."""
from collections import defaultdict
from datetime import UTC, datetime, timedelta
import json
import math

from fantasy.config import ROOT
from fantasy.receiving import METRICS, digest, fit_priors, forecast_game, kickoff, method_hash, policy, valid
from fantasy.receiving_data import ARTIFACT_ROOT, dataset, source
from fantasy.sources import atomic_json


def samples(data, model):
    players, teams, games, _ = data
    outcomes = {(r["game_id"], r["player_id"]): r for r in players}
    records, game_coverage = [], {"scheduled": len(games), "missing_kickoff": 0, "team_forecast_unavailable": 0}
    for game in sorted(games, key=lambda g: (g["gameday"], g["game_id"])):
        start = kickoff(game)
        if start is None:
            game_coverage["missing_kickoff"] += 1
            continue
        rows, volumes = forecast_game(game, start - timedelta(hours=model["policy"]["cutoff_hours"]),
                                      players, teams, games, model)
        game_coverage["team_forecast_unavailable"] += sum(v["targets"] is None for v in volumes)
        for row in rows:
            if row["position"] not in {"WR", "TE"}:
                continue
            outcome = outcomes.get((game["game_id"], row["player_id"]))
            state = "observed" if outcome and valid(outcome) and outcome["team"] == row["team"] else (
                "no row / DNP-no-stat-unknown" if outcome is None else "invalid or team mismatch")
            records.append({**row, "season": game["season"], "outcome_status": state,
                            "actual": {k: outcome[k] for k in METRICS} if state == "observed" else None})
    return records, game_coverage


def calibrate(records, model):
    cfg = model["policy"]
    groups = defaultdict(list)
    for row in records:
        if row["actual"] is None:
            continue
        for group in ("ALL", row["position"], row["position"] + "/" + row["tier"]):
            for kind, key in (("model", "estimate"), ("rolling", "rolling")):
                for metric in METRICS:
                    groups[kind, group, metric].append(abs(row[key][metric] - row["actual"][metric]))
    output = {"model": {}, "rolling": {}}
    for (kind, group, metric), errors in groups.items():
        if group != "ALL" and len(errors) < cfg["minimum_calibration"]:
            continue
        radius = sorted(errors)[min(len(errors), math.ceil((len(errors) + 1) * cfg["interval_level"])) - 1]
        output[kind].setdefault(group, {})[metric] = {"radius": radius, "n": len(errors), "group": group}
    model["intervals"] = output
    return model


def report(records, games):
    output = {"game_coverage": games, "groups": {}}
    groups = defaultdict(list)
    for row in records:
        for group in ("ALL", row["position"], "tier/" + row["tier"], row["position"] + "/" + row["tier"],
                      str(row["season"]) + "/" + row["position"]):
            groups[group].append(row)
    for group, rows in sorted(groups.items()):
        known = [r for r in rows if r["actual"] is not None]
        entry = {"forecasted": len(rows), "scored": len(known), "outcome_coverage": len(known) / len(rows),
                 "missing_outcome": sum(r["outcome_status"].startswith("no row") for r in rows),
                 "invalid_outcome": sum(r["actual"] is None and not r["outcome_status"].startswith("no row") for r in rows),
                 "confirmed_dnp": None, "models": {}}
        for kind, key, interval in (("model", "estimate", "interval"), ("rolling", "rolling", "rolling_interval")):
            entry["models"][kind] = {}
            for metric in METRICS:
                errors = [r[key][metric] - r["actual"][metric] for r in known]
                ranged = [r for r in known if r[interval][metric] is not None]
                n = len(errors)
                entry["models"][kind][metric] = {"n": n, "mae": sum(abs(e) for e in errors) / n if n else None,
                    "rmse": math.sqrt(sum(e * e for e in errors) / n) if n else None,
                    "bias": sum(errors) / n if n else None, "interval_n": len(ranged),
                    "interval_coverage": sum(r[interval][metric]["low"] <= r["actual"][metric] <= r[interval][metric]["high"]
                                             for r in ranged) / len(ranged) if ranged else None,
                    "interval_width": sum(r[interval][metric]["high"] - r[interval][metric]["low"] for r in ranged) /
                                      len(ranged) if ranged else None}
        output["groups"][group] = entry
    return output


def gates(result, cfg):
    failures = []
    for season in cfg["benchmark_seasons"]:
        for pos in ("WR", "TE"):
            if result["groups"].get(f"{season}/{pos}", {}).get("outcome_coverage", 0) < cfg["minimum_outcome_coverage"]:
                failures.append(f"{season}/{pos}: outcome coverage below minimum")
    for group in ("ALL", "WR", "TE"):
        for metric in METRICS:
            entry = result["groups"][group]["models"]
            candidate, baseline = entry["model"][metric], entry["rolling"][metric]
            ratio = cfg["maximum_rmse_ratio"] if group == "ALL" else cfg["maximum_position_rmse_ratio"]
            if candidate["rmse"] is None or candidate["rmse"] > baseline["rmse"] * ratio:
                failures.append(f"{group}/{metric}: RMSE gate failed")
            if group == "ALL" and candidate["mae"] > baseline["mae"] * cfg["maximum_mae_ratio"]:
                failures.append(f"{group}/{metric}: MAE gate failed")
            coverage = candidate["interval_coverage"]
            if coverage is None or not cfg["interval_coverage_low"] <= coverage <= cfg["interval_coverage_high"]:
                failures.append(f"{group}/{metric}: interval coverage gate failed")
    return failures


def run():
    ARTIFACT_ROOT.mkdir(parents=True, exist_ok=True)
    if (ARTIFACT_ROOT / "benchmark.json").exists() or (ARTIFACT_ROOT / "freeze.json").exists():
        raise ValueError("Receiving comparison already sealed; no repeated benchmark tuning")
    cfg = policy()
    protocol = ROOT / "docs/runbooks/fantasy-receiving.md"
    # Record exact method/config/protocol BEFORE loading or computing benchmark data.
    freeze = {"created_at": datetime.now(UTC).isoformat(), "method_sha256": method_hash(), "policy": cfg,
              "protocol_sha256": digest(protocol.read_text()), "benchmark_kind": "retrospective corrected-data chronology",
              "no_post_benchmark_tuning": True}
    atomic_json(ARTIFACT_ROOT / "freeze.json", freeze)
    schedule = source("schedule", cfg["train_seasons"][0])
    training = [dataset(s, schedule) for s in cfg["train_seasons"]]
    model = fit_priors([r for d in training for r in d[0]], [r for d in training for r in d[1]], cfg)
    calibration = [dataset(s, schedule) for s in cfg["calibration_seasons"]]
    cal_records, cal_games = samples(calibration[0], model)
    model = calibrate(cal_records, model)
    model["method_sha256"] = method_hash()
    model["freeze_sha256"] = digest(freeze)
    model["calibration_forecasted"] = len(cal_records)
    # Seal priors/radii before benchmark. Final gate decision is metadata only.
    atomic_json(ARTIFACT_ROOT / "calibrated-model.json", model)
    benchmark_data = [dataset(s, schedule) for s in cfg["benchmark_seasons"]]
    records, game_counts = [], {}
    for data in benchmark_data:
        row, coverage = samples(data, model)
        records.extend(row)
        game_counts[str(data[2][0]["season"])] = coverage
    result = report(records, game_counts)
    result.update(evaluated_at=datetime.now(UTC).isoformat(), method_sha256=method_hash(),
                  calibrated_model_sha256=digest(model), gate_failures=gates(result, cfg),
                  calibration_game_coverage=cal_games,
                  receipts={str(d[2][0]["season"]): d[3] for d in training + calibration + benchmark_data})
    model["gate_failures"] = result["gate_failures"]
    model["experimental"] = bool(result["gate_failures"])
    atomic_json(ARTIFACT_ROOT / "model.json", model)
    atomic_json(ARTIFACT_ROOT / "benchmark.json", result)
    print(json.dumps({"scored": result["groups"]["ALL"]["scored"], "failures": result["gate_failures"]}))


if __name__ == "__main__":
    run()
