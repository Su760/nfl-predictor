from datetime import UTC, datetime
from decimal import Decimal

import pytest
from pydantic import ValidationError

from nfl_predictor.contracts.enums import Origin, PredictionStatus, ProvenanceGrade
from nfl_predictor.contracts.forecasts import Prediction
from nfl_predictor.contracts.markets import MoneylineQuote, MoneylineSelection


def prediction(**overrides: object) -> Prediction:
    values: dict[str, object] = {
        "prediction_id": "pred-1",
        "origin_run_id": "run-1",
        "canonical_event_id": "2026_REG_01_NE_SEA",
        "event_version": 1,
        "origin": Origin.T72,
        "target_at_utc": datetime(2026, 9, 7, 0, 20, tzinfo=UTC),
        "decision_at_utc": datetime(2026, 9, 7, 0, 21, tzinfo=UTC),
        "model_lane": "football_only",
        "model_role": "champion",
        "model_artifact_id": "artifact-1",
        "calibrator_artifact_id": None,
        "feature_snapshot_id": "snapshot-1",
        "market_snapshot_id": None,
        "p_home": Decimal("0.600000"),
        "p_away": Decimal("0.395000"),
        "p_tie": Decimal("0.005000"),
        "predicted_winner": "home",
        "provenance_grade": ProvenanceGrade.A,
        "status": PredictionStatus.COMPLETE,
        "reason_codes": [],
        "code_sha": "b" * 40,
        "policy_versions": {"forecast": "v1"},
        "created_at_utc": datetime(2026, 9, 7, 0, 21, tzinfo=UTC),
    }
    values.update(overrides)
    return Prediction.model_validate(values)


def moneyline_quote(**overrides: object) -> MoneylineQuote:
    selection_home = MoneylineSelection(
        side="home",
        team="SEA",
        decimal_price=Decimal("1.8"),
        raw_pointer="$.home",
    )
    selection_away = MoneylineSelection(
        side="away",
        team="NE",
        decimal_price=Decimal("2.1"),
        raw_pointer="$.away",
    )
    values: dict[str, object] = {
        "quote_id": "quote-1",
        "capture_id": "capture-1",
        "canonical_event_id": "2026_REG_01_NE_SEA",
        "source_event_id": "provider-1",
        "book_key": "book",
        "book_name": "Book",
        "market_key": "h2h",
        "period": "full_game",
        "provider_last_update_at_utc": datetime(2026, 9, 7, 0, 20, tzinfo=UTC),
        "response_received_at_utc": datetime(2026, 9, 7, 0, 21, tzinfo=UTC),
        "capture_lag_seconds": 1,
        "provider_update_lag_seconds": 1,
        "selections": (selection_home, selection_away),
        "overtime_included": True,
        "tie_handling": "push",
        "market_semantics_version": "v1",
        "settlement_policy_url": "https://example.test/policy",
        "settlement_policy_version": "v1",
        "provenance_grade": ProvenanceGrade.A,
    }
    values.update(overrides)
    return MoneylineQuote.model_validate(values)


def test_prediction_probabilities_sum_to_one() -> None:
    with pytest.raises(ValidationError, match="sum to 1"):
        prediction(p_home=Decimal("0.61"))


def test_prediction_winner_follows_home_on_exact_tie() -> None:
    item = prediction(
        p_home=Decimal("0.4975"),
        p_away=Decimal("0.4975"),
        p_tie=Decimal("0.005"),
    )
    assert item.predicted_winner == "home"


def test_prediction_rejects_declared_winner_that_contradicts_probabilities() -> None:
    with pytest.raises(ValidationError, match="predicted_winner"):
        prediction(predicted_winner="away")


def test_prediction_rejects_naive_timestamp() -> None:
    with pytest.raises(ValidationError, match="timezone-aware UTC"):
        prediction(decision_at_utc=datetime(2026, 9, 7, 0, 21))  # noqa: DTZ001


def test_moneyline_quote_rejects_two_home_selections() -> None:
    home = MoneylineSelection(
        side="home",
        team="SEA",
        decimal_price=Decimal("1.8"),
        raw_pointer="$.home",
    )
    with pytest.raises(ValidationError, match="exactly home and away"):
        moneyline_quote(selections=(home, home))
