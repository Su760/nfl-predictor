"""Frozen offset-logistic EPA experiment. Research only; no forecast publication."""

from __future__ import annotations

import json
import tomllib
from datetime import UTC, datetime, timedelta
from pathlib import Path

import numpy as np
from season_cutoff import load_rows
from season_evaluation import CODE_ROOT, configuration, paired_delta, score
from season_probability import configuration as probability_configuration
from season_probability import historical_games, replay
from week1_live import canonical, digest, replace_view, stamp, write_once

from nfl_predictor.models.baselines import load_model_policy
from nfl_predictor.models.calibration import CALIBRATOR_REGISTRY
from nfl_predictor.models.tie import to_three_way


def sigmoid(z):
    return np.exp(-np.logaddexp(0, -np.asarray(z)))


def fit_offset(features, offset, labels, cfg):
    """Training-only scaling; penalized offset logistic with a fixed Elo coefficient of 1."""
    x, offset, labels = (np.asarray(v, dtype=float) for v in (features, offset, labels))
    if (
        x.ndim != 2
        or len(x) != len(offset)
        or offset.shape != labels.shape
        or not all(np.isfinite(v).all() for v in (x, offset, labels))
        or set(labels) != {0.0, 1.0}
    ):
        raise ValueError("INVALID_OFFSET_TRAINING")
    mean, scale = x.mean(axis=0), x.std(axis=0)
    scale[scale == 0] = 1
    design = np.column_stack((np.ones(len(x)), (x - mean) / scale))
    beta = np.zeros(design.shape[1])
    penalty = cfg["ridge_penalty"]
    if penalty <= 0 or cfg["tolerance"] <= 0 or cfg["max_iterations"] < 1:
        raise ValueError("INVALID_OPTIMIZER_CONFIG")

    def objective(b):
        z = offset + design @ b
        return np.sum(np.logaddexp(0, z) - labels * z) + penalty * (b @ b) / 2

    for _ in range(cfg["max_iterations"]):
        p = sigmoid(offset + design @ beta)
        gradient = design.T @ (p - labels) + penalty * beta
        if np.max(np.abs(gradient)) < cfg["tolerance"]:
            return {"mean": mean.tolist(), "scale": scale.tolist(), "beta": beta.tolist()}
        hessian = (design.T * (p * (1 - p))) @ design + penalty * np.eye(len(beta))
        step = np.linalg.solve(hessian, gradient)
        if np.max(np.abs(step)) < cfg["tolerance"]:
            return {"mean": mean.tolist(), "scale": scale.tolist(), "beta": beta.tolist()}
        rate = 1.0
        while objective(beta - rate * step) > objective(beta):
            rate /= 2
            if rate < np.finfo(float).eps:
                raise ValueError("OFFSET_LINE_SEARCH_FAILED")
        beta -= rate * step
    raise ValueError(
        f"OFFSET_DID_NOT_CONVERGE gradient={np.max(np.abs(gradient))} step={np.max(np.abs(step))} rate={rate}"
    )


def predict_offset(model, features, offset):
    x = np.asarray(features, dtype=float)
    mean, scale, beta = (np.asarray(model[k]) for k in ("mean", "scale", "beta"))
    if x.ndim != 2 or x.shape[1] != len(mean) or not np.isfinite(x).all():
        raise ValueError("INVALID_OFFSET_FEATURES")
    return sigmoid(np.asarray(offset) + beta[0] + ((x - mean) / scale) @ beta[1:])


def partition(rows, season, start):
    train = [r for r in rows if start <= r["season"] <= season - 2 and r["result"] != "tie"]
    cal = [r for r in rows if r["season"] == season - 1 and r["result"] != "tie"]
    test = [r for r in rows if r["season"] == season]
    if not train or not cal or not test:
        raise ValueError("INCOMPLETE_RESIDUAL_FOLD")
    return train, cal, test


def join_rows(native, baseline, seconds):
    by_id = {r["game_id"]: r for r in baseline}
    if len(by_id) != len(baseline) or len({r.event_id for r in native}) != len(native):
        raise ValueError("DUPLICATE_RESIDUAL_GAME")
    output = []
    for row in native:
        b = by_id.get(row.event_id)
        if b is None:
            raise ValueError("MISSING_ELO_GAME")
        if datetime.fromisoformat(b["cutoff"]) != row.kickoff_at_utc - timedelta(seconds=seconds):
            raise ValueError("RESIDUAL_CUTOFF_MISMATCH")
        if b["result"] != row.result or b["season"] != row.season:
            raise ValueError("RESIDUAL_LABEL_MISMATCH")
        output.append(
            {
                **b,
                "features": list(row.features),
                "offset": float(np.log(b["q_home"] / (1 - b["q_home"]))),
                "feature_snapshot": row.snapshot_id,
            }
        )
    return output


def run():
    cfg = tomllib.loads((CODE_ROOT / "configs/season_residual.toml").read_text())
    if cfg["production_change"] != "NONE" or cfg["zero_dollar_mode"] is not True:
        raise ValueError("RESEARCH_ONLY_REQUIRED")
    base_cfg = configuration()
    root = Path(base_cfg["output_root"]).expanduser()
    parent_path = root / "evaluations" / (cfg["parent_evaluation"] + ".json")
    parent = json.loads(parent_path.read_text())
    manifest = parent["declaration"]["native_manifest"]
    freeze = json.loads(Path(base_cfg["probability_freeze"]).expanduser().read_text())
    source = Path(freeze["source_path"])
    if digest(source.read_bytes()) != freeze["source_sha256"]:
        raise ValueError("SOURCE_HASH_MISMATCH")
    pcfg = probability_configuration()
    policy_path = CODE_ROOT / "configs/model_policy_v1.toml"
    policy = load_model_policy(policy_path)
    games, _ = historical_games(source, {**pcfg, "history_start": 2015})
    baseline = replay(
        [g for g in games if g["season"] >= pcfg["history_start"]],
        tomllib.loads(policy_path.read_text())["elo"],
        {**pcfg, "horizons": {h: base_cfg["horizons"][h] for h in cfg["horizons"]}},
    )
    declaration = {
        "config": cfg,
        "created_at": stamp(datetime.now(UTC)),
        "parent_sha256": digest(parent_path.read_bytes()),
        "native_manifest": manifest,
        "source_sha256": freeze["source_sha256"],
        "code_hashes": {
            name: digest((CODE_ROOT / name).read_bytes())
            for name in (
                "ops/season_residual.py",
                "ops/season_cutoff.py",
                "ops/season_probability.py",
                "ops/season_evaluation.py",
                "configs/season_insights.toml",
                "src/nfl_predictor/ratings/elo.py",
                "src/nfl_predictor/models/baselines.py",
                "src/nfl_predictor/models/tie.py",
                "src/nfl_predictor/models/calibration.py",
                "configs/model_policy_v1.toml",
                "configs/season_probability.toml",
            )
        },
        "prior_knowledge": "2016–2025 previously inspected. 2021–24 reused validation; 2025 known benchmark. No untouched historical holdout.",
        "formula": "logit(q_home) = fixed production Elo logit + intercept + standardized EPA dot beta; ridge penalizes intercept and beta. Sigmoid calibration on preceding season only.",
        "decision": "One fixed four-feature challenger; no search, automatic promotion or live publication.",
    }
    run_id = digest(canonical(declaration))
    write_once(root / "residual/declarations" / (run_id + ".json"), declaration)
    print("Frozen residual experiment", run_id, flush=True)
    predictions, folds, reports = [], [], []
    for horizon in cfg["horizons"]:
        native = [r for r in load_rows(manifest, horizon) if r.season >= cfg["training_start"]]
        rows = join_rows(
            native, [r for r in baseline if r["horizon"] == horizon], base_cfg["horizons"][horizon]
        )
        for season in cfg["evaluation_seasons"]:
            train, cal, test = partition(rows, season, cfg["training_start"])
            predictions.extend({**r, "model": "production_elo"} for r in test)
            for name, use_epa in (("elo_calibrated_control", False), ("elo_epa_residual", True)):

                def features(rs, use_epa=use_epa):
                    return np.asarray([r["features"] for r in rs])[:, : 4 if use_epa else 0]

                model = fit_offset(
                    features(train),
                    [r["offset"] for r in train],
                    [int(r["result"] == "home") for r in train],
                    cfg,
                )
                raw = predict_offset(model, features(cal), [r["offset"] for r in cal])
                calibrator = (
                    CALIBRATOR_REGISTRY["sigmoid"]
                    .from_policy(policy)
                    .fit(raw.tolist(), [int(r["result"] == "home") for r in cal])
                )
                q = calibrator.transform(
                    predict_offset(model, features(test), [r["offset"] for r in test]).tolist()
                )
                folds.append(
                    {
                        "horizon": horizon,
                        "season": season,
                        "model": name,
                        "train_seasons": sorted({r["season"] for r in train}),
                        "calibration_season": season - 1,
                        "train_n": len(train),
                        "calibration_n": len(cal),
                        "parameters": model,
                    }
                )
                for r, value in zip(test, q, strict=True):
                    ph, pa, pt = to_three_way(float(value), r["p_tie"])
                    predictions.append(
                        {
                            **r,
                            "model": name,
                            "model_name": name,
                            "q_home": float(value),
                            "p_home": ph,
                            "p_away": pa,
                            "p_tie": pt,
                        }
                    )
        for season in [*cfg["evaluation_seasons"], "overall"]:
            groups = {
                name: [
                    r
                    for r in predictions
                    if r["horizon"] == horizon
                    and r["model"] == name
                    and (season == "overall" or r["season"] == season)
                ]
                for name in ("production_elo", "elo_calibrated_control", "elo_epa_residual")
            }
            reports.append(
                {
                    "horizon": horizon,
                    "season": season,
                    "models": {
                        name: {
                            **score(rs, pcfg),
                            "vs_elo": paired_delta(rs, groups["production_elo"], cfg),
                            "vs_control": paired_delta(rs, groups["elo_calibrated_control"], cfg),
                        }
                        for name, rs in groups.items()
                    },
                }
            )
    result = {
        "run_id": run_id,
        "declaration": declaration,
        "reports": reports,
        "folds": folds,
        "status": "RECONSTRUCTED_RESEARCH_ONLY",
        "limitations": "Grade C corrected PBP; kickoff +24h final-availability proxy. Both horizons use verified native lineage. No timestamped historical odds; no market inputs. Matching game IDs and cutoffs required for every model comparison; ties in proper scores only. No prospective EPA shadow was published by this experiment.",
    }
    write_once(root / "residual/predictions" / (run_id + ".json"), predictions)
    write_once(root / "residual/evaluations" / (run_id + ".json"), result)
    replace_view(root / "residual.json", result)
    print(json.dumps({"run_id": run_id, "predictions": len(predictions)}), flush=True)
    return result


if __name__ == "__main__":
    run()
