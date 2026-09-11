"""Preserved forecast explanations and immutable, evidence-bounded game reviews."""

from __future__ import annotations

import copy
import json
import math
import re
import tomllib
from datetime import UTC, datetime
from pathlib import Path

from season_scoring import _latest_outcome, _prediction_id
from week1_live import canonical, digest, stamp, write_once


def explain_forecast(record, model, created_at, reconstructed=False):
    """Explain only supplied saved Elo state; never derive ratings from probabilities."""
    result = {
        "status": "MISSING",
        "provenance": "RECONSTRUCTED" if reconstructed else "PREGAME",
        "created_at": stamp(created_at) if isinstance(created_at, datetime) else created_at,
        "prediction_id": _prediction_id(record),
        "model_id": record.get("model_id"),
        "summary": "Preserved model inputs are insufficient to reproduce this forecast.",
        "saved_inputs": copy.deepcopy(record.get("inputs", {})),
        "factors": [],
        "calculation": None,
        "limitations": [
            "Elo uses team ratings and venue; captured injury, quarterback and weather context receives no model adjustment.",
            "An uncalibrated probability is not a guarantee; one result cannot establish a model defect.",
        ],
    }
    try:
        policy = model["elo_policy"]
        hr, ar = float(model["ratings"][record["home"]]), float(model["ratings"][record["away"]])
        if not isinstance(record["neutral_site"], bool):
            raise TypeError("NEUTRAL_SITE_MISSING")
        advantage = 0.0 if record["neutral_site"] else float(policy["home_field_points"])
        scale, tie = float(policy["logistic_scale"]), float(model["p_tie"])
        if (
            not all(math.isfinite(v) for v in (hr, ar, advantage, scale, tie))
            or scale <= 0
            or not 0 <= tie <= 1
        ):
            raise ValueError("INVALID_SAVED_MODEL")
        conditional = 1 / (1 + 10 ** (-(hr - ar + advantage) / scale))
        probs = (conditional * (1 - tie), (1 - conditional) * (1 - tie), tie)
        if not all(
            math.isclose(float(record[k]), p, rel_tol=1e-12, abs_tol=1e-12)
            for k, p in zip(("p_home", "p_away", "p_tie"), probs, strict=True)
        ):
            raise ValueError("SAVED_MODEL_DOES_NOT_REPRODUCE_FORECAST")
    except (KeyError, TypeError, ValueError, OverflowError) as error:
        result["missing_reason"] = str(error)
        return result
    result.update(
        status="VERIFIED",
        summary=(
            f"Saved Elo rated {record['home']} at {hr:.1f} and {record['away']} at {ar:.1f}. "
            f"The venue adds {advantage:g} home rating points. After reserving {tie:.2%} for a tie, "
            f"the forecast is {probs[0]:.1%} home and {probs[1]:.1%} away."
        ),
        calculation={
            "home_rating": hr,
            "away_rating": ar,
            "home_advantage": advantage,
            "neutral_site": record["neutral_site"],
            "logistic_scale": scale,
            "rating_difference": hr - ar,
            "adjusted_difference": hr - ar + advantage,
            "p_home_non_tie": conditional,
            "p_home": probs[0],
            "p_away": probs[1],
            "p_tie": tie,
            "formula": "q = 1/(1+10^(-(home-away+venue)/scale)); home=q*(1-tie); away=(1-q)*(1-tie)",
        },
        factors=[
            {
                "name": "Saved team strength",
                "value": hr - ar,
                "unit": "Elo points",
                "detail": "Home minus away saved Elo, in rating points; not a probability contribution.",
            },
            {
                "name": "Venue",
                "value": advantage,
                "unit": "Elo points",
                "detail": "Neutral site: no home advantage."
                if record["neutral_site"]
                else "Saved home-field setting.",
            },
            {
                "name": "Tie allowance",
                "value": tie,
                "unit": "probability fraction",
                "detail": "Saved tie probability fraction, shared by both teams.",
            },
        ],
    )
    return result


def _read(path):
    return json.loads(path.read_text())


def _verified(path):
    value = _read(path)
    if digest(canonical(value)) != path.stem:
        raise ValueError("ANALYSIS_ARCHIVE_HASH_MISMATCH")
    return value


def _saved_model(record, root, cfg, policy_cache):
    model_id = record.get("model_id", "")
    if not isinstance(model_id, str) or not re.fullmatch(r"[a-f0-9]{64}", model_id):
        return {}
    for directory in (root, Path(cfg.get("legacy_data_root", str(root.parent))).expanduser()):
        path = directory / "models" / (model_id + ".json")
        if path.exists():
            model = _verified(path)
            if "elo_policy" not in model:
                model["elo_policy"] = policy_cache.get(model.get("policy_sha256"), {})
            return model
    return {}


def _policies(root, cfg):
    policies = {}
    for directory in {root, Path(cfg.get("legacy_data_root", str(root.parent))).expanduser()}:
        for path in (directory / "source-versions").glob("*.json"):
            value = _verified(path)
            for name, content in value.get("files", {}).items():
                if name.endswith(".toml"):
                    parsed = tomllib.loads(content)
                    if "elo" in parsed:
                        policies[digest(content.encode())] = parsed["elo"]
    # A byte-identical policy is valid saved evidence even if retrieved from checkout.
    policy_path = Path(cfg.get("model_policy", "configs/model_policy_v1.toml"))
    if not policy_path.is_absolute():
        policy_path = Path(__file__).parents[1] / policy_path
    if policy_path.exists():
        content = policy_path.read_bytes()
        policies[digest(content)] = tomllib.loads(content.decode()).get("elo", {})
    return policies


def _read_analysis(path):
    saved = _read(path)
    payload = {k: v for k, v in saved.items() if k not in {"created_at", "record_id"}}
    if digest(canonical(payload)) != path.stem or saved.get("record_id") != path.stem:
        raise ValueError("ANALYSIS_RECORD_HASH_MISMATCH")
    return saved


def _persist(root, namespace, payload, created_at):
    # The content key excludes observation time, so unchanged evidence is idempotent.
    key = digest(canonical(payload))
    path = root / "analysis" / namespace / (key + ".json")
    if path.exists():
        saved = _read(path)
        if (
            digest(
                canonical({k: v for k, v in saved.items() if k not in {"created_at", "record_id"}})
            )
            != key
        ):
            raise ValueError("ANALYSIS_RECORD_HASH_MISMATCH")
        return saved
    saved = {**payload, "created_at": created_at, "record_id": key}
    write_once(path, saved)
    return saved


def _experiments(root, cfg):
    research = Path(cfg.get("legacy_data_root", str(root.parent))).expanduser() / "research"
    paths = sorted((research / "evaluations" / "records").glob("*.json"))
    report = research / "reports" / "v2-challenger-2025.json"
    if not paths and report.exists():
        paths = [report]
    result = []
    for path in paths:
        raw = _read(path)
        if path.parent.name == "records" and digest(canonical(raw)) != path.stem:
            raise ValueError("EXPERIMENT_HASH_MISMATCH")
        result.append(
            {
                "experiment_id": digest(canonical(raw)),
                "scope": "HISTORICAL_EXPERIMENT",
                "relationship": "Related global evaluation; not a causal explanation or repair for this game.",
                "evidence_basis": raw.get("evidence_basis"),
                "evidence_grade": raw.get("evidence_grade"),
                "versions": raw.get("experiment"),
                "chronology": raw.get("fold"),
                "matched_games": raw.get("matched_games"),
                "horizon": raw.get("origin", "NOT_RECORDED"),
                "results": {
                    k: {n: v for n, v in val.items() if n != "event_ids"}
                    for k, val in raw.get("models", {}).items()
                },
                "decision": raw.get(
                    "promotion_decision",
                    raw.get(
                        "promotion",
                        {"action": "NOT_ADOPTED", "blockers": raw.get("promotion_blockers", [])},
                    ),
                ),
                "status": "rejected" if raw.get("promotion_eligible") is False else "observed",
                "production_change": "NONE",
                "hypothesis": "Lagged EPA improves matched holdout log loss over Elo.",
                "proposed_change": "Evaluate an EPA logistic challenger against Elo without altering live forecasts.",
                "pregame_availability": "Historical reconstruction, Grade C; original pregame availability is not established.",
                "rollback": "Keep the saved live Elo model; no candidate was automatically adopted.",
                "rejection_rationale": (
                    "EPA holdout log loss is worse than Elo; historical reconstruction is ineligible for live promotion."
                    if raw.get("models", {}).get("epa_logistic", {}).get("multinomial_log_loss", 0)
                    > raw.get("models", {}).get("elo", {}).get("multinomial_log_loss", float("inf"))
                    else "Promotion requires reviewed evidence; see recorded blockers."
                ),
            }
        )
    return result


def _issues(prediction, evidence):
    issues = []
    inputs = (prediction or {}).get("inputs", {})
    if not inputs:
        issues.append(
            {
                "category": "missing_or_stale_inputs",
                "detail": "No detailed pregame source context was preserved.",
            }
        )
    for name, value in inputs.items():
        if value is None or value.get("status") in {"MISSING", "STALE"}:
            issues.append(
                {
                    "category": "missing_or_stale_inputs",
                    "inputs": [name],
                    "detail": f"Saved {name} context is missing or stale; impact is unknown.",
                }
            )
        elif value.get("used_by_model") is False:
            issues.append(
                {
                    "category": "available_but_unused",
                    "inputs": [name],
                    "detail": f"Saved {name} context was captured but received no Elo adjustment.",
                }
            )
    issues.append(
        {
            "category": "insufficient_evidence",
            "detail": "Outcome and play summaries do not identify why a pregame probability was wrong; no causal change is approved.",
        }
    )
    # Sourced play facts are presented separately, never called unexpected without a baseline.
    if evidence:
        issues.append(
            {
                "category": "in_game_evidence",
                "detail": "See sourced postgame evidence. Unexpectedness and causal model impact are not established.",
            }
        )
    return issues


def refresh_analysis(view, root, cfg, clock=None, evidence=None):
    root = Path(root)
    now = clock() if callable(clock) else clock or datetime.now(UTC)
    created = stamp(now) if isinstance(now, datetime) else now
    policies = _policies(root, cfg)
    experiments = [
        _persist(root, "historical-improvements", e, created) for e in _experiments(root, cfg)
    ]
    rows = {r["game_id"]: r for r in view.get("scorecards", {}).get("games", [])}
    evidence = evidence if evidence is not None else view.get("postgame_evidence", {})
    analysis = {}
    archived_explanations = {
        r.get("forecast_sha256"): r
        for r in (_read_analysis(p) for p in (root / "analysis" / "explanations").glob("*.json"))
    }
    archived_reviews = [_read_analysis(p) for p in (root / "analysis" / "reviews").glob("*.json")]
    archived_improvements = [
        _read_analysis(p) for p in (root / "analysis" / "improvements").glob("*.json")
    ]
    for game in view.get("games", []):
        gid = game["game_id"]
        explanations = {}
        for prediction in game.get("predictions", []):
            pid = _prediction_id(prediction) or digest(canonical(prediction))
            if prediction.get("explanation"):
                explanations[pid] = copy.deepcopy(prediction["explanation"])
                continue
            forecast_hash = digest(canonical(prediction))
            if forecast_hash in archived_explanations:
                explanations[pid] = archived_explanations[forecast_hash]
                continue
            # Exclude reconstruction time from identity; preserve first reconstruction time.
            content = explain_forecast(
                prediction, _saved_model(prediction, root, cfg, policies), created, True
            )
            content.pop("created_at")
            explanations[pid] = _persist(
                root,
                "explanations",
                {**content, "game_id": gid, "forecast_sha256": forecast_hash},
                created,
            )
        row = rows.get(gid, {})
        prediction = next(
            (
                p
                for p in game.get("predictions", [])
                if _prediction_id(p) == row.get("prediction_id") and row.get("prediction_id")
            ),
            None,
        )
        review, improvements = None, []
        history = [r for r in archived_reviews if r.get("game_id") == gid]
        if row.get("result") is not None or history:
            status = (
                "PENDING"
                if row.get("result") is None
                else "MISSING_FORECAST"
                if prediction is None
                else "TIE"
                if row["result"] == "tie"
                else "CORRECT"
                if row.get("winner_correct")
                else "INCORRECT"
            )
            final = _latest_outcome(game, now) if isinstance(now, datetime) else None
            postgame = evidence.get(gid)
            semantic = lambda value: {
                k: v
                for k, v in (value or {}).items()
                if k not in {"captured_at", "last_checked_at", "checked_at"}
            }
            for prior in history:
                if semantic(prior.get("evidence")) == semantic(postgame):
                    postgame = prior.get("evidence")
                    break
            payload = {
                "game_id": gid,
                "prediction_id": row.get("prediction_id"),
                "outcome_version": row.get("outcome_version"),
                "status": status,
                "summary": {
                    "PENDING": "The prior final result was retracted; scoring is pending.",
                    "MISSING_FORECAST": "No eligible saved pregame forecast exists. This is a coverage gap, not a losing pick.",
                    "TIE": "The game tied. Three-way probability scores apply; winner accuracy excludes ties.",
                    "CORRECT": "The saved pregame pick won; its probability remains subject to calibration review.",
                    "INCORRECT": "The saved pregame pick lost; the outcome alone does not establish a model defect.",
                }[status],
                "final_result": final,
                "scorecard": row,
                "metrics": {k: row.get(k) for k in ("multiclass_brier", "multinomial_log_loss")},
                "saved_pregame_context": copy.deepcopy((prediction or {}).get("inputs", {})),
                "issues": _issues(prediction, evidence.get(gid)),
                "evidence": postgame,
                "explanation_id": explanations.get(row.get("prediction_id"), {}).get("record_id"),
                "causal_claims": [],
                "production_change": "NONE",
            }
            if prediction and row.get("result"):
                side = row["result"]
                probability = prediction["p_" + side]
                winner = "a tie" if side == "tie" else prediction[side]
                payload["summary"] += (
                    f" The saved forecast assigned {probability:.1%} to {winner}. "
                    "This model made no score or margin forecast."
                )
                payload["actual_result_pregame_probability"] = probability
            review = _persist(root, "reviews", payload, created)
            review = {**review, "review_id": review["record_id"]}
            if not any(r["record_id"] == review["record_id"] for r in history):
                history.append(review)
            improvement = _persist(
                root,
                "improvements",
                {
                    "game_id": gid,
                    "status": "observed",
                    "evidence_refs": [review["record_id"], row.get("prediction_id")],
                    "pregame_availability": copy.deepcopy((prediction or {}).get("inputs", {})),
                    "hypothesis": (
                        "Preserving original source timestamps would make missing or unused pregame context auditable. "
                        "Its predictive value is untested; no game-specific causal defect was established."
                    ),
                    "proposed_change": "Preserve source gaps; test any proposed feature on chronological held-out matched horizons before adoption.",
                    "experiment": {
                        "status": "NOT_STARTED",
                        "chronology": "NOT_SELECTED; requires a future untouched evaluation period",
                        "config_version": "NOT_CREATED",
                        "dependencies": [
                            "A validated dataset with original pregame availability timestamps",
                            "Predeclared chronological train/calibration/holdout periods and matched forecast horizons",
                        ],
                        "evaluation_policy_reference": "configs/model_policy_v1.toml; historical experiment versions are linked separately",
                        "model_version": (prediction or {}).get("model_version"),
                        "data_version": (prediction or {}).get("input_fingerprint"),
                        "results": None,
                        "matched_elo_comparisons": view.get("scorecards", {}).get(
                            "matched_elo_comparisons", []
                        ),
                    },
                    "related_historical_experiments": [e["experiment_id"] for e in experiments],
                    "decision": "NO_CHANGE; evidence review required",
                    "rollback": (prediction or {}).get("model_id"),
                },
                created,
            )
            improvements = [
                r
                for r in archived_improvements
                if r.get("game_id") == gid and r["record_id"] != improvement["record_id"]
            ] + [improvement]
        analysis[gid] = {
            "explanations": explanations,
            "review": review,
            "review_history": sorted(history, key=lambda x: (x["created_at"], x["record_id"])),
            "improvements": improvements,
        }
    view["analysis"] = analysis
    view["historical_experiments"] = experiments
    return analysis


def game_detail(view, game_id, root, cfg):
    """Read-only API assembly: no current model reconstruction on page visits."""
    game = next((g for g in view.get("games", []) if g["game_id"] == game_id), None)
    if game is None:
        raise KeyError(game_id)
    row = next(
        (r for r in view.get("scorecards", {}).get("games", []) if r["game_id"] == game_id), {}
    )
    analysis = view.get("analysis", {}).get(game_id, {})
    history = [
        {
            **p,
            "explanation": p.get("explanation")
            or analysis.get("explanations", {}).get(_prediction_id(p)),
        }
        for p in game.get("predictions", [])
    ]
    official = next(
        (
            p
            for p in history
            if _prediction_id(p) == row.get("prediction_id") and row.get("prediction_id")
        ),
        None,
    )
    return {
        **game,
        "game": game,
        "scorecard": row,
        "official_prediction": official,
        "explanation": None if official is None else official.get("explanation"),
        "prediction_history": history,
        "postgame_review": analysis.get("review"),
        "review_history": analysis.get("review_history", []),
        "improvements": analysis.get("improvements", []),
        "historical_experiments": view.get("historical_experiments", []),
        "current_context": game.get("inputs", {}),
    }
