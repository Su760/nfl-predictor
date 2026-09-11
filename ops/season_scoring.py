from __future__ import annotations

import tomllib
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from nfl_predictor.evaluation.metrics import (
    binary_brier,
    binary_log_loss,
    conditional_non_tie_home,
    multiclass_brier,
    multinomial_log_loss,
    straight_up_accuracy,
)

ORIGINS = ("T72", "T60", "FINAL")
PRODUCTION_ROLES = {"champion", "fallback"}


def _instant(value: Any, field: str) -> datetime:
    if isinstance(value, datetime):
        result = value
    elif isinstance(value, str):
        result = datetime.fromisoformat(value)
    else:
        raise TypeError(f"{field} must be an ISO UTC timestamp")
    if result.tzinfo is None or result.utcoffset() != UTC.utcoffset(result):
        raise ValueError(f"{field} must be UTC")
    return result.astimezone(UTC)


def _optional_instant(value: Any, field: str) -> datetime | None:
    return None if value is None else _instant(value, field)


def _prediction_time(prediction: dict[str, Any]) -> datetime:
    return _instant(
        prediction.get("published_at", prediction.get("generated_at")), "prediction time"
    )


def _valid_prediction(
    prediction: dict[str, Any],
    game: dict[str, Any],
    kickoff: datetime,
    clock: datetime,
    *,
    allow_challenger: bool = False,
) -> bool:
    if prediction.get("status", "VALID") != "VALID":
        return False
    if prediction.get("role") == "challenger" and not allow_challenger:
        return False
    if prediction.get("role") is not None and prediction["role"] not in (
        PRODUCTION_ROLES | ({"challenger"} if allow_challenger else set())
    ):
        return False
    generated = _instant(prediction.get("generated_at"), "generated_at")
    published = _prediction_time(prediction)
    if (
        generated > clock
        or generated >= kickoff
        or published < generated
        or published >= kickoff
        or published > clock
    ):
        return False
    recorded_kickoff = _optional_instant(prediction.get("kickoff"), "prediction kickoff")
    if recorded_kickoff != kickoff:
        return False
    prediction_version = prediction.get("schedule_version")
    if prediction_version is not None and prediction_version != game.get("schedule_version"):
        return False
    probabilities = (prediction.get("p_home"), prediction.get("p_away"), prediction.get("p_tie"))
    try:
        multiclass_brier([probabilities], ["home"])
    except (TypeError, ValueError):
        return False
    return True


def _latest_predictions(
    game: dict[str, Any], kickoff: datetime | None, clock: datetime
) -> dict[str, dict[str, Any] | None]:
    slots = {"official": None, **{origin: None for origin in ORIGINS}}
    if kickoff is None:
        return slots
    valid = [
        prediction
        for prediction in game.get("predictions", [])
        if _valid_prediction(prediction, game, kickoff, clock)
    ]
    valid.sort(key=lambda row: (_prediction_time(row), str(row.get("revision_id", ""))))
    if valid:
        slots["official"] = valid[-1]
    for origin in ORIGINS:
        candidates = [row for row in valid if row.get("origin") == origin]
        if candidates:
            slots[origin] = candidates[-1]
    return slots


def _latest_by_model(
    game: dict[str, Any],
    kickoff: datetime | None,
    clock: datetime,
    origin: str | None = None,
) -> dict[str, dict[str, Any]]:
    if kickoff is None:
        return {}
    selected: dict[str, dict[str, Any]] = {}
    for prediction in game.get("predictions", []):
        if not _valid_prediction(prediction, game, kickoff, clock, allow_challenger=True) or (
            origin is not None and prediction.get("origin") != origin
        ):
            continue
        key = _model_key(prediction)
        current = selected.get(key)
        if current is None or (
            _prediction_time(prediction),
            str(prediction.get("revision_id", "")),
        ) > (_prediction_time(current), str(current.get("revision_id", ""))):
            selected[key] = prediction
    return selected


def _latest_outcome(game: dict[str, Any], clock: datetime) -> dict[str, Any] | None:
    observations = []
    for outcome in game.get("outcomes", []):
        observed = _instant(outcome.get("observed_at"), "observed_at")
        if observed <= clock:
            observations.append((observed, int(outcome.get("version", 0)), outcome))
    latest = max(observations, default=(None, None, None))[-1]
    return latest if latest is not None and latest.get("status") == "FINAL" else None


def _result(outcome: dict[str, Any] | None) -> str | None:
    if outcome is None:
        return None
    home, away = outcome["home_score"], outcome["away_score"]
    return "home" if home > away else "away" if away > home else "tie"


def _prediction_id(prediction: dict[str, Any] | None) -> str | None:
    if prediction is None:
        return None
    return prediction.get("revision_id") or prediction.get("prediction_id")


def _selected_metrics(prediction: dict[str, Any] | None, result: str | None) -> dict[str, Any]:
    probabilities = (
        None
        if prediction is None
        else tuple(float(prediction[key]) for key in ("p_home", "p_away", "p_tie"))
    )
    values: dict[str, Any] = {
        "prediction_id": _prediction_id(prediction),
        "probabilities": probabilities,
        "multinomial_log_loss": None,
        "multiclass_brier": None,
        "conditional_non_tie_log_loss": None,
        "conditional_non_tie_brier": None,
        "winner_correct": None,
    }
    if probabilities is None or result is None:
        return values
    values["multinomial_log_loss"] = multinomial_log_loss([probabilities], [result])
    values["multiclass_brier"] = multiclass_brier([probabilities], [result])
    if result != "tie":
        conditional = conditional_non_tie_home([probabilities])
        label = [int(result == "home")]
        values["conditional_non_tie_log_loss"] = binary_log_loss(conditional, label)
        values["conditional_non_tie_brier"] = binary_brier(conditional, label)
        values["winner_correct"] = (probabilities[0] >= probabilities[1]) == (result == "home")
    return values


def _score_rows(rows: list[dict[str, Any]]) -> dict[str, Any]:
    cfg = tomllib.loads((Path(__file__).parents[1] / "configs/season_live.toml").read_text())
    bin_count = cfg["calibration_bin_count"]
    settled = [
        row for row in rows if row["result"] is not None and row["probabilities"] is not None
    ]
    probabilities = [row["probabilities"] for row in settled]
    results = [row["result"] for row in settled]
    non_ties = [index for index, result in enumerate(results) if result != "tie"]
    output: dict[str, Any] = {
        "games": len(rows),
        "forecasted": sum(row["prediction_id"] is not None for row in rows),
        "settled": len(settled),
        "unresolved": sum(
            row["prediction_id"] is not None and row["result"] is None for row in rows
        ),
        "pending_coverage": sum(row["coverage"] == "PENDING" for row in rows),
        "missed_coverage": sum(row["coverage"] == "MISSED" for row in rows),
        "ties": len(results) - len(non_ties),
        "winner_accuracy_denominator": len(non_ties),
        "correct": sum(row.get("winner_correct") is True for row in settled),
        "missed": sum(row.get("winner_correct") is False for row in settled),
        "pending": sum(row["prediction_id"] is not None and row["result"] is None for row in rows),
        "not_yet_forecast": sum(row["prediction_id"] is None for row in rows),
    }
    if probabilities:
        output["multinomial_log_loss"] = multinomial_log_loss(probabilities, results)
        output["multiclass_brier"] = multiclass_brier(probabilities, results)
    else:
        output["multinomial_log_loss"] = output["multiclass_brier"] = None
    if non_ties:
        non_tie_probabilities = [probabilities[index] for index in non_ties]
        labels = [int(results[index] == "home") for index in non_ties]
        conditional = conditional_non_tie_home(non_tie_probabilities)
        output.update(
            conditional_non_tie_log_loss=binary_log_loss(conditional, labels),
            conditional_non_tie_brier=binary_brier(conditional, labels),
            straight_up_accuracy=straight_up_accuracy(probabilities, results),
        )
    else:
        output.update(
            conditional_non_tie_log_loss=None,
            conditional_non_tie_brier=None,
            straight_up_accuracy=None,
        )
    bins: dict[int, list[tuple[float, bool]]] = defaultdict(list)
    for row in settled:
        confidence = max(row["probabilities"])
        bins[min(bin_count - 1, int(confidence * bin_count))].append(
            (
                confidence,
                row["probabilities"].index(max(row["probabilities"]))
                == ("home", "away", "tie").index(row["result"]),
            )
        )
    output["calibration_bins"] = [
        {
            "lower": index / bin_count,
            "upper": (index + 1) / bin_count,
            "count": len(values),
            "mean_confidence": sum(value[0] for value in values) / len(values),
            "accuracy": sum(value[1] for value in values) / len(values),
        }
        for index, values in sorted(bins.items())
    ]
    return output


def _input_issues(prediction: dict[str, Any] | None, status: str) -> list[str]:
    if prediction is None:
        return []
    inputs = prediction.get("inputs", {})
    if isinstance(inputs, dict):
        return sorted(
            str(name)
            for name, value in inputs.items()
            if value is None or (isinstance(value, dict) and value.get("status") == status)
            if status == "MISSING" or value is not None
        )
    if isinstance(inputs, list):
        return sorted(
            str(item.get("name", item.get("source", "unknown")))
            for item in inputs
            if isinstance(item, dict) and item.get("status") == status
        )
    return []


def weekly_error_analysis(per_game: list[dict[str, Any]]) -> dict[str, Any]:
    cfg = tomllib.loads((Path(__file__).parents[1] / "configs/season_live.toml").read_text())
    threshold = cfg["confident_review_threshold"]
    settled = [row for row in per_game if row["result"] is not None and row["prediction_id"]]
    wins, misses = [], []
    for row in settled:
        probabilities = row["probabilities"]
        predicted = ("home", "away", "tie")[probabilities.index(max(probabilities))]
        target = wins if predicted == row["result"] else misses
        target.append(
            {"game_id": row["game_id"], "confidence": max(probabilities), "result": row["result"]}
        )
    return {
        "wins": wins,
        "misses": misses,
        "review_threshold": threshold,
        "confident_wins": sorted(
            (row for row in wins if row["confidence"] >= threshold),
            key=lambda row: -row["confidence"],
        ),
        "confident_misses": sorted(
            (row for row in misses if row["confidence"] >= threshold),
            key=lambda row: -row["confidence"],
        ),
        "capture_staleness": [
            {"game_id": row["game_id"], "inputs": row.get("stale_inputs", [])}
            for row in per_game
            if row.get("capture_stale") or row.get("stale_inputs")
        ],
        "missing_inputs": [
            {"game_id": row["game_id"], "inputs": row["missing_inputs"]}
            for row in per_game
            if row.get("missing_inputs")
        ],
        "missing_forecasts": [row["game_id"] for row in per_game if row["coverage"] == "MISSED"],
        "feature_scope": "pregame_only",
        "causal_claims": [],
        "promotion_action": "NONE",
    }


def _model_key(prediction: dict[str, Any]) -> str:
    return str(
        prediction.get("model_version")
        or prediction.get("model_id")
        or prediction.get("model_label")
        or "unknown"
    )


def score_season(
    games: list[dict[str, Any]], clock: datetime, policy: dict[str, Any]
) -> dict[str, Any]:
    now = _instant(clock, "clock")
    effective_at = _instant(policy.get("effective_at"), "policy effective_at")
    per_game: list[dict[str, Any]] = []
    horizon_rows: dict[str, list[dict[str, Any]]] = {origin: [] for origin in ORIGINS}
    model_rows: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for game in games:
        kickoff = _optional_instant(game.get("kickoff"), "kickoff")
        selections = _latest_predictions(game, kickoff, now)
        outcome = _latest_outcome(game, now)
        result = _result(outcome)
        prediction = selections["official"]
        if kickoff is None or kickoff > now:
            coverage = "PENDING"
        elif prediction is None:
            coverage = "MISSED"
        elif outcome is None:
            coverage = "PENDING"
        else:
            coverage = "SETTLED"
        probabilities = (
            None
            if prediction is None
            else tuple(float(prediction[key]) for key in ("p_home", "p_away", "p_tie"))
        )
        row = {
            "game_id": game["game_id"],
            "season": game["season"],
            "week": game["week"],
            "home": game["home"],
            "away": game["away"],
            "kickoff": game.get("kickoff"),
            "coverage": coverage,
            "retrospective_policy": kickoff is not None and kickoff <= effective_at,
            "prediction_id": _prediction_id(prediction),
            "selected_prediction_ids": {
                key: _prediction_id(value) for key, value in selections.items()
            },
            "outcome_version": None if outcome is None else outcome.get("version"),
            "result": result,
            "probabilities": probabilities,
            "model": None if prediction is None else _model_key(prediction),
            "capture_stale": bool(prediction and prediction.get("capture_stale", False)),
            "stale_inputs": _input_issues(prediction, "STALE"),
            "missing_inputs": _input_issues(prediction, "MISSING"),
        }
        if prediction is not None and result is not None:
            row["multinomial_log_loss"] = multinomial_log_loss([probabilities], [result])
            row["multiclass_brier"] = multiclass_brier([probabilities], [result])
            if result == "tie":
                row["conditional_non_tie_log_loss"] = None
                row["conditional_non_tie_brier"] = None
                row["winner_correct"] = None
            else:
                conditional = conditional_non_tie_home([probabilities])
                label = [int(result == "home")]
                row["conditional_non_tie_log_loss"] = binary_log_loss(conditional, label)
                row["conditional_non_tie_brier"] = binary_brier(conditional, label)
                row["winner_correct"] = (probabilities[0] >= probabilities[1]) == (result == "home")
        else:
            row["multinomial_log_loss"] = row["multiclass_brier"] = None
            row["conditional_non_tie_log_loss"] = row["conditional_non_tie_brier"] = None
            row["winner_correct"] = None
        per_game.append(row)
        for model, model_prediction in _latest_by_model(game, kickoff, now).items():
            model_probabilities = tuple(
                float(model_prediction[key]) for key in ("p_home", "p_away", "p_tie")
            )
            model_rows[model].append(
                {
                    **row,
                    **_selected_metrics(model_prediction, result),
                    "probabilities": model_probabilities,
                }
            )
        for origin in ORIGINS:
            selected = selections[origin]
            if kickoff is None or kickoff > now:
                horizon_coverage = "PENDING"
            elif selected is None:
                horizon_coverage = "MISSED"
            elif result is None:
                horizon_coverage = "PENDING"
            else:
                horizon_coverage = "SETTLED"
            horizon_rows[origin].append(
                {
                    **row,
                    "coverage": horizon_coverage,
                    **_selected_metrics(selected, result),
                }
            )

    weekly = {
        str(week): _score_rows(rows) for week, rows in sorted(_group(per_game, "week").items())
    }
    horizons = {origin: _score_rows(rows) for origin, rows in horizon_rows.items()}
    weekly_horizons = {
        origin: {
            str(week): _score_rows(week_rows)
            for week, week_rows in sorted(_group(rows, "week").items())
        }
        for origin, rows in horizon_rows.items()
    }
    models = {model: _score_rows(rows) for model, rows in sorted(model_rows.items())}
    comparisons = []
    for horizon in ("LATEST", *ORIGINS):
        by_model: dict[str, dict[str, dict[str, Any]]] = defaultdict(dict)
        for game, official_row in zip(games, per_game, strict=True):
            kickoff = _optional_instant(game.get("kickoff"), "kickoff")
            selected_models = _latest_by_model(
                game, kickoff, now, None if horizon == "LATEST" else horizon
            )
            for model, model_prediction in selected_models.items():
                by_model[model][game["game_id"]] = {
                    **official_row,
                    **_selected_metrics(model_prediction, official_row["result"]),
                }
        elo_models = [name for name in by_model if "elo" in name.lower()]
        for model in sorted(name for name in by_model if "elo" not in name.lower()):
            for elo_model in sorted(elo_models):
                game_ids = sorted(
                    game_id
                    for game_id in set(by_model[model]) & set(by_model[elo_model])
                    if by_model[model][game_id]["result"] is not None
                )
                if not game_ids:
                    continue
                model_matched = [by_model[model][game_id] for game_id in game_ids]
                elo_matched = [by_model[elo_model][game_id] for game_id in game_ids]
                comparisons.append(
                    {
                        "horizon": horizon,
                        "model": model,
                        "baseline": elo_model,
                        "game_ids": game_ids,
                        "model_scores": _score_rows(model_matched),
                        "elo_scores": _score_rows(elo_matched),
                    }
                )
    return {
        "as_of": now.isoformat().replace("+00:00", "Z"),
        "policy": {**policy, "effective_at": effective_at.isoformat().replace("+00:00", "Z")},
        "summary": {"season": _score_rows(per_game), "weekly": weekly},
        "horizons": horizons,
        "weekly_horizons": weekly_horizons,
        "model_breakdowns": models,
        "matched_elo_comparisons": comparisons,
        "games": per_game,
        "weekly_error_analysis": {
            week: weekly_error_analysis(rows) for week, rows in _group(per_game, "week").items()
        },
    }


def _group(rows: list[dict[str, Any]], key: str) -> dict[Any, list[dict[str, Any]]]:
    grouped: dict[Any, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[row[key]].append(row)
    return grouped
