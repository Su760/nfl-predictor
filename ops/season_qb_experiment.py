"""Offline QB correction experiment: immutable inputs, real freeze, matched evaluation.

Use preserved content-addressed game/baseline/report inputs. No source fetch, backdated
freeze, parameter search, or promotion is performed. Identical verified inputs reuse their immutable completed run.
"""

import argparse
import hashlib
import json
import sys
import tomllib
import types
from datetime import UTC, datetime
from importlib.metadata import version
from pathlib import Path

import polars as pl

repo = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(repo / "ops"))
import season_probability as probability
import week1_live


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def now():
    return datetime.now(UTC)


def _reuse(index_path, identity):
    if not index_path.exists():
        return None
    index = json.loads(index_path.read_bytes())
    if index.get("identity") != identity:
        raise ValueError("QB_RUN_INDEX_IDENTITY_MISMATCH")
    for item in index["files"]:
        if sha(Path(item["path"]).read_bytes()) != item["sha256"]:
            raise ValueError("QB_RUN_OUTPUT_HASH_MISMATCH")
    raw = Path(index["result_path"]).read_bytes()
    if sha(raw) != index["result_sha256"]:
        raise ValueError("QB_RUN_RESULT_HASH_MISMATCH")
    return json.loads(raw)


def run_experiment(
    *,
    output_root,
    probability_freeze,
    probability_report,
    baseline_rows,
    player_stats,
    player_stats_sha256,
    qb_config,
):
    private = Path(output_root)

    def archive(namespace, payload, suffix="json"):
        raw = payload if isinstance(payload, bytes) else week1_live.canonical(payload)
        path = private / namespace / (sha(raw) + "." + suffix)
        week1_live.write_once(path, raw)
        return path

    def rows_archive(namespace, rows):
        return archive(namespace, b"".join(week1_live.canonical(r) + b"\n" for r in rows), "jsonl")

    cfg_raw = Path(qb_config).read_bytes()
    cfg = tomllib.loads(cfg_raw.decode())
    if cfg["tie_fit_policy"] != "fixed_mass_decisive_conditional_likelihood":
        raise ValueError("CORRECTED_TIE_FIT_POLICY_REQUIRED")
    if cfg["zero_dollar_mode"] is not True or cfg["allow_paid_usage"] is not False:
        raise ValueError("ZERO_DOLLAR_GUARD")

    probability_root = Path(probability_freeze).parent.parent
    prob_freeze_path = Path(probability_freeze)
    prob_freeze_raw = prob_freeze_path.read_bytes()
    if sha(prob_freeze_raw) != prob_freeze_path.stem:
        raise ValueError("PROBABILITY_FREEZE_HASH_MISMATCH")
    prob_freeze = json.loads(prob_freeze_raw)
    historical_raw = Path(prob_freeze["source_path"]).read_bytes()
    if sha(historical_raw) != prob_freeze["source_sha256"]:
        raise ValueError("HISTORICAL_SOURCE_HASH_MISMATCH")
    historical_path = archive("raw", historical_raw, "csv")
    player_raw = Path(player_stats).read_bytes()
    if sha(player_raw) != player_stats_sha256:
        raise ValueError("PLAYER_SOURCE_CHANGED_FROM_DECLARED_EXPERIMENT")
    player_path = archive("raw", player_raw, "parquet")
    baseline_original = Path(baseline_rows)
    baseline_raw = baseline_original.read_bytes()
    if sha(baseline_raw) != baseline_original.stem:
        raise ValueError("BASELINE_ROWS_HASH_MISMATCH")
    baseline_path = archive("baseline-rows", baseline_raw, "jsonl")

    # Compile the captured source, so concurrent runtime-only edits cannot change the fitted code.
    qb_source = (repo / "ops/season_qb.py").read_bytes()
    qb_source_path = archive("source", qb_source, "py")
    qb = types.ModuleType("frozen_corrected_qb")
    qb.__file__ = str(qb_source_path)
    exec(compile(qb_source, str(qb_source_path), "exec"), qb.__dict__)  # noqa: S102 — exact trusted repository snapshot
    bundle = probability._source_bundle()
    bundle["files"]["ops/season_qb.py"] = qb_source.decode()
    bundle["files"]["configs/season_qb.toml"] = cfg_raw.decode()
    bundle["files"]["ops/season_qb_experiment.py"] = Path(__file__).read_text()
    bundle["files"]["ops/season_sources.py"] = (repo / "ops/season_sources.py").read_text()
    bundle["environment"]["polars"] = version("polars")
    bundle_path = archive("source-versions", bundle)
    original_report_path = Path(probability_report)
    original_report_raw = original_report_path.read_bytes()
    if sha(original_report_raw) != original_report_path.stem:
        raise ValueError("ORIGINAL_PROBABILITY_REPORT_HASH_MISMATCH")
    original_report = json.loads(original_report_raw)
    prob_cfg = tomllib.loads(prob_freeze["config"])
    if sha(prob_freeze["config"].encode()) != prob_freeze["config_sha256"]:
        raise ValueError("PROBABILITY_CONFIG_HASH_MISMATCH")
    original_candidates = [
        Path(original_report["candidate_rows_paths"][name])
        for name in ("production_elo", "production_elo_sigmoid", "tuned_elo")
    ]
    for path in original_candidates:
        if sha(path.read_bytes()) != path.stem:
            raise ValueError("ORIGINAL_CANDIDATE_HASH_MISMATCH")
    identity = sha(
        week1_live.canonical(
            {
                "source_bundle": bundle_path.stem,
                "config": sha(cfg_raw),
                "historical": historical_path.stem,
                "baseline": baseline_path.stem,
                "players": player_path.stem,
                "probability_freeze": prob_freeze_path.stem,
                "probability_report": original_report_path.stem,
                "candidate_rows": [path.stem for path in original_candidates],
            }
        )
    )
    index_path = private / "run-index" / (identity + ".json")
    cached = _reuse(index_path, identity)
    if cached is not None:
        print("REUSED", index_path, flush=True)
        return cached
    frozen_at = week1_live.stamp(now())
    freeze = {
        "schema_version": "qb-corrected-experiment-freeze-v2",
        "frozen_at": frozen_at,
        "correction": "Exclude fixed-mass ties from conditional fitting; reject duplicate/nonfinite statistics; preserve all earlier experiments.",
        "runtime_config": cfg,
        "runtime_config_sha256": sha(cfg_raw),
        "production_policy_sha256": prob_freeze["production_policy_sha256"],
        "probability_freeze_sha256": prob_freeze_path.stem,
        "source_bundle_path": str(bundle_path),
        "source_bundle_sha256": bundle_path.stem,
        "fitted_source_path": str(qb_source_path),
        "fitted_source_sha256": sha(qb_source),
        "historical_source_path": str(historical_path),
        "historical_source_sha256": historical_path.stem,
        "baseline_rows_path": str(baseline_path),
        "baseline_rows_sha256": baseline_path.stem,
        "player_stats_path": str(player_path),
        "player_stats_sha256": player_path.stem,
        "splits": {
            "development_through": cfg["development_end_season"],
            "validation": cfg["validation_season"],
            "known_benchmark": cfg["known_benchmark_season"],
            "prospective": cfg["experiment_freeze_season"],
        },
        "historical_qb_identity": "Retrospective actual starter IDs, Grade C; no original expected-starter availability claim.",
        "prior_knowledge": "2024 and 2025 results of the defective tie fit were inspected; this run corrects the objective without selecting new hyperparameters.",
        "production_change": "NONE",
        "promotion_eligible": False,
    }
    freeze_path = archive("freezes", freeze)
    print("FROZEN", freeze_path, flush=True)

    cfg["production_policy_sha256"] = prob_freeze["production_policy_sha256"]
    cfg["qb_state_as_of"] = frozen_at
    prob_cfg = tomllib.loads(prob_freeze["config"])
    if sha(prob_freeze["config"].encode()) != prob_freeze["config_sha256"]:
        raise ValueError("PROBABILITY_CONFIG_HASH_MISMATCH")
    games, exclusions = probability.historical_games(historical_path, prob_cfg)
    if exclusions:
        raise ValueError("UNEXPECTED_HISTORICAL_EXCLUSIONS")
    baselines = [json.loads(line) for line in baseline_raw.splitlines()]
    player_rows = pl.read_parquet(player_path).to_dicts()
    prepared, lineage = qb.prepare_training_rows(baselines, player_rows, games, cfg)
    prepared_path = rows_archive("prepared-rows", prepared)
    lineage.update(
        {
            "freeze_sha256": freeze_path.stem,
            "freeze_path": str(freeze_path),
            "runtime_config_sha256": sha(cfg_raw),
            "source_bundle_sha256": bundle_path.stem,
            "source_bundle_path": str(bundle_path),
            "fitted_source_path": str(qb_source_path),
            "historical_source_sha256": historical_path.stem,
            "historical_source_path": str(historical_path),
            "baseline_rows_sha256": baseline_path.stem,
            "baseline_rows_path": str(baseline_path),
            "player_stats_sha256": player_path.stem,
            "player_stats_path": str(player_path),
            "prepared_rows_sha256": prepared_path.stem,
            "prepared_rows_path": str(prepared_path),
        }
    )
    fitting_counts = {
        "development_observations": sum(
            int(row["season"]) <= int(cfg["development_end_season"]) for row in prepared
        ),
        "coefficient_fitting_decisive_games": sum(
            int(row["season"]) <= int(cfg["development_end_season"]) and row["result_home"] != 0.5
            for row in prepared
        ),
        "excluded_development_ties": sum(
            int(row["season"]) <= int(cfg["development_end_season"]) and row["result_home"] == 0.5
            for row in prepared
        ),
    }
    artifact = qb.fit_qb(prepared, cfg, lineage, sha(qb_source), now)
    artifact_path = qb.write_artifact(private, artifact)
    baseline_index = {r["game_id"]: r for r in baselines if r["horizon"] == cfg["primary_horizon"]}
    predictions = []
    for row in prepared:
        base = baseline_index[row["game_id"]]
        feature = qb._row_feature(row)
        q = qb._sigmoid(qb._baseline_logit(row) + artifact["coefficient"] * feature)
        ph, pa, pt = probability.to_three_way(q, row["baseline_p_tie"])
        predictions.append(
            {
                **base,
                "model_name": "qb_residual_corrected",
                "q_home": q,
                "p_home": ph,
                "p_away": pa,
                "p_tie": pt,
                "qb_feature": feature,
                "qb_coefficient": artifact["coefficient"],
                "qb_artifact_id": artifact["artifact_id"],
                "fit_freeze_sha256": freeze_path.stem,
            }
        )
    prediction_path = rows_archive("candidate-rows", predictions)
    report = {
        "schema_version": "qb-corrected-evaluation-v2",
        "created_at": week1_live.stamp(now()),
        "artifact_id": artifact["artifact_id"],
        "artifact_path": str(artifact_path),
        "artifact_file_sha256": sha(artifact_path.read_bytes()),
        "freeze_sha256": freeze_path.stem,
        "freeze_path": str(freeze_path),
        "source_bundle_path": str(bundle_path),
        "source_bundle_sha256": bundle_path.stem,
        "fitted_source_sha256": sha(qb_source),
        "prepared_rows_path": str(prepared_path),
        "prepared_rows_sha256": prepared_path.stem,
        "prediction_rows_path": str(prediction_path),
        "prediction_rows_sha256": prediction_path.stem,
        "player_stats_path": str(player_path),
        "player_stats_sha256": player_path.stem,
        "metrics": artifact["metrics"],
        "coefficient": artifact["coefficient"],
        "prepared_rows": len(prepared),
        "fitting_sample": fitting_counts,
        "evidence_grade": "C",
        "eligibility": artifact["eligibility"],
        "production_change": "NONE",
        "period_roles": probability._period_roles(),
        "promotion_eligible": False,
        "limitations": [
            "Historical QB IDs are retrospective actual starters, not timestamped expected starters.",
            "2024 and 2025 were previously inspected; neither fits this coefficient.",
            "Coverage requires both starters to meet frozen support thresholds.",
            "This is an objective correction, not a new parameter search or production promotion.",
        ],
    }
    report_path = archive("reports", report)
    print("FIT_COMPLETE", artifact_path, flush=True)

    candidate_specs = {
        name: {
            "path": original_report["candidate_rows_paths"][name],
            "sha256": Path(original_report["candidate_rows_paths"][name]).stem,
            "fit_report_sha256": original_report_path.stem,
        }
        for name in ("production_elo", "production_elo_sigmoid", "tuned_elo")
    }
    candidate_specs["qb_residual_corrected"] = {
        "path": str(prediction_path),
        "sha256": prediction_path.stem,
        "artifact_id": artifact["artifact_id"],
        "artifact_file_sha256": sha(artifact_path.read_bytes()),
        "fit_report_sha256": report_path.stem,
    }
    references = {
        "probability_report_path": str(original_report_path),
        "probability_report_sha256": original_report_path.stem,
        "calibration_artifact_path": original_report["artifact_path"],
        "calibration_artifact_sha256": original_report["artifact_sha256"],
        "qb_report_path": str(report_path),
        "qb_report_sha256": report_path.stem,
        "qb_freeze_sha256": freeze_path.stem,
        "qb_source_bundle_sha256": bundle_path.stem,
        "qb_raw_player_sha256": player_path.stem,
        "historical_source_sha256": historical_path.stem,
        "selection": "Use the previously selected raw tuned Elo; do not switch to tuned sigmoid based on 2025.",
    }
    comparison = probability.compare_saved_candidates(
        candidate_specs, prob_cfg, probability_root, references
    )
    result = {
        "freeze_path": str(freeze_path),
        "source_bundle_path": str(bundle_path),
        "artifact_path": str(artifact_path),
        "artifact_file_sha256": sha(artifact_path.read_bytes()),
        "report_path": str(report_path),
        "report_sha256": report_path.stem,
        "prediction_rows_path": str(prediction_path),
        "prepared_rows_path": str(prepared_path),
        "player_stats_path": str(player_path),
        "player_stats_sha256": player_path.stem,
        "coefficient": artifact["coefficient"],
        "prepared_rows": len(prepared),
        "fitting_sample": fitting_counts,
        "metrics": artifact["metrics"],
        "comparison_path": comparison["report_path"],
        "comparison_sha256": comparison["report_sha256"],
        "comparison_summary": {
            period: {
                "n": section["n"],
                "models": {
                    name: {
                        "metrics": {
                            k: value["metrics"].get(k)
                            for k in (
                                "n",
                                "n_non_ties",
                                "ties",
                                "multinomial_log_loss",
                                "multiclass_brier",
                                "accuracy",
                                "calibration_intercept",
                                "calibration_slope",
                            )
                        },
                        "paired_vs_production": value["paired_vs_production"],
                    }
                    for name, value in section["models"].items()
                },
            }
            for period, section in comparison["periods"].items()
        },
    }
    result_path = archive("results", result)
    outputs = [
        result_path,
        freeze_path,
        bundle_path,
        qb_source_path,
        historical_path,
        baseline_path,
        player_path,
        prepared_path,
        prediction_path,
        artifact_path,
        report_path,
        Path(comparison["report_path"]),
    ]
    week1_live.write_once(
        index_path,
        week1_live.canonical(
            {
                "identity": identity,
                "result_path": str(result_path),
                "result_sha256": result_path.stem,
                "files": [
                    {"path": str(path), "sha256": sha(path.read_bytes())} for path in outputs
                ],
            }
        ),
    )
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in (
        "output-root",
        "probability-freeze",
        "probability-report",
        "baseline-rows",
        "player-stats",
        "player-stats-sha256",
        "qb-config",
    ):
        parser.add_argument("--" + name, required=True)
    print(json.dumps(run_experiment(**vars(parser.parse_args())), indent=2))


if __name__ == "__main__":
    main()
