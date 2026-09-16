"""Private prospective challenger publication; production records are never modified."""

from __future__ import annotations

import copy
import json
import re
import tomllib
from datetime import datetime, timedelta
from pathlib import Path

from season_live import (
    all_records,
    canonical,
    digest,
    journal,
    origin_state,
    publish,
    stamp,
    write_once,
)
from season_scoring import (
    ORIGINS,
    _latest_outcome,
    _prediction_time,
    _result,
    _score_rows,
    _selected_metrics,
    _valid_prediction,
)

from nfl_predictor.evaluation.metrics import multiclass_brier
from nfl_predictor.models.tie import to_three_way


def _instant(value):
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        raise ValueError("SHADOW_TIMESTAMP_REQUIRES_TIMEZONE")
    return parsed


def load_artifact(spec, clock):
    path = Path(spec["artifact_path"]).expanduser()
    raw = path.read_bytes()
    if digest(raw) != spec["artifact_sha256"]:
        raise ValueError("SHADOW_ARTIFACT_HASH_MISMATCH")
    artifact = json.loads(raw)
    if not isinstance(artifact, dict):
        raise TypeError("SHADOW_ARTIFACT_INVALID")
    for key in ("created_at", "generated_at", "frozen_at"):
        if artifact.get(key) and _instant(artifact[key]) > clock:
            raise ValueError("SHADOW_ARTIFACT_FROM_FUTURE")
    return artifact


def _candidate(kind, baseline, game, artifact, clock):
    if kind == "calibration":
        from season_probability import calibrated_probability

        conditional = baseline["p_home"] / (baseline["p_home"] + baseline["p_away"])
        mapped = calibrated_probability(conditional, artifact)
        ph, pa, pt = to_three_way(mapped, baseline["p_tie"])
        return {
            "status": "VALID",
            "p_home": ph,
            "p_away": pa,
            "p_tie": pt,
            "evidence": {
                "baseline_conditional_home": conditional,
                "calibrated_conditional_home": mapped,
            },
            "limitations": [
                "Research-fitted calibration; prospective shadow only; no production promotion."
            ],
        }
    if kind == "qb":
        from season_qb import predict_qb

        return predict_qb(baseline, game, artifact, lambda: clock)
    raise ValueError("UNKNOWN_SHADOW_KIND")


def _metrics_row(game, prediction, clock):
    result = _result(_latest_outcome(game, clock))
    return {
        "game_id": game["game_id"],
        "week": game["week"],
        "result": result,
        "coverage": "PENDING"
        if _instant(game["kickoff"]) > clock or (prediction and result is None)
        else "MISSED"
        if prediction is None
        else "SETTLED",
        **_selected_metrics(prediction, result),
    }


def score_shadow(games, records, clock, starts):
    """Freeze latest pregame shadow and its paired baseline, never choose by outcome."""
    cards = {}
    for model, model_records in records.items():
        eligible = [g for g in games if g.get("kickoff") and _instant(g["kickoff"]) > starts[model]]
        horizon_cards = {}
        for horizon in ("LATEST", *ORIGINS):
            rows, pairs, baseline_rows = [], [], []
            for game in eligible:
                kick = _instant(game["kickoff"])
                candidates = [
                    p
                    for p in model_records
                    if p["game_id"] == game["game_id"]
                    and (horizon == "LATEST" or p["origin"] == horizon)
                    and _valid_prediction(p, game, kick, clock, allow_challenger=True)
                ]
                selected = max(candidates, key=_prediction_time) if candidates else None
                row = _metrics_row(game, selected, clock)
                rows.append(row)
                if selected and row["result"] is not None:
                    baseline = selected["paired_baseline"]
                    if not _valid_prediction(
                        baseline, game, kick, _instant(selected["generated_at"])
                    ):
                        raise ValueError("SHADOW_PAIRED_BASELINE_INVALID")
                    baseline_rows.append(_metrics_row(game, baseline, clock))
                    pairs.append(row)
            a, b = _score_rows(pairs), _score_rows(baseline_rows)
            horizon_cards[horizon] = {
                "summary": _score_rows(rows),
                "games": rows,
                "paired_comparison": {
                    "n": len(pairs),
                    "game_ids": [x["game_id"] for x in pairs],
                    "shadow": a,
                    "baseline": b,
                    "delta_log_loss": a["multinomial_log_loss"] - b["multinomial_log_loss"]
                    if pairs
                    else None,
                    "delta_brier": a["multiclass_brier"] - b["multiclass_brier"] if pairs else None,
                    "interpretation": "Shadow minus its frozen production reference at the shadow horizon. Negative favors shadow.",
                },
            }
        cards[model] = {
            "prospective_start": stamp(starts[model]),
            "eligible_games": len(eligible),
            "excluded_before_start": len(games) - len(eligible),
            "horizons": horizon_cards,
        }
    return cards


def _current_finals(view, at):
    finals = []
    for game in view["games"]:
        outcome = _latest_outcome(game, at)
        if _result(outcome) is None:
            continue
        finals.append(
            {
                **{
                    key: game[key]
                    for key in ("game_id", "season", "week", "home", "away", "kickoff")
                },
                "status": "FINAL",
                "outcome_version": outcome["version"],
                "observed_at": outcome["observed_at"],
            }
        )
    return finals


def refresh_qb_state(view, artifact, spec, root, cfg, clock):
    """Keep the fitted definition fixed while capturing new finalized-game QB statistics."""
    from season_probability import historical_games
    from season_qb import refresh_state

    raw_config = Path(spec["runtime_config_path"]).expanduser().read_bytes()
    if digest(raw_config) != spec["runtime_config_sha256"]:
        raise ValueError("SHADOW_QB_RUNTIME_CONFIG_CHANGED")
    lineage = artifact.get("lineage", {})
    if (
        lineage.get("runtime_config_sha256") != spec["runtime_config_sha256"]
        or lineage.get("historical_source_sha256") != spec["history_source_sha256"]
    ):
        raise ValueError("SHADOW_QB_DEFINITION_INPUT_BINDING_MISMATCH")
    runtime = tomllib.loads(raw_config.decode())
    if runtime["zero_dollar_mode"] is not True or runtime["allow_paid_usage"] is not False:
        raise ValueError("ZERO_DOLLAR_GUARD")
    source = Path(spec["history_source_path"]).expanduser()
    if digest(source.read_bytes()) != spec["history_source_sha256"]:
        raise ValueError("SHADOW_QB_HISTORICAL_SOURCE_CHANGED")
    history, _ = historical_games(
        source,
        {
            "history_start": runtime["history_start"],
            "known_benchmark_season": runtime["known_benchmark_season"],
            "team_aliases": runtime.get("team_aliases", {}),
            "historical_result_delay_hours": runtime["historical_result_delay_hours"],
        },
    )
    finalized = [{**game, "status": "FINAL"} for game in history if game["game_type"] == "REG"]
    finalized += _current_finals(view, clock())
    runtime["qb_state_through_season"] = cfg["season"]
    runtime["qb_state_supplements"] = cfg.get("qb_state_supplements", [])
    try:
        return refresh_state(artifact, runtime, root, finalized, clock)
    except Exception as error:
        # Optional challenger ingestion must never abort production view publication.
        raise ValueError(f"QB_STATE_REFRESH_FAILED: {type(error).__name__}: {error}") from error


def run_shadow(view, root, cfg, clock, *, predictor=None):
    if cfg["zero_dollar_mode"] is not True or cfg["allow_paid_usage"] is not False:
        raise ValueError("ZERO_DOLLAR_GUARD")
    at = clock()
    specs = cfg.get("shadow_models", [])
    output = {
        "status": "SHADOW_ONLY" if specs else "COLLECTING_INPUTS",
        "production_change": "NONE",
        "models": {},
        "games": {},
        "scorecards": {},
    }
    all_models, starts = {}, {}
    current_finals = _current_finals(view, at)
    code_root = Path(__file__).resolve().parents[1]
    code_files = [
        "ops/season_shadow.py",
        "ops/season_qb.py",
        "ops/season_probability.py",
        "configs/season_probability.toml",
        "configs/season_qb.toml",
        "ops/season_live.py",
        "ops/season_sources.py",
        "ops/season_scoring.py",
        "ops/week1_live.py",
        "configs/season_live.toml",
        "configs/model_policy_v1.toml",
        "uv.lock",
    ]
    code_hashes = {
        name: digest((code_root / name).read_bytes())
        for name in code_files
        if (code_root / name).exists()
    }
    source_version = journal(
        root / "shadow",
        "source-versions",
        {
            "hashes": code_hashes,
            "files": {name: (code_root / name).read_text() for name in code_hashes},
        },
    )
    # Collect original pregame source evidence even when every challenger is unavailable.
    for game in view["games"]:
        if not re.fullmatch(r"[A-Za-z0-9_-]+", game["game_id"]):
            raise ValueError("SHADOW_GAME_ID_INVALID")
        if not game.get("kickoff") or not at < _instant(game["kickoff"]) <= at + timedelta(
            days=cfg["slate_days"]
        ):
            continue
        inputs = game.get("inputs", {})
        future = any(
            v.get(key) and _instant(v[key]) > at
            for v in inputs.values()
            for key in ("captured_at", "source_updated_at", "published_at")
        )
        if future:
            output["games"][game["game_id"]] = {
                "evidence": {"status": "BLOCKED", "reason": "FUTURE_INPUT_TIMESTAMP"}
            }
            continue
        ref = journal(
            root / "shadow",
            "prospective-inputs",
            {
                "game_id": game["game_id"],
                "kickoff": game["kickoff"],
                "schedule_version": game["schedule_version"],
                "inputs": inputs,
                "source_checks": view.get("sources", {}),
                "production_model_state": view["model"].get("model_state_sha256"),
            },
        )
        output["games"].setdefault(game["game_id"], {})["evidence"] = {
            "status": "CAPTURED",
            "record_id": ref,
        }
    for spec in specs:
        name = spec["name"]
        if not re.fullmatch(r"[A-Za-z0-9_-]+", name):
            raise ValueError("SHADOW_MODEL_NAME_INVALID")
        starts[name] = _instant(spec["prospective_start"])
        model_root = root / "shadow" / name
        records = [p for g in view["games"] for p in all_records(model_root, g["game_id"])]
        all_models[name] = records
        frozen = None
        try:
            frozen_path = model_root / "experiment.json"
            if frozen_path.exists():
                frozen = json.loads(frozen_path.read_text())
                starts[name] = _instant(frozen["prospective_start"])
            expected_spec = {
                key: spec[key] for key in ("name", "kind", "artifact_sha256", "prospective_start")
            }
            expected_spec.update(
                {
                    key: spec[key]
                    for key in ("runtime_config_sha256", "history_source_sha256")
                    if key in spec
                }
            )
            if frozen is not None and frozen != expected_spec:
                raise ValueError("SHADOW_EXPERIMENT_CHANGED_REQUIRES_NEW_MODEL_NAME")
            artifact = load_artifact(spec, at)
            if not artifact.get("production_policy_sha256") or artifact[
                "production_policy_sha256"
            ] != view["model"].get("policy_sha256"):
                raise ValueError("SHADOW_BASELINE_POLICY_MISMATCH")
            if starts[name] > at:
                raise ValueError("SHADOW_EXPERIMENT_NOT_STARTED")
            if frozen is None:
                write_once(frozen_path, expected_spec)
            if spec["kind"] == "qb" and predictor is None:
                artifact = refresh_qb_state(view, artifact, spec, root, cfg, clock)
            artifact_ref = journal(root / "shadow", "artifacts", artifact)
            evaluation = None
            if spec.get("evaluation_path"):
                raw_evaluation = Path(spec["evaluation_path"]).expanduser().read_bytes()
                if digest(raw_evaluation) != spec["evaluation_sha256"]:
                    raise ValueError("SHADOW_EVALUATION_HASH_MISMATCH")
                saved_evaluation = json.loads(raw_evaluation)
                if (
                    saved_evaluation.get(
                        "artifact_file_sha256", saved_evaluation.get("artifact_sha256")
                    )
                    != spec["artifact_sha256"]
                ):
                    raise ValueError("SHADOW_EVALUATION_MODEL_MISMATCH")
                evaluation = {
                    "report_id": journal(root / "shadow", "evaluations", saved_evaluation),
                    "evidence_grade": saved_evaluation.get("evidence_grade", "C"),
                    "results": saved_evaluation.get("evaluation", saved_evaluation.get("metrics")),
                    "development_interpretation": "Fitting/selection sample; not an out-of-sample performance estimate.",
                    "limitations": saved_evaluation.get("limitations", []),
                    "promotion_eligible": False,
                }
            output["models"][name] = {
                "status": "SHADOW_ONLY",
                "artifact_id": artifact_ref,
                "artifact_sha256": spec["artifact_sha256"],
                "kind": spec["kind"],
                "historical_evaluation": evaluation,
                "state_data_as_of": artifact.get("data_as_of"),
                "state_source_sha256": artifact.get("state_raw_sha256"),
                "promotion": "NOT_APPROVED",
            }
        except (OSError, ValueError, KeyError, TypeError) as error:
            output["models"][name] = {"status": "BLOCKED", "reason": str(error)}
            continue
        for game in view["games"]:
            gid = game["game_id"]
            states = output["games"].setdefault(gid, {})
            if not game.get("kickoff"):
                states[name] = {"status": "BLOCKED", "reason": "KICKOFF_TBD"}
                continue
            kick = _instant(game["kickoff"])
            if at >= kick:
                states[name] = {"status": "FROZEN", "reason": "NO_POSTKICKOFF_SHADOW_PUBLICATION"}
                continue
            if game.get("status") != "STATUS_SCHEDULED" or kick > at + timedelta(
                days=cfg["slate_days"]
            ):
                continue
            baseline = game.get("prediction")
            if not baseline or not _valid_prediction(baseline, game, kick, at):
                states[name] = {"status": "BLOCKED", "reason": "NO_VALID_PRODUCTION_REFERENCE"}
                continue
            if states.get("evidence", {}).get("status") != "CAPTURED":
                states[name] = {"status": "BLOCKED", "reason": "NO_VALID_PROSPECTIVE_INPUTS"}
                continue
            try:
                prediction_game = copy.deepcopy(game)
                prediction_game["qb_state_required_game_ids"] = [
                    final["game_id"]
                    for final in current_finals
                    if final["season"] == game["season"]
                    and _instant(final["kickoff"]) < kick
                    and {final["home"], final["away"]}.intersection({game["home"], game["away"]})
                ]
                prediction = (predictor or _candidate)(
                    spec["kind"], baseline, prediction_game, artifact, clock()
                )
            except (ValueError, KeyError, TypeError, ArithmeticError) as error:
                states[name] = {"status": "BLOCKED", "reason": str(error)}
                continue
            states[name] = copy.deepcopy(prediction)
            if prediction.get("conditional_scenarios"):
                try:
                    for scenario in prediction["conditional_scenarios"]:
                        values = [scenario.get(key) for key in ("p_home", "p_away", "p_tie")]
                        if any(value is not None for value in values):
                            multiclass_brier([values], ["home"])
                except (TypeError, ValueError, AttributeError):
                    states[name] = {
                        "status": "BLOCKED",
                        "reason": "INVALID_CONDITIONAL_DISTRIBUTION",
                    }
                    continue
                conditional_root = model_root / "conditional"
                conditional_fingerprint = digest(
                    canonical(
                        {
                            "baseline": baseline.get("revision_id"),
                            "model": artifact_ref,
                            "evidence": states["evidence"]["record_id"],
                            "schedule_version": game["schedule_version"],
                            "scenarios": prediction["conditional_scenarios"],
                        }
                    )
                )
                saved_conditional = all_records(conditional_root, gid)
                if not any(
                    p["input_fingerprint"] == conditional_fingerprint for p in saved_conditional
                ):
                    conditional = {
                        "game_id": gid,
                        "kickoff": game["kickoff"],
                        "schedule_version": game["schedule_version"],
                        "generated_at": stamp(clock()),
                        "origin": "CONDITIONAL",
                        "status": "CONDITIONAL",
                        "role": "scenario",
                        "mode": "CONDITIONAL_RESEARCH",
                        "model_version": name,
                        "model_id": artifact_ref,
                        "model_definition_sha256": spec["artifact_sha256"],
                        "source_version": source_version,
                        "input_fingerprint": conditional_fingerprint,
                        "paired_baseline": copy.deepcopy(baseline),
                        "inputs": copy.deepcopy(game["inputs"]),
                        "scenarios": copy.deepcopy(prediction["conditional_scenarios"]),
                        "scoring": "EXCLUDED; unweighted conditional scenarios are not official or shadow outcome forecasts",
                    }
                    if not publish(conditional_root, conditional, kick, clock):
                        states[name]["conditional_scenarios"] = []
                        states[name]["conditional_status"] = "NOT_PUBLISHED_DEADLINE"
                saved_conditional = all_records(conditional_root, gid)
                states[name]["conditional_history"] = [
                    {
                        key: p[key]
                        for key in (
                            "revision_id",
                            "generated_at",
                            "published_at",
                            "schedule_version",
                        )
                    }
                    for p in saved_conditional
                ]
            if prediction.get("status") not in {"VALID", "AVAILABLE"}:
                continue
            fingerprint = digest(
                canonical(
                    {
                        "baseline": baseline.get("revision_id"),
                        "artifact": artifact_ref,
                        "evidence": states["evidence"]["record_id"],
                        "probabilities": [prediction[k] for k in ("p_home", "p_away", "p_tie")],
                    }
                )
            )
            current = [
                p
                for p in records
                if p["game_id"] == gid and p["schedule_version"] == game["schedule_version"]
            ]
            origins = [
                o
                for o in ORIGINS
                if origin_state(kick, o, at, cfg)[0] == "DUE"
                and not any(p["origin"] == o for p in current)
            ]
            if not current:
                origins.append("ON_DEMAND")
            elif not any(p["input_fingerprint"] == fingerprint for p in current):
                origins.append("UPDATE")
            publication_failed = False
            attempts = {}
            for origin in origins:
                generated = clock()
                deadline = (
                    origin_state(kick, origin, generated, cfg)[2] if origin in ORIGINS else kick
                )
                saved_inputs = copy.deepcopy(game.get("inputs", {}))
                if spec["kind"] == "qb" and "expected_qb" in saved_inputs:
                    saved_inputs["expected_qb"]["used_by_model"] = True
                    saved_inputs["expected_qb"]["usage"] = (
                        "QB residual feature; conditional on the timestamped listed starter, subject to availability guards"
                    )
                record = {
                    "game_id": gid,
                    "home": game["home"],
                    "away": game["away"],
                    "kickoff": game["kickoff"],
                    "schedule_version": game["schedule_version"],
                    "origin": origin,
                    "generated_at": stamp(generated),
                    "role": "challenger",
                    "mode": "LIVE_SHADOW",
                    "status": "VALID",
                    "model_version": name,
                    "model_id": artifact_ref,
                    "artifact_sha256": spec["artifact_sha256"],
                    "model_definition_sha256": spec["artifact_sha256"],
                    "state_artifact_sha256": artifact_ref,
                    "required_prior_result_ids": prediction_game["qb_state_required_game_ids"],
                    "source_version": source_version,
                    "code_hashes": code_hashes,
                    "paired_baseline": copy.deepcopy(baseline),
                    "input_fingerprint": fingerprint,
                    "inputs": saved_inputs,
                    "prospective_evidence_id": states["evidence"]["record_id"],
                    "reasons": [origin, "SHADOW_ONLY; FROZEN_BASELINE_REFERENCE_AND_SAVED_INPUTS"],
                    **{k: prediction[k] for k in ("p_home", "p_away", "p_tie")},
                    "explanation": copy.deepcopy(prediction),
                    "promotion": "NOT_APPROVED",
                }
                if not _valid_prediction(record, game, kick, generated, allow_challenger=True):
                    states[name] = {
                        "status": "BLOCKED",
                        "reason": "INVALID_SHADOW_PROBABILITIES_OR_DEADLINE",
                    }
                    continue
                published = publish(model_root, record, deadline, clock)
                attempts[origin] = "SAVED" if published else "NOT_PUBLISHED_DEADLINE"
                publication_failed = publication_failed or not published
            records = [p for p in records if p["game_id"] != gid] + all_records(model_root, gid)
            all_models[name] = records
            saved = [p for p in records if p["game_id"] == gid]
            if publication_failed:
                states[name] = {
                    "status": "BLOCKED",
                    "reason": "SHADOW_PUBLICATION_DEADLINE_MISSED",
                    "latest_saved": max(saved, key=_prediction_time) if saved else None,
                }
            states[name]["publication_attempts"] = attempts
            states[name]["saved_revisions"] = len(saved)
            states[name]["history"] = [
                {
                    key: p.get(key)
                    for key in (
                        "revision_id",
                        "generated_at",
                        "published_at",
                        "origin",
                        "model_id",
                        "p_home",
                        "p_away",
                        "p_tie",
                    )
                }
                for p in saved
            ]
        observation = {
            "model_version": name,
            "artifact_id": artifact_ref,
            "status": "testing",
            "observation": "Evaluate prospective challenger probabilities against frozen production references.",
            "hypothesis": "Calibration or QB residual information improves matched proper probability scores.",
            "pregame_availability": "Prospective captures saved before kickoff; historical model-fitting provenance remains research-only.",
            "experiment_config": spec,
            "experiment_results": evaluation,
            "source_version": source_version,
            "decision": "NO_PROMOTION",
            "rollback": view["model"].get("model_version"),
            "experiment_started_at": spec["prospective_start"],
            "decision_rationale": "No production change is approved. Review the frozen historical evaluation and prospective matched scores; historical point-in-time provenance remains research-only.",
        }
        output["models"][name]["improvement_id"] = journal(
            root / "analysis", "shadow-improvements", observation
        )
    output["scorecards"] = score_shadow(view["games"], all_models, clock(), starts)
    output["report_id"] = journal(
        root / "analysis",
        "shadow-reports",
        {
            "models": output["models"],
            "scorecards": output["scorecards"],
            "decision": "NO_PROMOTION",
        },
    )
    for name, records in all_models.items():
        output["models"].setdefault(name, {})["saved_forecasts"] = len(records)
        output["models"][name]["game_coverage"] = len({p["game_id"] for p in records})
    return output
