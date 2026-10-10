"""Standalone frozen points experiment; never writes official forecasts or worker state."""

from __future__ import annotations

import argparse
import csv
import fcntl
import io
import json
import subprocess
import tomllib
from collections import Counter
from datetime import UTC, datetime, timedelta
from importlib.metadata import version
from pathlib import Path

import numpy as np
import polars as pl
from season_points import (
    FEATURES,
    features,
    fit_linear,
    home_advantage,
    interval_radius,
    predict_linear,
    spread,
    time,
    valid_score,
)
from week1_live import canonical, digest, kickoff, replace_view, stamp, write_once

from nfl_predictor.evaluation.splits import outer_fold

CODE_ROOT = Path(__file__).resolve().parents[1]
DEPENDENCIES = (
    "ops/season_points.py",
    "ops/season_points_experiment.py",
    "configs/season_points.toml",
    "src/nfl_predictor/ratings/massey.py",
    "src/nfl_predictor/ratings/base.py",
    "src/nfl_predictor/evaluation/splits.py",
    "ops/week1_live.py",
    "docs/reports/2026-10-10-points-experiment-spec.md",
)


def configuration(path=None):
    cfg = tomllib.loads(Path(path or CODE_ROOT / "configs/season_points.toml").read_text())
    root = Path(cfg["private_root"]).expanduser().resolve()
    if root == CODE_ROOT or CODE_ROOT in root.parents:
        raise ValueError("PRIVATE_ROOT_REQUIRED")
    if max(cfg["selection_seasons"]) >= min(cfg["evaluation_seasons"]):
        raise ValueError("DEVELOPMENT_MUST_PRECEDE_EVALUATION")
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    return cfg, root


def source_files(cfg):
    root = Path(cfg["history_root"]).expanduser()
    return [
        root / f"{kind}/season={season}/data.arrow"
        for season in range(cfg["history_start"], cfg["history_end"] + 1)
        for kind in ("schedules", "pbp")
    ]


def hashes(cfg):
    return {
        "code": {name: digest((CODE_ROOT / name).read_bytes()) for name in DEPENDENCIES},
        "inputs": {str(p): digest(p.read_bytes()) for p in source_files(cfg)},
    }


def freeze(cfg, root):
    declaration = {
        "version": cfg["version"],
        "frozen_at": stamp(datetime.now(UTC)),
        "config": cfg,
        "hashes": hashes(cfg),
        "features": FEATURES,
        "libraries": {k: version(k) for k in ("numpy", "polars", "scikit-learn")},
        "prior_knowledge": "2016–2025 previously inspected; benchmark, not untouched.",
        "gate": "At least 2% lower pooled MAE and no higher pooled RMSE, paired games.",
        "prospective": "First valid future-only forecast, next 100 eligible games; no tuning.",
    }
    path = root / "declaration.json"
    if path.exists():
        existing = json.loads(path.read_text())
        if existing["hashes"] != declaration["hashes"] or existing["config"] != cfg:
            raise ValueError("FROZEN_EXPERIMENT_CHANGED_NEW_VERSION_REQUIRED")
        return existing
    write_once(path, declaration)
    return declaration


def verify_freeze(cfg, root):
    declaration = json.loads((root / "declaration.json").read_text())
    if declaration["hashes"] != hashes(cfg) or declaration["config"] != cfg:
        raise ValueError("FROZEN_EXPERIMENT_CHANGED_NEW_VERSION_REQUIRED")
    return declaration


def pace_counts(frame, cfg):
    selected = frame.filter(
        ((pl.col("rush_attempt") == 1) | (pl.col("pass_attempt") == 1) | (pl.col("sack") == 1))
        & pl.col("posteam").is_not_null()
    )
    rows = selected.group_by(["game_id", "posteam"]).len().to_dicts()
    result = {}
    for r in rows:
        team = cfg["team_aliases"].get(r["posteam"], r["posteam"])
        result.setdefault(r["game_id"], {})[team] = r["len"]
    return result


def historical_games(cfg):
    games, exclusions, seen = [], [], set()
    raw = Path(cfg["history_root"]).expanduser()
    columns = ["game_id", "posteam", "rush_attempt", "pass_attempt", "sack"]
    for season in range(cfg["history_start"], cfg["history_end"] + 1):
        pace = pace_counts(
            pl.read_ipc(raw / f"pbp/season={season}/data.arrow", columns=columns), cfg
        )
        frame = pl.read_ipc(raw / f"schedules/season={season}/data.arrow")
        for row in frame.to_dicts():
            if row["game_type"] != "REG":
                continue
            gid = row["game_id"]
            if gid in seen:
                raise ValueError("DUPLICATE_SOURCE_GAME")
            seen.add(gid)
            if not valid_score(row) or row["location"] not in ("Home", "Neutral"):
                exclusions.append(
                    {"game_id": gid, "season": season, "reason": "INVALID_RESULT_OR_VENUE"}
                )
                continue
            if not row["gameday"] or not row["gametime"]:
                exclusions.append({"game_id": gid, "season": season, "reason": "MISSING_KICKOFF"})
                continue
            at = kickoff(row)
            available = stamp(at + timedelta(hours=cfg["historical_delay_hours"]))
            games.append(
                {
                    "game_id": gid,
                    "season": season,
                    "week": int(row["week"]),
                    "game_type": "REG",
                    "home": cfg["team_aliases"].get(row["home_team"], row["home_team"]),
                    "away": cfg["team_aliases"].get(row["away_team"], row["away_team"]),
                    "home_score": row["home_score"],
                    "away_score": row["away_score"],
                    "neutral_site": row["location"] == "Neutral",
                    "kickoff": stamp(at),
                    "available_at": available,
                    "pace": pace.get(gid, {}),
                    "pace_available_at": available,
                }
            )
    return sorted(games, key=lambda g: (g["kickoff"], g["game_id"])), exclusions


def build_rows(games, cfg):
    rows, exclusions, baseline_counts = [], [], Counter()
    hfas = {
        s: home_advantage(games, s, cfg)
        for s in range(cfg["training_start"], cfg["history_end"] + 1)
    }
    for g in games:
        if g["season"] < cfg["training_start"]:
            continue
        cutoff = time(g["kickoff"]) - timedelta(seconds=cfg["cutoff_seconds"])
        f = features(g, games, cutoff, hfas[g["season"]], cfg)
        if f["baseline"] is not None:
            baseline_counts[g["season"]] += 1
        if f["features"] is None:
            exclusions.append(
                {
                    "game_id": g["game_id"],
                    "season": g["season"],
                    "week": g["week"],
                    "reason": f["reason"],
                }
            )
            continue
        rows.append(
            {
                **g,
                **f,
                "cutoff": stamp(cutoff),
                "actual": {
                    "margin": g["home_score"] - g["away_score"],
                    "total": g["home_score"] + g["away_score"],
                },
            }
        )
    return rows, exclusions, dict(baseline_counts), hfas


def partition(rows, season):
    fold = outer_fold(season, {r["season"] for r in rows})
    train = [r for r in rows if r["season"] in fold.estimator_seasons]
    cal = [r for r in rows if r["season"] == fold.calibration_season]
    test = [r for r in rows if r["season"] == fold.test_season]
    if not train or not cal or not test:
        raise ValueError("INCOMPLETE_POINTS_FOLD")
    boundary = min(time(r["cutoff"]) for r in cal)
    if any(time(r["available_at"]) >= boundary for r in train):
        raise ValueError("TRAINING_OUTCOME_AFTER_CALIBRATION_CUTOFF")
    test_boundary = min(time(r["cutoff"]) for r in test)
    if any(time(r["available_at"]) >= test_boundary for r in cal):
        raise ValueError("CALIBRATION_OUTCOME_AFTER_TEST_CUTOFF")
    return train, cal, test


def fit_target(rows, target, alpha):
    return fit_linear(
        [r["features"][target] for r in rows], [r["actual"][target] for r in rows], alpha
    )


def select_alphas(rows, cfg):
    selected, details = {}, {}
    for target in FEATURES:
        losses = []
        for alpha in cfg["alphas"]:
            errors = []
            for season in cfg["selection_seasons"]:
                train, _, test = partition(rows, season)
                model = fit_target(train, target, alpha)
                predictions = predict_linear(model, [r["features"][target] for r in test])
                errors.extend(
                    abs(p - r["actual"][target]) for p, r in zip(predictions, test, strict=True)
                )
            losses.append({"alpha": alpha, "mae": float(np.mean(errors)), "n": len(errors)})
        selected[target] = min(losses, key=lambda r: (r["mae"], r["alpha"]))["alpha"]
        details[target] = losses
    return selected, details


def calibrated_predictions(rows, season, target, alpha, cfg):
    train, cal, test = partition(rows, season)
    model = fit_target(train, target, alpha)
    predicted = predict_linear(model, [r["features"][target] for r in cal])
    radii = {
        "candidate": interval_radius(
            [p - r["actual"][target] for p, r in zip(predicted, cal, strict=True)],
            cfg["interval_level"],
        ),
        "baseline": interval_radius(
            [r["baseline"][target] - r["actual"][target] for r in cal], cfg["interval_level"]
        ),
    }
    predictions = predict_linear(model, [r["features"][target] for r in test])
    output = [
        {
            "game_id": r["game_id"],
            "season": season,
            "week": r["week"],
            "cutoff": r["cutoff"],
            "actual": r["actual"][target],
            "baseline": r["baseline"][target],
            "candidate": float(p),
            "radii": radii,
        }
        for r, p in zip(test, predictions, strict=True)
    ]
    return output, {
        "season": season,
        "training_seasons": sorted({r["season"] for r in train}),
        "calibration_season": season - 1,
        "train_n": len(train),
        "calibration_n": len(cal),
        "calibration_latest_outcome": max(r["available_at"] for r in cal),
        "evaluation_first_cutoff": min(r["cutoff"] for r in test),
        "radii": radii,
    }


def metrics(rows, model):
    if not rows:
        return {"n": 0, "mae": None, "rmse": None, "bias": None, "coverage": None, "width": None}
    errors = np.asarray([r[model] - r["actual"] for r in rows])
    radii = np.asarray([r["radii"][model] for r in rows])
    return {
        "n": len(rows),
        "mae": float(np.mean(abs(errors))),
        "rmse": float(np.sqrt(np.mean(errors**2))),
        "bias": float(np.mean(errors)),
        "coverage": float(np.mean(abs(errors) <= radii)),
        "width": float(np.mean(2 * radii)),
    }


def paired_report(rows, cfg):
    if not rows or len({r["game_id"] for r in rows}) != len(rows):
        raise ValueError("UNIQUE_PAIRED_GAMES_REQUIRED")
    if not all(
        np.isfinite([r["actual"], r["baseline"], r["candidate"], *r["radii"].values()]).all()
        for r in rows
    ):
        raise ValueError("FINITE_PAIRED_GAMES_REQUIRED")
    baseline, candidate = metrics(rows, "baseline"), metrics(rows, "candidate")
    groups = {}
    for r in rows:
        groups.setdefault((r["season"], r["week"]), []).append(r)
    # Cluster sufficient statistics retain every game and both models together.
    blocks = []
    for group in groups.values():
        values = [len(group)]
        for name in ("baseline", "candidate"):
            e = np.asarray([r[name] - r["actual"] for r in group])
            radii = np.asarray([r["radii"][name] for r in group])
            values.extend([sum(abs(e)), sum(e**2), sum(e), sum(abs(e) <= radii), sum(2 * radii)])
        blocks.append(values)
    blocks = np.asarray(blocks, dtype=float)
    rng = np.random.default_rng(cfg["bootstrap_seed"])
    draws = blocks[rng.integers(0, len(blocks), (cfg["bootstrap_replicates"], len(blocks)))].sum(
        axis=1
    )
    summaries = {}
    sampled = {}
    for offset, name in ((1, "baseline"), (6, "candidate")):
        sampled[name] = dict(
            zip(
                ("mae", "rmse", "bias", "coverage", "width"),
                (
                    draws[:, offset] / draws[:, 0],
                    np.sqrt(draws[:, offset + 1] / draws[:, 0]),
                    draws[:, offset + 2] / draws[:, 0],
                    draws[:, offset + 3] / draws[:, 0],
                    draws[:, offset + 4] / draws[:, 0],
                ),
                strict=True,
            )
        )
        summaries[name] = {
            k: np.quantile(v, [0.025, 0.975]).tolist() for k, v in sampled[name].items()
        }
    deltas = {
        k: np.quantile(sampled["candidate"][k] - sampled["baseline"][k], [0.025, 0.975]).tolist()
        for k in sampled["baseline"]
    }
    improvement = 1 - candidate["mae"] / baseline["mae"] if baseline["mae"] else 0
    passed = improvement >= cfg["gate_mae_improvement"] and candidate["rmse"] <= baseline["rmse"]
    return {
        "baseline": baseline,
        "candidate": candidate,
        "mae_improvement": improvement,
        "gate": "PASS" if passed else "FAIL",
        "paired_game_ids": [r["game_id"] for r in rows],
        "uncertainty": {
            "method": "paired season/week cluster bootstrap",
            "blocks": len(blocks),
            "replicates": cfg["bootstrap_replicates"],
            "seed": cfg["bootstrap_seed"],
            "metrics_95": summaries,
            "paired_candidate_minus_baseline_95": deltas,
        },
    }


def save_selection(root, selected, selection, cfg):
    path = root / "selection.json"
    result = {"alphas": selected, "development": selection, "seasons": cfg["selection_seasons"]}
    if path.exists():
        saved = json.loads(path.read_text())
        if any(saved[k] != v for k, v in result.items()):
            raise ValueError("FROZEN_SELECTION_CHANGED")
        return saved
    result["frozen_at"] = stamp(datetime.now(UTC))
    write_once(path, result)
    return result


def evaluate(cfg, root):
    declaration = verify_freeze(cfg, root)
    if (root / "evaluation.json").exists():
        return json.loads((root / "evaluation.json").read_text())
    games, source_exclusions = historical_games(cfg)
    rows, exclusions, baseline_counts, hfas = build_rows(games, cfg)
    selected, selection = select_alphas(rows, cfg)
    selection_record = save_selection(root, selected, selection, cfg)
    print("Alphas frozen before benchmark comparisons:", selected, flush=True)
    report = {
        "version": cfg["version"],
        "declaration_sha256": digest((root / "declaration.json").read_bytes()),
        "comparison_started_at": selection_record["frozen_at"],
        "alphas": selected,
        "development": selection,
        "source_exclusions": source_exclusions,
        "exclusions": exclusions,
        "baseline_eligible_by_season": baseline_counts,
        "hfa_by_season": hfas,
        "targets": {},
    }
    deployment = {
        "version": cfg["version"],
        "declaration_sha256": report["declaration_sha256"],
        "frozen_at": selection_record["frozen_at"],
        "training_seasons": list(range(cfg["training_start"], 2025)),
        "calibration_season": 2025,
        "hfa": home_advantage(games, 2026, cfg),
        "targets": {},
    }
    for target in FEATURES:
        predictions, folds = [], []
        for season in cfg["evaluation_seasons"]:
            out, fold = calibrated_predictions(rows, season, target, selected[target], cfg)
            predictions.extend(out)
            folds.append(fold)
        result = paired_report(predictions, cfg)
        result["by_season"] = {
            str(s): paired_report([r for r in predictions if r["season"] == s], cfg)
            for s in cfg["evaluation_seasons"]
        }
        result["folds"] = folds
        report["targets"][target] = result
        write_once(root / f"{target}-paired-predictions.json", predictions)
        train = [r for r in rows if r["season"] <= 2024]
        cal = [r for r in rows if r["season"] == 2025]
        model = fit_target(train, target, selected[target])
        preds = predict_linear(model, [r["features"][target] for r in cal])
        deployment["targets"][target] = {
            "model": model,
            "gate": result["gate"],
            "features": FEATURES[target],
            "radii": {
                "candidate": interval_radius(
                    [p - r["actual"][target] for p, r in zip(preds, cal, strict=True)],
                    cfg["interval_level"],
                ),
                "baseline": interval_radius(
                    [r["baseline"][target] - r["actual"][target] for r in cal],
                    cfg["interval_level"],
                ),
            },
        }
    # All fitting/calibration evidence must be earlier than this real prospective freeze.
    if max(time(r["available_at"]) for r in rows) >= time(declaration["frozen_at"]):
        raise ValueError("HISTORICAL_OUTCOME_AFTER_DECLARATION")
    write_once(root / "model.json", deployment)
    report["model_sha256"] = digest((root / "model.json").read_bytes())
    write_once(root / "feature-rows.json", rows)
    write_once(root / "evaluation.json", report)
    return report


def capture_pace(cfg, root):
    """One bounded public capture, outside production source collection."""
    destination = root / "pace-download.parquet"
    headers = root / "pace-download.headers"
    completed = subprocess.run(
        [
            "curl",
            "--fail",
            "--location",
            "--silent",
            "--show-error",
            "--max-time",
            "120",
            "--dump-header",
            str(headers),
            "--output",
            str(destination),
            cfg["pace_url"],
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    receipt = {
        "source_url": cfg["pace_url"],
        "retrieved_at": stamp(datetime.now(UTC)),
        "status": "AVAILABLE" if completed.returncode == 0 else "UNAVAILABLE",
        "error": completed.stderr.strip() or None,
    }
    if completed.returncode == 0:
        body = destination.read_bytes()
        identity = digest(body)
        write_once(root / "raw" / f"{identity}.parquet", body)
        receipt.update(sha256=identity, path=str(root / "raw" / f"{identity}.parquet"))
    write_once(root / "captures" / (digest(canonical(receipt)) + ".json"), receipt)
    replace_view(root / "pace-receipt.json", receipt)
    return receipt


def latest_final(game, cutoff):
    outcomes = [o for o in game.get("outcomes", []) if time(o["observed_at"]) <= cutoff]
    if not outcomes:
        return None
    latest = max(outcomes, key=lambda o: o["version"])
    same_version = [o for o in outcomes if o["version"] == latest["version"]]
    fields = ("status", "home_score", "away_score")
    if len({tuple(o.get(k) for k in fields) for o in same_version}) > 1:
        return None
    if latest["status"] != "FINAL" or not valid_score(latest):
        return None
    return latest


def live_history(view, games, cfg, root, cutoff):
    receipt = (
        json.loads((root / "pace-receipt.json").read_text())
        if (root / "pace-receipt.json").exists()
        else None
    )
    pace = {}
    if receipt and receipt["status"] == "AVAILABLE" and time(receipt["retrieved_at"]) <= cutoff:
        path = Path(receipt["path"])
        if digest(path.read_bytes()) != receipt["sha256"]:
            raise ValueError("PACE_SOURCE_HASH_MISMATCH")
        pace = pace_counts(
            pl.read_parquet(
                path, columns=["game_id", "posteam", "rush_attempt", "pass_attempt", "sack"]
            ),
            cfg,
        )
    for g in view["games"]:
        final = latest_final(g, cutoff)
        if not final or time(g["kickoff"]) >= cutoff or type(g.get("neutral_site")) is not bool:
            continue
        games.append(
            {
                "game_id": g["game_id"],
                "season": g["season"],
                "week": g["week"],
                "home": g["home"],
                "away": g["away"],
                "home_score": final["home_score"],
                "away_score": final["away_score"],
                "neutral_site": g["neutral_site"],
                "game_type": "REG",
                "kickoff": g["kickoff"],
                "available_at": final["observed_at"],
                "pace": pace.get(g["game_id"], {}),
                "pace_available_at": receipt["retrieved_at"] if pace else None,
            }
        )
    return games, receipt


def eligible_market(quotes, cutoff, kickoff_at):
    eligible = []
    for q in quotes:
        try:
            if (
                q["target"] not in FEATURES
                or q["side"] not in ("home", "away", "over", "under")
                or (q["target"] == "margin" and q["side"] not in ("home", "away"))
                or (q["target"] == "total" and q["side"] not in ("over", "under"))
                or not q["provider"]
                or not q["sportsbook"]
                or q.get("kind") == "closing_without_timestamp"
                or not np.isfinite([q["line"], q["odds"]]).all()
                or abs(q["odds"]) < 100
            ):
                continue
            if time(q["market_timestamp"]) <= time(q["retrieval_timestamp"]) and all(
                time(q[k]) <= cutoff and time(q[k]) < kickoff_at
                for k in ("market_timestamp", "retrieval_timestamp")
            ):
                eligible.append(q)
        except (KeyError, TypeError, ValueError):
            continue
    return {
        "status": "AVAILABLE" if eligible else "UNAVAILABLE",
        "quotes": eligible,
        "reason": None if eligible else "NO_TIMESTAMPED_PREGAME_PRICE",
        "comparison_cutoff": stamp(cutoff),
        "cover_probability": None,
        "over_probability": None,
    }


def read_archives(root):
    records = []
    for path in sorted((root / "forecasts").glob("*.json")):
        record = json.loads(path.read_text())
        if record["archive_id"] != digest(
            canonical({k: v for k, v in record.items() if k != "archive_id"})
        ):
            raise ValueError("ARCHIVE_HASH_MISMATCH")
        records.append(record)
    return records


def archive_forecast(root, record, cfg, clock=None):
    clock = clock or (lambda: datetime.now(UTC))
    directory = root / "forecasts"
    directory.mkdir(parents=True, exist_ok=True)
    if not any(t["tracking_eligible"] for t in record["targets"].values()):
        return "GATE_FAILED"
    # One independent writer lock bounds the distinct-game cohort even under concurrent runs.
    with (root / "archive.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        existing = read_archives(root)
        if any(r["game_id"] == record["game_id"] for r in existing):
            return "ALREADY_ARCHIVED"
        if len(existing) >= cfg["future_games"]:
            return "COHORT_COMPLETE"
        now = clock()
        kick = time(record["kickoff"])
        generated = time(record["generated_at"])
        if (
            now >= kick
            or generated >= kick
            or not 0 <= (now - generated).total_seconds() <= cfg["maximum_archive_delay_seconds"]
        ):
            return "STARTED_OR_EXPIRED"
        if time(record["data_cutoff"]) > generated:
            raise ValueError("DATA_AFTER_GENERATION")
        frozen = time(record["model_frozen_at"])
        if frozen > time(record["data_cutoff"]):
            raise ValueError("MODEL_AFTER_CUTOFF")
        record = {**record, "archived_at": stamp(now), "cohort_index": len(existing) + 1}
        record["archive_id"] = digest(canonical(record))
        if not write_once(directory / (record["archive_id"] + ".json"), record, deadline=kick):
            return "STARTED_BEFORE_DURABLE_WRITE"
        return "ARCHIVED"


def forecast(cfg, root, quotes=None):
    verify_freeze(cfg, root)
    model_path = root / "model.json"
    model = json.loads(model_path.read_text())
    evaluation = json.loads((root / "evaluation.json").read_text())
    if digest(model_path.read_bytes()) != evaluation["model_sha256"]:
        raise ValueError("MODEL_HASH_MISMATCH")
    cutoff = datetime.now(UTC)
    source_path = Path(cfg["season_root"]).expanduser() / "view.json"
    source_body = source_path.read_bytes()
    view = json.loads(source_body)
    parent_sha256 = digest(source_body)
    # Retain every actual model input exactly; unrelated dashboard history is large.
    fields = (
        "game_id",
        "season",
        "week",
        "home",
        "away",
        "home_name",
        "away_name",
        "kickoff",
        "schedule_version",
        "neutral_site",
        "time_valid",
        "status",
        "outcomes",
    )
    projection = {
        "parent_view_sha256": parent_sha256,
        "snapshot_kind": "points-input-projection-v1",
        "last_successful_source_check": view["last_successful_source_check"],
        "games": [{k: g[k] for k in fields if k in g} for g in view["games"]],
    }
    source_body = canonical(projection)
    # Preserve the exact compact input bytes before any forecast can reference them.
    write_once(root / "sources" / (digest(source_body) + ".json"), source_body)
    age = (cutoff - time(view["last_successful_source_check"])).total_seconds()
    fresh = 0 <= age <= cfg["maximum_source_age_seconds"]
    games, _ = historical_games(cfg)
    games, receipt = live_history(view, games, cfg, root, cutoff)
    archives = {r["game_id"]: r for r in read_archives(root)}
    rows = []
    for g in sorted(view["games"], key=lambda g: (g.get("kickoff") or "9999", g["game_id"])):
        common = {
            k: g[k]
            for k in ("game_id", "season", "week", "home", "away", "kickoff", "schedule_version")
        }
        common.update(
            home_name=g.get("home_name", g["home"]), away_name=g.get("away_name", g["away"])
        )
        if not g.get("kickoff"):
            rows.append({**common, "status": "UNAVAILABLE", "reason": "KICKOFF_UNVERIFIED"})
            continue
        kick = time(g["kickoff"])
        if not cutoff < kick <= cutoff + timedelta(days=cfg["slate_days"]):
            continue
        if g["game_id"] in archives:
            saved = archives[g["game_id"]]
            matches = all(
                g[k] == saved[k] for k in ("schedule_version", "home", "away", "neutral_site")
            ) and time(g["kickoff"]) == time(saved["kickoff"])
            rows.append(
                {
                    **saved,
                    "archive_status": "ALREADY_ARCHIVED",
                    "status": "SAVED" if matches else "STALE — SCHEDULE CHANGED",
                }
            )
            continue
        if (
            not fresh
            or not g.get("time_valid")
            or type(g.get("neutral_site")) is not bool
            or g["status"] != "STATUS_SCHEDULED"
        ):
            rows.append(
                {
                    **common,
                    "status": "STALE" if not fresh else "UNAVAILABLE",
                    "reason": "SAVED_SOURCE_STALE" if not fresh else "INVALID_SCHEDULE_OR_VENUE",
                }
            )
            continue
        f = features(g, games, cutoff, model["hfa"], cfg)
        if f["features"] is None:
            rows.append(
                {
                    **common,
                    "status": "UNAVAILABLE",
                    "reason": f["reason"],
                    "baseline": f["baseline"],
                }
            )
            continue
        targets = {}
        for target, spec in model["targets"].items():
            prediction = float(predict_linear(spec["model"], [f["features"][target]])[0])
            baseline = f["baseline"][target]
            targets[target] = {
                "candidate": prediction,
                "baseline": baseline,
                "gate": spec["gate"],
                "tracking_eligible": spec["gate"] == "PASS",
                "interval": [
                    prediction - spec["radii"]["candidate"],
                    prediction + spec["radii"]["candidate"],
                ],
                "baseline_interval": [
                    baseline - spec["radii"]["baseline"],
                    baseline + spec["radii"]["baseline"],
                ],
            }
        record = {
            **common,
            "neutral_site": g["neutral_site"],
            "status": "AVAILABLE",
            "targets": targets,
            "spread": spread(targets["margin"]["candidate"], g["home"], g["away"]),
            "version": cfg["version"],
            "model_sha256": evaluation["model_sha256"],
            "model_frozen_at": model["frozen_at"],
            "data_cutoff": stamp(cutoff),
            "source_timestamp": view["last_successful_source_check"],
            "source_sha256": digest(source_body),
            "parent_view_sha256": parent_sha256,
            "source_snapshot_kind": projection["snapshot_kind"],
            "pace_receipt": receipt,
            "history_ids": f["history_ids"],
            "pace_history_ids": f["pace_history_ids"],
            "feature_values": f["features"],
            "market": eligible_market((quotes or {}).get(g["game_id"], []), cutoff, kick),
            "generated_at": stamp(datetime.now(UTC)),
        }
        status = archive_forecast(root, record, cfg)
        saved = next((r for r in read_archives(root) if r["game_id"] == g["game_id"]), record)
        rows.append({**saved, "archive_status": status})
    result = {
        "version": cfg["version"],
        "generated_at": stamp(datetime.now(UTC)),
        "data_cutoff": stamp(cutoff),
        "source_timestamp": view["last_successful_source_check"],
        "status": "AVAILABLE" if fresh else "STALE",
        "model_sha256": evaluation["model_sha256"],
        "gates": {k: v["gate"] for k, v in model["targets"].items()},
        "archived_games": len(read_archives(root)),
        "cohort_limit": cfg["future_games"],
        "games": rows,
    }
    replace_view(root / "view.json", result)
    csv_fields = [
        "game_id",
        "matchup",
        "kickoff",
        "status",
        "margin",
        "home_spread",
        "favorite",
        "favorite_spread",
        "margin_low",
        "margin_high",
        "baseline_margin",
        "total",
        "total_low",
        "total_high",
        "baseline_total",
        "margin_gate",
        "total_gate",
        "model_version",
        "generated_at",
        "data_cutoff",
        "archive_status",
    ]
    out = io.StringIO()
    writer = csv.DictWriter(out, fieldnames=csv_fields)
    writer.writeheader()
    for r in rows:
        d = {
            "game_id": r["game_id"],
            "matchup": r["away"] + " @ " + r["home"],
            "kickoff": r["kickoff"],
            "status": r["status"],
            "model_version": r.get("version", cfg["version"]),
            "generated_at": r.get("generated_at"),
            "data_cutoff": r.get("data_cutoff"),
            "archive_status": r.get("archive_status"),
        }
        if r.get("targets"):
            m, t = r["targets"]["margin"], r["targets"]["total"]
            d.update(
                margin=m["candidate"],
                **r["spread"],
                margin_low=m["interval"][0],
                margin_high=m["interval"][1],
                baseline_margin=m["baseline"],
                total=t["candidate"],
                total_low=t["interval"][0],
                total_high=t["interval"][1],
                baseline_total=t["baseline"],
                margin_gate=m["gate"],
                total_gate=t["gate"],
            )
        writer.writerow(d)
    (root / "forecast-sheet.csv").write_text(out.getvalue())
    return result


def grade(cfg, root):
    verify_freeze(cfg, root)
    view = json.loads((Path(cfg["season_root"]).expanduser() / "view.json").read_text())
    by_id = {g["game_id"]: g for g in view["games"]}
    now = datetime.now(UTC)
    predictions = {t: [] for t in FEATURES}
    exclusions = []
    for r in read_archives(root):
        g = by_id.get(r["game_id"])
        reason = None
        if not g:
            reason = "GAME_UNAVAILABLE"
        elif not g.get("kickoff"):
            reason = "KICKOFF_UNVERIFIED"
        elif any(
            g[k] != r[k] for k in ("schedule_version", "home", "away", "neutral_site")
        ) or time(g["kickoff"]) != time(r["kickoff"]):
            reason = "SCHEDULE_CHANGED"
        elif time(r["archived_at"]) >= time(g["kickoff"]):
            reason = "NOT_PREGAME"
        elif time(g["kickoff"]) >= now:
            reason = "GAME_NOT_STARTED"
        final = latest_final(g, now) if g else None
        if not reason and final and time(final["observed_at"]) < time(g["kickoff"]):
            reason = "FINAL_OBSERVED_BEFORE_KICKOFF"
        if not reason and not final:
            reason = "PENDING_OR_UNAVAILABLE_FINAL"
        if reason:
            exclusions.append({"game_id": r["game_id"], "reason": reason})
            continue
        for target, spec in r["targets"].items():
            if not spec["tracking_eligible"]:
                continue
            actual = (
                final["home_score"] - final["away_score"]
                if target == "margin"
                else final["home_score"] + final["away_score"]
            )
            predictions[target].append(
                {
                    "game_id": r["game_id"],
                    "season": r["season"],
                    "week": r["week"],
                    "actual": actual,
                    "baseline": spec["baseline"],
                    "candidate": spec["candidate"],
                    "archive_id": r["archive_id"],
                    "outcome_version": final["version"],
                    "radii": {
                        k: (spec["interval"][1] - spec["interval"][0]) / 2
                        if k == "candidate"
                        else (spec["baseline_interval"][1] - spec["baseline_interval"][0]) / 2
                        for k in ("baseline", "candidate")
                    },
                }
            )
    result = {
        "version": cfg["version"],
        "descriptive_only": True,
        "exclusions": exclusions,
        "targets": {
            t: paired_report(rs, cfg) if rs else {"n": 0, "status": "NO_ELIGIBLE_FINALS"}
            for t, rs in predictions.items()
        },
        "predictions": predictions,
    }
    write_once(root / "scorecards" / (digest(canonical(result)) + ".json"), result)
    replace_view(root / "grade.json", result)
    return result


def sheet(cfg=None, root=None, clock=None):
    if cfg is None:
        cfg, root = configuration()
    clock = clock or datetime.now(UTC)
    try:
        result = json.loads((root / "view.json").read_text())
        age = (clock - time(result["generated_at"])).total_seconds()
        source_age = (clock - time(result["source_timestamp"])).total_seconds()
        if (
            age < 0
            or age > cfg["maximum_forecast_age_seconds"]
            or not 0 <= source_age <= cfg["maximum_source_age_seconds"]
        ):
            result["status"] = "STALE"
        for r in result["games"]:
            if r.get("kickoff") and time(r["kickoff"]) <= clock:
                r["status"] = "STARTED — SAVED FORECAST ONLY"
        if (root / "grade.json").exists():
            grade_view = json.loads((root / "grade.json").read_text())
            result["grading"] = {k: v for k, v in grade_view.items() if k != "predictions"}
        return result
    except (OSError, ValueError, KeyError):
        return {"status": "UNAVAILABLE", "reason": "NO_VALID_EXPERIMENTAL_SHEET", "games": []}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "command", choices=["freeze", "evaluate", "capture-pace", "forecast", "grade"]
    )
    parser.add_argument("--config", type=Path)
    parser.add_argument(
        "--market-quotes", type=Path, help="Optional timestamped quotes grouped by game ID"
    )
    args = parser.parse_args()
    cfg, root = configuration(args.config)
    if args.command == "freeze":
        result = freeze(cfg, root)
        print("Declaration:", digest(canonical(result)))
    elif args.command == "evaluate":
        result = evaluate(cfg, root)
        print(
            json.dumps(
                {
                    t: {
                        k: v
                        for k, v in r.items()
                        if k in ("baseline", "candidate", "gate", "mae_improvement")
                    }
                    for t, r in result["targets"].items()
                },
                indent=2,
            )
        )
    elif args.command == "capture-pace":
        print(json.dumps(capture_pace(cfg, root)))
    elif args.command == "forecast":
        result = forecast(
            cfg, root, json.loads(args.market_quotes.read_text()) if args.market_quotes else None
        )
        print(json.dumps({k: v for k, v in result.items() if k != "games"}, indent=2))
    else:
        result = grade(cfg, root)
        print(
            json.dumps(
                {t: {"n": r.get("candidate", {}).get("n", 0)} for t, r in result["targets"].items()}
            )
        )


if __name__ == "__main__":
    main()
