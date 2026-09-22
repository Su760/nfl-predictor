"""Bounded, lineage-audited season-forward research. Never publishes forecasts."""

from __future__ import annotations

import json
import math
import tomllib
from collections import Counter
from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from importlib.metadata import version
from pathlib import Path

import numpy as np
from season_probability import configuration as probability_configuration
from season_probability import historical_games, metrics, replay
from season_rebuild import _matrix, _row_from_json, chronological_fold
from week1_live import canonical, digest, replace_view, stamp, write_once

from nfl_predictor.evaluation.calibration import wilson_interval
from nfl_predictor.models.baselines import load_model_policy
from nfl_predictor.models.calibration import CALIBRATOR_REGISTRY
from nfl_predictor.models.epa_logistic import EpaLogisticModel
from nfl_predictor.models.tie import TieLayer, to_three_way

CODE_ROOT = Path(__file__).resolve().parents[1]
REPORT_DEPENDENCIES = (
    CODE_ROOT / "ops/season_evaluation.py",
    CODE_ROOT / "ops/season_probability.py",
    CODE_ROOT / "configs/season_probability.toml",
    CODE_ROOT / "src/nfl_predictor/evaluation/metrics.py",
    CODE_ROOT / "src/nfl_predictor/evaluation/calibration.py",
    CODE_ROOT / "src/nfl_predictor/models/calibration.py",
)
GAME_CONTEXT_FIELDS = ("game_id", "season", "week", "horizon", "cutoff", "result")


def configuration():
    cfg = tomllib.loads((CODE_ROOT / "configs/season_insights.toml").read_text())
    if cfg["zero_dollar_mode"] is not True or cfg["production_change"] != "NONE":
        raise ValueError("RESEARCH_ONLY_REQUIRED")
    if cfg["historical_result_delay_hours"] < 24 or any(v <= 0 for v in cfg["horizons"].values()):
        raise ValueError("CONSERVATIVE_PREGAME_CUTOFF_REQUIRED")
    return cfg


def eligible_selection(target, ids, games, cutoff):
    """Every selected preceding game's conservative final proxy must precede cutoff."""
    if target not in ids:
        return False, "TARGET_MISSING_FROM_LINEAGE"
    for gid in set(ids) - {target}:
        if gid not in games:
            return False, "HISTORY_GAME_UNVERIFIED"
        if datetime.fromisoformat(games[gid]["available_at"]) >= cutoff:
            return False, "HISTORY_AFTER_CUTOFF"
    return True, None


def score(rows, cfg):
    if len({r["game_id"] for r in rows}) != len(rows):
        raise ValueError("DUPLICATE_EVALUATION_GAME")
    result = metrics(rows, cfg)
    correct = sum(
        (r["q_home"] >= 0.5) == (r["result"] == "home") for r in rows if r["result"] != "tie"
    )
    result["correct"] = correct
    result["accuracy_interval_95"] = (
        list(wilson_interval(correct, result["n_non_ties"])) if result["n_non_ties"] else None
    )
    return result


def paired_delta(rows, reference, cfg):
    by_id = {r["game_id"]: r for r in reference}
    if (
        len(by_id) != len(reference)
        or set(by_id) != {r["game_id"] for r in rows}
        or len(rows) != len(reference)
    ):
        raise ValueError("MATCHED_SAMPLE_REQUIRED")

    def loss(r):
        return -math.log(max(1e-15, r["p_" + r["result"]]))

    # Cluster by season/week: modest uncertainty estimate, not independent-play precision.
    groups = {}
    for r in rows:
        b = by_id[r["game_id"]]
        if r["horizon"] != b["horizon"] or r["cutoff"] != b["cutoff"]:
            raise ValueError("IDENTICAL_CUTOFF_REQUIRED")
        if any(r[field] != b[field] for field in ("season", "week", "result")):
            raise ValueError("IDENTICAL_GAME_CONTEXT_REQUIRED")
        groups.setdefault((r["season"], r["week"]), []).append(loss(r) - loss(b))
    values = list(groups.values())
    rng = np.random.default_rng(cfg["bootstrap_seed"])
    samples = [
        np.mean([x for i in rng.integers(0, len(values), len(values)) for x in values[i]])
        for _ in range(cfg["bootstrap_replicates"])
    ]
    return {
        "delta_log_loss": float(np.mean([x for v in values for x in v])),
        "interval_95": np.quantile(samples, [0.025, 0.975]).tolist(),
        "method": "paired season-week cluster bootstrap",
        "n": len(rows),
    }


def fitted_rows(rows, target_season, indices, policy, cfg, recent=False):
    fold = chronological_fold(target_season, {r.season for r in rows})
    train_seasons = (
        fold.estimator_seasons[-cfg["recent_training_seasons"] :]
        if recent
        else fold.estimator_seasons
    )
    train = [r for r in rows if r.season in train_seasons]
    calibrate = [r for r in rows if r.season == fold.calibration_season and r.result != "tie"]
    test = [r for r in rows if r.season == target_season]
    if not train or not calibrate or not test:
        raise ValueError("INCOMPLETE_CHRONOLOGICAL_FOLD")
    model = EpaLogisticModel(
        tuple(indices), policy.logistic.c, policy.logistic.max_iter, policy.random_seed
    )
    if len(train[0].features) == 4:

        def select(values):
            matrix = _matrix(values)
            if matrix.ndim != 2 or matrix.shape[1] != 4 or not np.isfinite(matrix).all():
                raise ValueError("INVALID_NATIVE_FEATURE_MATRIX")
            if {r.result for r in values} - {"home", "away", "tie"}:
                raise ValueError("INVALID_NATIVE_OUTCOME")
            return matrix[:, list(indices)]

        non_ties = [r for r in train if r.result != "tie"]
        labels = [int(r.result == "home") for r in non_ties]
        if set(labels) != {0, 1}:
            raise ValueError("NATIVE_FIT_REQUIRES_BOTH_CLASSES")
        model.pipeline.fit(select(non_ties), labels)
        calibration_raw = model.pipeline.predict_proba(select(calibrate))[:, 1].tolist()
        test_raw = model.pipeline.predict_proba(select(test))[:, 1].tolist()
    else:
        model.fit(_matrix(train), [r.result for r in train])
        calibration_raw = model.predict_r_home(_matrix(calibrate)).tolist()
        test_raw = model.predict_r_home(_matrix(test)).tolist()
    calibrator = (
        CALIBRATOR_REGISTRY[cfg["calibrator"]]
        .from_policy(policy)
        .fit(
            calibration_raw,
            [int(r.result == "home") for r in calibrate],
        )
    )
    q = calibrator.transform(test_raw)
    tie = TieLayer.fit([r.result for r in rows if r.season < target_season])
    return (
        test,
        q,
        tie.p_tie,
        {
            **asdict(fold),
            "estimator_seasons": list(train_seasons),
            "calibrator": cfg["calibrator"],
            "preprocessing_fit_games": sum(r.result != "tie" for r in train),
            "calibration_games": len(calibrate),
        },
    )


def rows_for_model(rows, horizon, name):
    # Legacy EPA rows include an inherited baseline model_name plus their own model.
    return [
        r for r in rows if r["horizon"] == horizon and r.get("model", r.get("model_name")) == name
    ]


def evaluation_indices(indices, native_manifest):
    if native_manifest is None:
        return indices
    columns = native_manifest["feature_indices"]
    if any(index not in columns for index in indices):
        raise ValueError("FEATURE_NOT_REBUILT")
    return [columns.index(index) for index in indices]


def period_comparison(predictions, evaluation, cfg, pcfg):
    """Score saved predictions by early/full periods without fitting or regenerating models."""
    overall = {
        r["horizon"]: r
        for r in evaluation["reports"]
        if r["season"] == "overall"
    }
    result = {
        "schema_version": "historical-period-comparison-v1",
        "early_weeks": [1, 2],
        "ties_policy": "included in Brier/log loss; excluded from winner accuracy",
        "brier_convention": "three-outcome sum of squared errors, range 0–2",
        "log_loss_convention": "natural logarithm over home/away/tie outcomes",
        "reconstruction_limitations": evaluation["limitations"],
        "horizons": {},
    }
    for horizon, seconds in cfg["horizons"].items():
        horizon_rows = [r for r in predictions if r["horizon"] == horizon]
        model_names = sorted({r.get("model", r.get("model_name")) for r in horizon_rows})
        if "production_elo" not in model_names:
            raise ValueError("PRODUCTION_ELO_REQUIRED")
        by_model = {name: rows_for_model(horizon_rows, horizon, name) for name in model_names}
        baseline_keys = {
            tuple(r[field] for field in GAME_CONTEXT_FIELDS)
            for r in by_model["production_elo"]
        }
        if any(
            {tuple(r[field] for field in GAME_CONTEXT_FIELDS) for r in rows} != baseline_keys
            for rows in by_model.values()
        ):
            raise ValueError("MATCHED_PERIOD_SAMPLE_REQUIRED")

        periods = {}
        for label, include in (
            ("weeks_1_2", lambda r: r["week"] in {1, 2}),
            ("full_season", lambda r: True),
        ):
            reference = [r for r in by_model["production_elo"] if include(r)]
            periods[label] = {}
            for name, rows in by_model.items():
                selected = [r for r in rows if include(r)]
                if {tuple(r[field] for field in GAME_CONTEXT_FIELDS) for r in selected} != {
                    tuple(r[field] for field in GAME_CONTEXT_FIELDS) for r in reference
                }:
                    raise ValueError("MATCHED_PERIOD_SAMPLE_REQUIRED")
                scored = score(selected, pcfg)
                periods[label][name] = {
                    **{
                        key: scored[key]
                        for key in (
                            "n",
                            "n_non_ties",
                            "ties",
                            "correct",
                            "accuracy",
                            "accuracy_interval_95",
                            "multiclass_brier",
                            "multinomial_log_loss",
                        )
                    },
                    "vs_elo": paired_delta(selected, reference, cfg),
                }

        report = overall[horizon]
        legacy = None
        if horizon == "T60":
            prior = report.get("prior_sample_comparison", {}).get("production_elo", {}).get(
                "prior"
            )
            if prior:
                legacy = {
                    **prior,
                    "label": "legacy cutoff-safe cached subset",
                    "source_games": report["available_games"],
                    "excluded_games": report["available_games"] - prior["n"],
                    "prediction_sha256": evaluation["declaration"].get(
                        "reference_prediction_sha256"
                    ),
                }
        result["horizons"][horizon] = {
            "prediction_horizon_seconds": seconds,
            "source_games": report["available_games"],
            "matched_games": report["matched_games"],
            "excluded_games": report["available_games"] - report["matched_games"],
            "legacy_subset": legacy,
            **periods,
        }
    return result


def report_update_identity(run_id, evaluation_path, prediction_path, dependencies=None):
    dependencies = tuple(dependencies or REPORT_DEPENDENCIES)
    return {
        "schema_version": "historical-period-comparison-v1",
        "source_run_id": run_id,
        "source_evaluation_sha256": digest(Path(evaluation_path).read_bytes()),
        "source_prediction_sha256": digest(Path(prediction_path).read_bytes()),
        "dependencies": {str(path.relative_to(CODE_ROOT)): digest(path.read_bytes()) for path in dependencies}
        if all(path.is_relative_to(CODE_ROOT) for path in dependencies)
        else {str(path): digest(path.read_bytes()) for path in dependencies},
        "packages": {name: version(name) for name in ("numpy", "scikit-learn")},
    }


def refresh_saved_report():
    """Add derived fields to the dashboard view; immutable model artifacts remain untouched."""
    cfg = configuration()
    root = Path(cfg["output_root"]).expanduser()
    pointer = json.loads((root / "evaluation.json").read_text())
    run_id = pointer["run_id"]
    evaluation_path = root / "evaluations" / f"{run_id}.json"
    prediction_path = root / "predictions" / f"{run_id}.json"
    evaluation = json.loads(evaluation_path.read_text())
    predictions = json.loads(prediction_path.read_text())
    run_cfg = evaluation["declaration"]["config"]
    comparison = period_comparison(predictions, evaluation, run_cfg, probability_configuration())
    identity = report_update_identity(run_id, evaluation_path, prediction_path)
    update_id = digest(canonical(identity))
    update_path = root / "report-updates" / f"{update_id}.json"
    if update_path.exists():
        update = json.loads(update_path.read_text())
        if any(update.get(key) != value for key, value in identity.items()):
            raise ValueError("REPORT_UPDATE_IDENTITY_MISMATCH")
    else:
        update = {
            **identity,
            "update_id": update_id,
            "created_at": stamp(datetime.now(UTC)),
            "historical_period_comparison": comparison,
        }
        write_once(update_path, update)
    view = {
        **evaluation,
        "historical_period_comparison": update["historical_period_comparison"],
        "report_update": {key: update[key] for key in (*identity, "update_id", "created_at")},
    }
    replace_view(root / "evaluation.json", view)
    return view


def run():
    cfg = configuration()
    research = Path(cfg["research_root"]).expanduser()
    root = Path(cfg["output_root"]).expanduser()
    dataset = research / cfg["dataset"]
    freeze = json.loads(Path(cfg["probability_freeze"]).expanduser().read_text())
    source = Path(freeze["source_path"])
    if digest(source.read_bytes()) != freeze["source_sha256"]:
        raise ValueError("SOURCE_HASH_MISMATCH")
    pcfg = probability_configuration()
    policy_path = CODE_ROOT / "configs/model_policy_v1.toml"
    policy = load_model_policy(policy_path)
    all_games, source_exclusions = historical_games(source, {**pcfg, "history_start": 2015})
    native_manifest = None
    if cfg.get("native", {}).get("enabled"):
        from season_cutoff import build

        native_manifest = build(cfg, all_games)
    reference_run = cfg.get("native", {}).get("reference_evaluation")
    reference_path = root / "predictions" / (str(reference_run) + ".json")
    previous_predictions = json.loads(reference_path.read_text()) if native_manifest else []
    declaration = {
        "config": cfg,
        "dataset_sha256": digest(canonical(native_manifest))
        if native_manifest
        else digest(dataset.read_bytes()),
        "native_manifest": native_manifest,
        "reference_prediction_sha256": digest(reference_path.read_bytes())
        if native_manifest
        else None,
        "source_sha256": freeze["source_sha256"],
        "code_sha256": digest(Path(__file__).read_bytes()),
        "policy_sha256": digest(policy_path.read_bytes()),
        "created_at": stamp(datetime.now(UTC)),
        "prior_knowledge": "2016–2025 already used in model development. No untouched historical holdout. Future forecasts after this freeze are prospective.",
        "production_change": "NONE",
    }
    run_id = digest(canonical(declaration))
    write_once(root / "declarations" / (run_id + ".json"), declaration)
    print("Frozen experiment", run_id, flush=True)
    games = [g for g in all_games if g["season"] >= pcfg["history_start"]]
    by_game = {g["game_id"]: g for g in all_games}
    cached = [_row_from_json(line) for line in dataset.read_text().splitlines()]
    if len({r.event_id for r in cached}) != len(cached):
        raise ValueError("DUPLICATE_CACHED_GAME")
    lineage_root = dataset.parent.parent / "selections"
    lineages = {
        r.event_id: json.loads(
            (lineage_root / f"season={r.season}" / r.event_id / "schedules.json").read_text()
        )
        for r in cached
    }
    baseline = replay(
        games, tomllib.loads(policy_path.read_text())["elo"], {**pcfg, "horizons": cfg["horizons"]}
    )
    baseline_by_key = {(r["horizon"], r["game_id"]): r for r in baseline}
    reports, predictions, exclusions = (
        [],
        [],
        list(native_manifest["exclusions"]) if native_manifest else [],
    )
    parity = {}
    for horizon, seconds in cfg["horizons"].items():
        eligible = []
        for r in cached:
            cutoff = r.kickoff_at_utc - timedelta(seconds=seconds)
            valid, reason = eligible_selection(
                r.event_id, lineages[r.event_id]["eligible_game_ids"], by_game, cutoff
            )
            if valid:
                eligible.append(r)
            elif not native_manifest:
                exclusions.append(
                    {
                        "game_id": r.event_id,
                        "season": r.season,
                        "horizon": horizon,
                        "reason": reason,
                    }
                )
        if native_manifest:
            from season_cutoff import load_rows

            native_rows = load_rows(native_manifest, horizon)
            by_id = {r.event_id: r for r in native_rows}
            differences = [
                abs(r.features[index] - by_id[r.event_id].features[position])
                for r in eligible
                if r.event_id in by_id
                for position, index in enumerate(native_manifest["feature_indices"])
            ]
            maximum = max(differences, default=0.0)
            if maximum > cfg["native"]["parity_absolute_tolerance"]:
                raise ValueError("NATIVE_FEATURE_FORMULA_PARITY_FAILED")
            parity[horizon] = {
                "compared_games": len(differences) // 4,
                "maximum_absolute_difference": maximum,
            }
            eligible = native_rows
        if horizon == "T72" and not native_manifest:
            full = [
                r
                for r in baseline
                if r["horizon"] == horizon and r["season"] in cfg["evaluation_seasons"]
            ]
            for season in [*cfg["evaluation_seasons"], "overall"]:
                selected = (
                    full if season == "overall" else [r for r in full if r["season"] == season]
                )
                reports.append(
                    {
                        "season": season,
                        "horizon": horizon,
                        "available_games": len(selected),
                        "matched_games": len(selected),
                        "models": {
                            "production_elo": {**score(selected, pcfg), "uses_market": False}
                        },
                        "football_status": "UNAVAILABLE: cached cutoff-safe calibration has insufficient games/classes; no comparison or ablation claim.",
                        "cutoff_safe_cached_games": len(
                            [r for r in eligible if season == "overall" or r.season == season]
                        ),
                    }
                )
            predictions.extend(full)
            continue
        combined = {}
        for season in cfg["evaluation_seasons"]:
            test = [r for r in eligible if r.season == season]
            reference = [baseline_by_key[(horizon, r.event_id)] for r in test]
            population = [g for g in games if g["season"] == season and g["game_type"] == "REG"]
            models = {"production_elo": reference}
            folds = {}
            for name, indices in cfg["variants"].items():
                targets, qs, tie, folds[name] = fitted_rows(
                    eligible, season, evaluation_indices(indices, native_manifest), policy, cfg
                )
                models[name] = [
                    {
                        **baseline_by_key[(horizon, r.event_id)],
                        "model": name,
                        "model_name": name,
                        "q_home": float(q),
                        **dict(
                            zip(
                                ("p_home", "p_away", "p_tie"),
                                to_three_way(float(q), tie),
                                strict=True,
                            )
                        ),
                    }
                    for r, q in zip(targets, qs, strict=True)
                ]
            name = "football_epa_recent_training"
            targets, qs, tie, folds[name] = fitted_rows(
                eligible,
                season,
                evaluation_indices(cfg["variants"]["football_epa"], native_manifest),
                policy,
                cfg,
                recent=True,
            )
            models[name] = [
                {
                    **baseline_by_key[(horizon, r.event_id)],
                    "model": name,
                    "model_name": name,
                    "q_home": float(q),
                    **dict(
                        zip(("p_home", "p_away", "p_tie"), to_three_way(float(q), tie), strict=True)
                    ),
                }
                for r, q in zip(targets, qs, strict=True)
            ]
            report = {
                "season": season,
                "horizon": horizon,
                "available_games": len(population),
                "matched_games": len(reference),
                "excluded_games": sorted(
                    {g["game_id"] for g in population} - {r["game_id"] for r in reference}
                ),
                "models": {},
                "folds": folds,
                "full_schedule_elo": score(
                    [r for r in baseline if r["horizon"] == horizon and r["season"] == season], pcfg
                ),
            }
            for name, rows in models.items():
                report["models"][name] = {
                    **score(rows, pcfg),
                    "vs_elo": paired_delta(rows, reference, cfg),
                    "vs_football_epa": paired_delta(rows, models["football_epa"], cfg),
                    "uses_market": False,
                }
                predictions.extend(rows)
                combined.setdefault(name, []).extend(rows)
            reports.append(report)
            print(
                horizon,
                season,
                len(reference),
                {name: round(v["multinomial_log_loss"], 4) for name, v in report["models"].items()},
                flush=True,
            )
        common_comparison = {}
        if native_manifest and horizon == "T60":
            for name, current_rows in combined.items():
                old = rows_for_model(previous_predictions, horizon, name)
                old_ids = {r["game_id"] for r in old}
                same = [r for r in current_rows if r["game_id"] in old_ids]
                common_comparison[name] = {
                    "prior": score(old, pcfg),
                    "current": score(same, pcfg),
                    "paired_change": paired_delta(same, old, cfg),
                }
        reports.append(
            {
                "prior_sample_comparison": common_comparison,
                "season": "overall",
                "horizon": horizon,
                "available_games": sum(
                    r["available_games"] for r in reports if r["horizon"] == horizon
                ),
                "matched_games": len(combined["production_elo"]),
                "full_schedule_elo": score(
                    [
                        r
                        for r in baseline
                        if r["horizon"] == horizon and r["season"] in cfg["evaluation_seasons"]
                    ],
                    pcfg,
                ),
                "models": {
                    name: {
                        **score(rows, pcfg),
                        "vs_elo": paired_delta(rows, combined["production_elo"], cfg),
                        "vs_football_epa": paired_delta(rows, combined["football_epa"], cfg),
                        "uses_market": False,
                    }
                    for name, rows in combined.items()
                },
            }
        )
    result = {
        "run_id": run_id,
        "declaration": declaration,
        "reports": reports,
        "exclusion_counts": dict(Counter(x["reason"] for x in exclusions)),
        "exclusions": exclusions,
        "source_exclusions": source_exclusions,
        "status": "CUTOFF_NATIVE_RECONSTRUCTION_GRADE_C"
        if native_manifest
        else "RECONSTRUCTED_GRADE_C_KNOWN_RESEARCH",
        "feature_parity": parity,
        "coverage_basis": "All source-finalized games with complete preceding PBP; distinct T72/T60 inputs"
        if native_manifest
        else "Cutoff-safe subset of old cached rows",
        "simple_elo_baseline": "Identical to current production Elo formula; reported once, not as two independent models.",
        "market_baseline": {
            "status": "UNAVAILABLE",
            "reason": "No audited historical paired moneylines with pre-cutoff observation timestamps; closing prices are not substituted.",
        },
        "limitations": [
            "Horizon-native features recover previously excluded games. Comparisons to the old run isolate its common sample, but training/calibration coverage also changed."
            if native_manifest
            else "Conditional analysis on cutoff-safe cached rows; coverage is selective, particularly T72. Not a full-schedule accuracy estimate.",
            "Historical final availability is kickoff +24h proxy. Actual publication timestamps and as-issued feature revisions are unavailable.",
            "EPA provider models and subsequent stat corrections may use later information. This is reconstructed research, not strict point-in-time evidence.",
            "Fixed sigmoid calibrator, scaler and coefficients are fitted only on pre-evaluation seasons; all seasons already influenced development.",
            "No target QB, weather, injury, closing odds or final-season target statistics used by these four EPA features.",
            "No production promotion; no validated margin model or projected spread.",
        ],
        "next_experiment": "Freeze a bounded football-only Elo-plus-EPA residual challenger; require multi-season validation and prospective evidence before promotion."
        if native_manifest
        else "Rebuild horizon-native EPA inputs to recover excluded games; freeze and compare on full matched season-forward samples before considering promotion.",
    }
    result["historical_period_comparison"] = period_comparison(
        predictions, result, cfg, pcfg
    )
    write_once(root / "evaluations" / (run_id + ".json"), result)
    write_once(root / "predictions" / (run_id + ".json"), predictions)
    replace_view(root / "evaluation.json", result)
    return result


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--refresh-saved-report",
        action="store_true",
        help="derive report-only fields from immutable saved predictions without refitting",
    )
    arguments = parser.parse_args()
    refresh_saved_report() if arguments.refresh_saved_report else run()
