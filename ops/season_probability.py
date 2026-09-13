"""Frozen chronological Elo research and validated shadow-only calibration."""

from __future__ import annotations

import argparse
import csv
import itertools
import json
import math
import platform
import tomllib
from collections import defaultdict
from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from importlib.metadata import version
from pathlib import Path

import numpy as np
from week1_live import canonical, digest, kickoff, stamp, write_once

from nfl_predictor.evaluation.calibration import (
    calibration_intercept_slope,
    fixed_width_reliability,
)
from nfl_predictor.evaluation.metrics import (
    multiclass_brier,
    multinomial_log_loss,
    straight_up_accuracy,
)
from nfl_predictor.models.calibration import SigmoidCalibrator
from nfl_predictor.models.tie import TieLayer, to_three_way
from nfl_predictor.ratings.base import CompletedGame
from nfl_predictor.ratings.elo import EloRater

CODE_ROOT = Path(__file__).resolve().parents[1]


def configuration(path=None):
    path = Path(path or CODE_ROOT / "configs/season_probability.toml")
    cfg = tomllib.loads(path.read_text())
    if cfg["zero_dollar_mode"] is not True or cfg["allow_paid_usage"] is not False:
        raise ValueError("ZERO_DOLLAR_GUARD")
    if (
        not cfg["history_start"]
        < cfg["development_start"]
        <= cfg["development_end"]
        < cfg["validation_season"]
        < cfg["known_benchmark_season"]
        < cfg["prospective_season"]
    ):
        raise ValueError("CHRONOLOGICAL_SPLITS_REQUIRED")
    if cfg["primary_horizon"] not in cfg["horizons"] or any(
        v <= 0 for v in cfg["horizons"].values()
    ):
        raise ValueError("PREGAME_HORIZONS_REQUIRED")
    if cfg["historical_result_delay_hours"] < 24:
        raise ValueError("CONSERVATIVE_RESULT_DELAY_REQUIRED")
    return cfg


def load_freeze(path, cfg_path=None):
    path = Path(path)
    raw = path.read_bytes()
    if digest(raw) != path.stem:
        raise ValueError("FREEZE_HASH_MISMATCH")
    freeze = json.loads(raw)
    config_path = Path(cfg_path or CODE_ROOT / "configs/season_probability.toml")
    if digest(config_path.read_bytes()) != freeze["config_sha256"]:
        raise ValueError("CONFIG_CHANGED_AFTER_FREEZE")
    cfg = configuration(config_path)
    policy_path = CODE_ROOT / cfg["model_policy"]
    if digest(policy_path.read_bytes()) != freeze["production_policy_sha256"]:
        raise ValueError("PRODUCTION_POLICY_CHANGED_AFTER_FREEZE")
    source = Path(freeze["source_path"])
    if digest(source.read_bytes()) != freeze["source_sha256"]:
        raise ValueError("SOURCE_CHANGED_AFTER_FREEZE")
    return freeze, cfg, tomllib.loads(policy_path.read_text())["elo"]


def historical_games(source_path, cfg):
    games, exclusions, seen = [], [], set()
    with Path(source_path).open(newline="", encoding="utf-8-sig") as handle:
        for row in csv.DictReader(handle):
            season = int(row["season"])
            if not cfg["history_start"] <= season <= cfg["known_benchmark_season"]:
                continue
            gid = row["game_id"]
            if gid in seen:
                raise ValueError("DUPLICATE_HISTORICAL_GAME")
            seen.add(gid)
            if not row["home_score"] or not row["away_score"]:
                exclusions.append({"game_id": gid, "reason": "UNFINALIZED_SOURCE_RESULT"})
                continue
            at = kickoff(row)
            home, away = int(row["home_score"]), int(row["away_score"])
            if min(home, away) < 0 or row["home_team"] == row["away_team"]:
                raise ValueError("INVALID_HISTORICAL_GAME")
            if row["location"] not in {"Home", "Neutral"}:
                raise ValueError("UNKNOWN_HISTORICAL_VENUE")
            games.append(
                {
                    "game_id": gid,
                    "season": season,
                    "week": int(row["week"]),
                    "home": cfg["team_aliases"].get(row["home_team"], row["home_team"]),
                    "away": cfg["team_aliases"].get(row["away_team"], row["away_team"]),
                    "home_score": home,
                    "away_score": away,
                    "kickoff": stamp(at),
                    "neutral_site": row["location"] == "Neutral",
                    "game_type": row["game_type"],
                    "home_qb_id": row.get("home_qb_id"),
                    "away_qb_id": row.get("away_qb_id"),
                    "result": "tie" if home == away else "home" if home > away else "away",
                    "available_at": stamp(
                        at + timedelta(hours=cfg["historical_result_delay_hours"])
                    ),
                }
            )
    return sorted(games, key=lambda g: (g["kickoff"], g["game_id"])), exclusions


def _advance_season(rater, state, season, policy):
    if state[0] is not None and season < state[0]:
        raise ValueError("NONCHRONOLOGICAL_ELO_UPDATE")
    if state[0] is not None and season > state[0]:
        rater.ratings = {
            team: policy["initial"] + (rating - policy["initial"]) * policy["offseason_retention"]
            for team, rating in rater.ratings.items()
        }
    state[0] = season


def replay(games, policy, cfg, model_name="production_elo"):
    """Replay finalized-result proxies strictly before each target horizon."""
    completed = sorted(games, key=lambda g: (g["available_at"], g["game_id"]))
    output = []
    for horizon, seconds in cfg["horizons"].items():
        rater, state, index = EloRater(**policy), [None], 0
        targets = sorted(
            (
                g
                for g in games
                if g["game_type"] == "REG" and g["season"] >= cfg["development_start"]
            ),
            key=lambda g: (g["kickoff"], g["game_id"]),
        )
        tie_by_season = {
            season: TieLayer.fit(
                [g["result"] for g in games if g["season"] < season and g["game_type"] == "REG"]
            ).p_tie
            for season in {g["season"] for g in targets}
        }
        for game in targets:
            cutoff = datetime.fromisoformat(game["kickoff"]) - timedelta(seconds=seconds)
            while (
                index < len(completed)
                and datetime.fromisoformat(completed[index]["available_at"]) < cutoff
            ):
                prior = completed[index]
                _advance_season(rater, state, prior["season"], policy)
                rater.update(
                    CompletedGame(
                        prior["game_id"],
                        prior["season"],
                        prior["home"],
                        prior["away"],
                        prior["home_score"],
                        prior["away_score"],
                        prior["neutral_site"],
                        datetime.fromisoformat(prior["available_at"]),
                    )
                )
                index += 1
            _advance_season(rater, state, game["season"], policy)
            q = rater.home_probability(game["home"], game["away"], game["neutral_site"])
            ph, pa, pt = to_three_way(q, tie_by_season[game["season"]])
            output.append(
                {
                    **game,
                    "horizon": horizon,
                    "cutoff": stamp(cutoff),
                    "model_name": model_name,
                    "q_home": q,
                    "p_home": ph,
                    "p_away": pa,
                    "p_tie": pt,
                    "home_rating": rater.rating(game["home"]),
                    "away_rating": rater.rating(game["away"]),
                    "history_games": index,
                    "last_history_available_at": completed[index - 1]["available_at"]
                    if index
                    else None,
                    "evidence_grade": "C",
                    "availability_basis": "Kickoff plus frozen 24-hour delay proxy; no original final receipt",
                }
            )
    return output


def _period(rows, cfg, period, horizon=None):
    horizon = horizon or cfg["primary_horizon"]
    return [
        r
        for r in rows
        if r["horizon"] == horizon
        and (
            cfg["development_start"] <= r["season"] <= cfg["development_end"]
            if period == "development"
            else r["season"] == cfg["validation_season"]
            if period == "validation"
            else r["season"] == cfg["known_benchmark_season"]
        )
    ]


def metrics(rows, cfg):
    if not rows:
        return {"n": 0, "multinomial_log_loss": None, "multiclass_brier": None, "accuracy": None}
    probs = [(r["p_home"], r["p_away"], r["p_tie"]) for r in rows]
    outcomes = [r["result"] for r in rows]
    non_ties = [r for r in rows if r["result"] != "tie"]
    q = [r["p_home"] / (r["p_home"] + r["p_away"]) for r in non_ties]
    labels = [int(r["result"] == "home") for r in non_ties]
    bins = (
        fixed_width_reliability(
            q,
            labels,
            [r["game_id"] for r in non_ties],
            minimum_count=cfg["reliability_minimum_count"],
        )
        if non_ties
        else []
    )
    slope = (
        calibration_intercept_slope(
            q, labels, cfg["calibration"]["max_iter"], cfg["calibration"]["logit_epsilon"]
        )
        if len(set(labels)) == 2
        else (None, None)
    )
    return {
        "n": len(rows),
        "n_non_ties": len(non_ties),
        "ties": len(rows) - len(non_ties),
        "multinomial_log_loss": multinomial_log_loss(probs, outcomes),
        "multiclass_brier": multiclass_brier(probs, outcomes),
        "accuracy": straight_up_accuracy(probs, outcomes) if non_ties else None,
        "calibration_intercept": slope[0],
        "calibration_slope": slope[1],
        "reliability_bins": [asdict(b) for b in bins],
        "game_ids": [r["game_id"] for r in rows],
    }


def _calibration_fit(rows, cfg):
    rows = [r for r in rows if r["result"] != "tie"]
    if len(rows) < cfg["minimum_fit_non_ties"]:
        raise ValueError("INSUFFICIENT_CALIBRATION_HISTORY")
    settings = cfg["calibration"]
    mapping = SigmoidCalibrator(
        settings["sigmoid_c"],
        settings["max_iter"],
        cfg["bootstrap_seed"],
        settings["logit_epsilon"],
    )
    mapping.fit([r["q_home"] for r in rows], [int(r["result"] == "home") for r in rows])
    return {
        "family": "sigmoid",
        "slope": float(mapping.mapping.coef_[0, 0]),
        "intercept": float(mapping.mapping.intercept_[0]),
        "epsilon": settings["logit_epsilon"],
        "fit_n": len(rows),
        "fit_game_ids_sha256": digest(canonical([r["game_id"] for r in rows])),
    }


def _map_probability(p, calibration):
    if isinstance(p, bool) or not isinstance(p, (int, float)):
        raise TypeError("PROBABILITY_MUST_BE_NUMERIC")
    if not math.isfinite(p) or not 0 <= p <= 1:
        raise ValueError("PROBABILITY_OUT_OF_RANGE")
    if calibration["family"] == "identity":
        return float(p)
    eps = calibration["epsilon"]
    clipped = min(1 - eps, max(eps, p))
    logit = calibration["intercept"] + calibration["slope"] * math.log(clipped / (1 - clipped))
    return 1 / (1 + math.exp(-logit)) if logit >= 0 else math.exp(logit) / (1 + math.exp(logit))


def validate_artifact(artifact):
    if (
        artifact.get("schema_version") != "season-probability-artifact-v1"
        or artifact.get("applies_to") != "production_elo_conditional_home"
    ):
        raise ValueError("CALIBRATION_ARTIFACT_SCOPE_INVALID")
    if artifact.get("artifact_sha256") != digest(
        canonical({k: v for k, v in artifact.items() if k != "artifact_sha256"})
    ):
        raise ValueError("CALIBRATION_ARTIFACT_HASH_MISMATCH")
    if artifact.get("production_change") != "NONE" or artifact.get("role") != "challenger":
        raise ValueError("CALIBRATION_MUST_REMAIN_SHADOW")
    if (
        not artifact["train_through"]
        < artifact["validation_season"]
        < artifact["known_benchmark_season"]
        < artifact["prospective_season"]
    ):
        raise ValueError("CALIBRATION_SPLIT_ORDER_INVALID")
    mapping, bounds = artifact["calibration"], artifact["calibration_bounds"]
    if mapping["family"] not in {"identity", "sigmoid"}:
        raise ValueError("UNSUPPORTED_CALIBRATION")
    for key in ("slope", "intercept", "epsilon"):
        if (
            isinstance(mapping[key], bool)
            or not isinstance(mapping[key], (int, float))
            or not math.isfinite(mapping[key])
        ):
            raise ValueError("INVALID_CALIBRATION_COEFFICIENT")
    if (
        not 0 < mapping["epsilon"] < 0.5
        or not bounds["minimum_slope"] <= mapping["slope"] <= bounds["maximum_slope"]
        or abs(mapping["intercept"]) > bounds["maximum_abs_intercept"]
    ):
        raise ValueError("CALIBRATION_OUTSIDE_FROZEN_BOUNDS")
    if mapping["family"] == "identity" and (mapping["slope"] != 1 or mapping["intercept"] != 0):
        raise ValueError("IDENTITY_COEFFICIENT_MISMATCH")
    return artifact


def calibrated_probability(p, artifact):
    """Validate a frozen production-Elo mapping, return conditional non-tie home probability."""
    validate_artifact(artifact)
    return _map_probability(p, artifact["calibration"])


def _mapped_rows(rows, mapping, name):
    result = []
    for row in rows:
        q = _map_probability(row["q_home"], mapping)
        ph, pa, pt = to_three_way(q, row["p_tie"])
        result.append(
            {**row, "model_name": name, "q_home": q, "p_home": ph, "p_away": pa, "p_tie": pt}
        )
    return result


def paired_uncertainty(baseline, candidate, cfg):
    key = lambda r: (r["game_id"], r["horizon"])
    left, right = {key(r): r for r in baseline}, {key(r): r for r in candidate}
    if len(left) != len(baseline) or len(right) != len(candidate) or set(left) != set(right):
        raise ValueError("MATCHED_GAME_HORIZONS_REQUIRED")
    blocks = defaultdict(lambda: defaultdict(list))
    for identity, before in left.items():
        after = right[identity]
        if before["result"] != after["result"] or before["cutoff"] != after["cutoff"]:
            raise ValueError("MATCHED_OUTCOME_CUTOFF_REQUIRED")
        probs = [(r["p_home"], r["p_away"], r["p_tie"]) for r in (before, after)]
        outcome = before["result"]
        delta = [
            multinomial_log_loss([probs[1]], [outcome])
            - multinomial_log_loss([probs[0]], [outcome]),
            multiclass_brier([probs[1]], [outcome]) - multiclass_brier([probs[0]], [outcome]),
        ]
        blocks[before["season"]][before["week"]].append(delta)
    if not blocks:
        return {"n": 0, "intervals": None}
    rng = np.random.default_rng(cfg["bootstrap_seed"])
    years = sorted(blocks)
    samples = []
    for _ in range(cfg["bootstrap_replicates"]):
        draw = []
        for year in rng.choice(years, size=len(years), replace=True):
            weeks = sorted(blocks[int(year)])
            for week in rng.choice(weeks, size=len(weeks), replace=True):
                draw.extend(blocks[int(year)][int(week)])
        samples.append(np.mean(draw, axis=0))
    tail = (1 - cfg["confidence_level"]) / 2
    ci = np.quantile(np.asarray(samples), [tail, 1 - tail], axis=0)
    observed = np.mean(
        [value for year in blocks.values() for week in year.values() for value in week], axis=0
    )
    return {
        "n": len(left),
        "method": "paired hierarchical season/week block bootstrap",
        "blocks": sum(len(y) for y in blocks.values()),
        "season_blocks": len(years),
        "replicates": cfg["bootstrap_replicates"],
        "confidence_level": cfg["confidence_level"],
        "intervals": {
            name: {"delta": float(observed[i]), "lower": float(ci[0, i]), "upper": float(ci[1, i])}
            for i, name in enumerate(("multinomial_log_loss", "multiclass_brier"))
        },
        "interpretation": "Negative favors candidate; interval describes this dependent sample, not guaranteed future improvement.",
    }


def _object(root, namespace, value, suffix="json"):
    payload = (
        canonical(value) if suffix == "json" else b"".join(canonical(row) + b"\n" for row in value)
    )
    path = root / namespace / (digest(payload) + "." + suffix)
    write_once(path, payload)
    return path


def export_baseline(freeze_path, cfg_path=None):
    freeze, cfg, policy = load_freeze(freeze_path, cfg_path)
    games, exclusions = historical_games(freeze["source_path"], cfg)
    rows = replay(games, policy, cfg)
    root = Path(cfg["research_root"]).expanduser()
    path = _object(root, "baseline-rows", rows, "jsonl")
    return freeze, cfg, policy, games, exclusions, rows, path


def _source_bundle():
    names = (
        "ops/season_probability.py",
        "ops/week1_live.py",
        "src/nfl_predictor/ratings/elo.py",
        "src/nfl_predictor/ratings/base.py",
        "src/nfl_predictor/models/tie.py",
        "src/nfl_predictor/models/calibration.py",
        "src/nfl_predictor/models/baselines.py",
        "src/nfl_predictor/evaluation/metrics.py",
        "src/nfl_predictor/evaluation/calibration.py",
        "configs/model_policy_v1.toml",
        "configs/season_probability.toml",
        "uv.lock",
    )
    return {
        "files": {name: (CODE_ROOT / name).read_text() for name in names},
        "environment": {
            "python": platform.python_version(),
            "numpy": version("numpy"),
            "scikit-learn": version("scikit-learn"),
        },
    }


def _period_roles():
    return {
        "development": "Fitting sample for the Elo parameter search and calibration coefficients; calibrated development scores are not out-of-sample.",
        "validation": "Calibration family selection sample; used to choose identity or sigmoid.",
        "known_benchmark": "Previously inspected 2025 benchmark, not untouched; never selects this experiment's parameters or calibration family.",
    }


def _cached_evaluation(index_path, identity):
    if not index_path.exists():
        return None
    result = json.loads(index_path.read_text())
    if result.get("experiment_identity") != identity:
        raise ValueError("EVALUATION_INDEX_IDENTITY_MISMATCH")
    for key in ("report_path", "artifact_path", "source_bundle_path"):
        path = Path(result[key])
        if digest(path.read_bytes()) != path.stem:
            raise ValueError("EVALUATION_ARCHIVE_HASH_MISMATCH")
    artifact = validate_artifact(json.loads(Path(result["artifact_path"]).read_text()))
    report = json.loads(Path(result["report_path"]).read_text())
    if (
        artifact.get("source_bundle_sha256") != identity["source_bundle_sha256"]
        or artifact.get("freeze_sha256") != identity["freeze_sha256"]
        or report.get("source_bundle_sha256") != identity["source_bundle_sha256"]
        or result.get("artifact_file_sha256") != Path(result["artifact_path"]).stem
    ):
        raise ValueError("EVALUATION_REFERENCE_MISMATCH")
    return result


def evaluate(freeze_path, cfg_path=None):
    freeze, cfg, policy = load_freeze(freeze_path, cfg_path)
    root = Path(cfg["research_root"]).expanduser()
    bundle_path = _object(root, "source-versions", _source_bundle())
    experiment_identity = {
        "freeze_sha256": Path(freeze_path).stem,
        "source_bundle_sha256": bundle_path.stem,
    }
    index_path = root / "evaluation-index" / (digest(canonical(experiment_identity)) + ".json")
    cached = _cached_evaluation(index_path, experiment_identity)
    if cached is not None:
        return cached
    freeze, cfg, policy, games, exclusions, baseline, baseline_path = export_baseline(
        freeze_path, cfg_path
    )
    settings = cfg["calibration"]
    identity = {
        "family": "identity",
        "slope": 1.0,
        "intercept": 0.0,
        "epsilon": settings["logit_epsilon"],
        "fit_n": 0,
    }
    grids = []
    best_rows, best_policy, best_loss = baseline, policy, float("inf")
    for hfa, k, retention in itertools.product(
        cfg["grid"]["home_field_points"],
        cfg["grid"]["k_factor"],
        cfg["grid"]["offseason_retention"],
    ):
        candidate_policy = {
            **policy,
            "home_field_points": hfa,
            "k_factor": k,
            "offseason_retention": retention,
        }
        rows = replay(games, candidate_policy, cfg, "tuned_elo")
        score = metrics(_period(rows, cfg, "development"), cfg)["multinomial_log_loss"]
        grids.append({"policy": candidate_policy, "development_log_loss": score})
        if score < best_loss:
            best_rows, best_policy, best_loss = rows, candidate_policy, score
    candidates, selected = {"production_elo": baseline, "tuned_elo": best_rows}, {}
    for name, rows in list(candidates.items()):
        mapping = _calibration_fit(_period(rows, cfg, "development"), cfg)
        calibrated = _mapped_rows(rows, mapping, name + "_sigmoid")
        candidates[name + "_sigmoid"] = calibrated
        raw_score = metrics(_period(rows, cfg, "validation"), cfg)["multinomial_log_loss"]
        adjusted_score = metrics(_period(calibrated, cfg, "validation"), cfg)[
            "multinomial_log_loss"
        ]
        within_bounds = (
            settings["minimum_slope"] <= mapping["slope"] <= settings["maximum_slope"]
            and abs(mapping["intercept"]) <= settings["maximum_abs_intercept"]
        )
        selected[name] = {
            "calibration": mapping
            if within_bounds and adjusted_score < raw_score - cfg["selection_tolerance"]
            else identity,
            "raw_validation_log_loss": raw_score,
            "sigmoid_validation_log_loss": adjusted_score,
            "selection_basis": "2024 validation only; 2025 never selects family or parameters",
        }
    artifact = {
        "schema_version": "season-probability-artifact-v1",
        "model_version": "elo-calibration-shadow-v1",
        "applies_to": "production_elo_conditional_home",
        "role": "challenger",
        "production_change": "NONE",
        "created_at": stamp(datetime.now(UTC)),
        "frozen_at": freeze["frozen_at"],
        "train_through": cfg["development_end"],
        "validation_season": cfg["validation_season"],
        "known_benchmark_season": cfg["known_benchmark_season"],
        "prospective_season": cfg["prospective_season"],
        "production_policy_sha256": freeze["production_policy_sha256"],
        "source_sha256": freeze["source_sha256"],
        "config_sha256": freeze["config_sha256"],
        "code_sha256": digest(Path(__file__).read_bytes()),
        "source_bundle_sha256": bundle_path.stem,
        "source_bundle_path": str(bundle_path),
        "freeze_sha256": Path(freeze_path).stem,
        "calibration": selected["production_elo"]["calibration"],
        "selection": selected["production_elo"],
        "calibration_bounds": {
            k: settings[k] for k in ("minimum_slope", "maximum_slope", "maximum_abs_intercept")
        },
        "fit_horizon": cfg["primary_horizon"],
        "evidence_grade": "C",
        "promotion_eligible": False,
        "limitations": [
            "Historical outcome availability uses a frozen 24-hour proxy, not original receipts.",
            "2025 is a previously inspected known benchmark.",
            "Only future pregame forecasts generated after the freeze can supply untouched prospective evidence.",
        ],
    }
    artifact["artifact_sha256"] = digest(canonical(artifact))
    validate_artifact(artifact)
    artifact_path = _object(root, "artifacts", artifact)
    candidate_paths = {
        name: str(_object(root, "candidate-rows", rows, "jsonl"))
        for name, rows in candidates.items()
    }
    evaluation = {}
    for period in ("development", "validation", "known_benchmark"):
        evaluation[period] = {}
        for horizon in cfg["horizons"]:
            baseline_period = _period(baseline, cfg, period, horizon)
            evaluation[period][horizon] = {
                name: {
                    "metrics": metrics(_period(rows, cfg, period, horizon), cfg),
                    "paired_vs_production": paired_uncertainty(
                        baseline_period, _period(rows, cfg, period, horizon), cfg
                    ),
                }
                for name, rows in candidates.items()
            }
    report = {
        "schema_version": "season-probability-evaluation-v1",
        "freeze": freeze,
        "code_sha256": digest(Path(__file__).read_bytes()),
        "source_bundle_sha256": bundle_path.stem,
        "source_bundle_path": str(bundle_path),
        "production_policy": policy,
        "fixed_parameters": ["initial", "logistic_scale", "mov_denominator", "mov_rating_scale"],
        "searched_parameters": list(cfg["grid"]),
        "grid": grids,
        "selected_elo_policy": best_policy,
        "calibration_selection": selected,
        "evaluation": evaluation,
        "period_roles": _period_roles(),
        "coverage": {
            "historical_games_including_postseason": len(games),
            "excluded": exclusions,
            "by_season": {
                str(season): sum(g["season"] == season and g["game_type"] == "REG" for g in games)
                for season in sorted({g["season"] for g in games})
            },
        },
        "production_audit": {
            "rating_parameters": "Fixed policy defaults, never fitted in production",
            "tie_layer": "(prior regular-season ties+0.5)/(prior regular-season games+1); fixed during target season",
            "history": "All regular and postseason games update Elo; only regular-season target games are scored",
            "calibration": "Production has none",
        },
        "baseline_rows_path": str(baseline_path),
        "candidate_rows_paths": candidate_paths,
        "artifact_path": str(artifact_path),
        "artifact_sha256": digest(artifact_path.read_bytes()),
        "production_change": "NONE",
        "promotion_eligible": False,
        "evidence_grade": "C",
        "limitations": artifact["limitations"],
    }
    report_path = _object(root, "reports", report)
    result = {
        "report_path": str(report_path),
        "artifact_path": str(artifact_path),
        "artifact_file_sha256": digest(artifact_path.read_bytes()),
        "baseline_rows_path": str(baseline_path),
        "calibration": artifact["calibration"],
        "selected_elo_policy": best_policy,
        "source_bundle_path": str(bundle_path),
        "experiment_identity": experiment_identity,
    }
    write_once(index_path, result)
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("export-baseline", "evaluate"))
    parser.add_argument("--freeze", required=True)
    parser.add_argument("--config", type=Path)
    args = parser.parse_args()
    if args.command == "export-baseline":
        *_, path = export_baseline(args.freeze, args.config)
        result = {"baseline_rows_path": str(path)}
    else:
        result = evaluate(args.freeze, args.config)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
