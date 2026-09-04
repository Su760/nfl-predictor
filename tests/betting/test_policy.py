from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from threading import Lock

import pytest
from pydantic import ValidationError

from nfl_predictor.betting.policy import (
    BookSettlementContract,
    CalibrationBinEvidence,
    CandidateContext,
    CandidatePolicy,
    EventMatch,
    InMemoryDecisionRepository,
    OddsPolicy,
    OfficialEventState,
    TrustedEventSourceMatch,
    load_odds_policy,
)
from nfl_predictor.contracts.betting import BettingDecision
from nfl_predictor.contracts.enums import Origin, ProvenanceGrade
from nfl_predictor.contracts.events import EventVersion
from nfl_predictor.contracts.forecasts import Prediction
from nfl_predictor.contracts.lineage import CaptureManifest
from nfl_predictor.contracts.markets import (
    DisplayedQuoteCandidate,
    MoneylineQuote,
    MoneylineSelection,
)
from nfl_predictor.identity.teams import CANONICAL_TEAM_ALIASES

ROOT = Path(__file__).resolve().parents[2]
NOW = datetime(2026, 9, 10, 18, 0, tzinfo=UTC)

NFL_PROVIDER_TEAM_NAMES = (
    {"provider_name": "Arizona Cardinals", "canonical_team": "ARI"},
    {"provider_name": "Atlanta Falcons", "canonical_team": "ATL"},
    {"provider_name": "Baltimore Ravens", "canonical_team": "BAL"},
    {"provider_name": "Buffalo Bills", "canonical_team": "BUF"},
    {"provider_name": "Carolina Panthers", "canonical_team": "CAR"},
    {"provider_name": "Chicago Bears", "canonical_team": "CHI"},
    {"provider_name": "Cincinnati Bengals", "canonical_team": "CIN"},
    {"provider_name": "Cleveland Browns", "canonical_team": "CLE"},
    {"provider_name": "Dallas Cowboys", "canonical_team": "DAL"},
    {"provider_name": "Denver Broncos", "canonical_team": "DEN"},
    {"provider_name": "Detroit Lions", "canonical_team": "DET"},
    {"provider_name": "Green Bay Packers", "canonical_team": "GB"},
    {"provider_name": "Houston Texans", "canonical_team": "HOU"},
    {"provider_name": "Indianapolis Colts", "canonical_team": "IND"},
    {"provider_name": "Jacksonville Jaguars", "canonical_team": "JAX"},
    {"provider_name": "Kansas City Chiefs", "canonical_team": "KC"},
    {"provider_name": "Las Vegas Raiders", "canonical_team": "LV"},
    {"provider_name": "Los Angeles Chargers", "canonical_team": "LAC"},
    {"provider_name": "Los Angeles Rams", "canonical_team": "LAR"},
    {"provider_name": "Miami Dolphins", "canonical_team": "MIA"},
    {"provider_name": "Minnesota Vikings", "canonical_team": "MIN"},
    {"provider_name": "New England Patriots", "canonical_team": "NE"},
    {"provider_name": "New Orleans Saints", "canonical_team": "NO"},
    {"provider_name": "New York Giants", "canonical_team": "NYG"},
    {"provider_name": "New York Jets", "canonical_team": "NYJ"},
    {"provider_name": "Philadelphia Eagles", "canonical_team": "PHI"},
    {"provider_name": "Pittsburgh Steelers", "canonical_team": "PIT"},
    {"provider_name": "San Francisco 49ers", "canonical_team": "SF"},
    {"provider_name": "Seattle Seahawks", "canonical_team": "SEA"},
    {"provider_name": "Tampa Bay Buccaneers", "canonical_team": "TB"},
    {"provider_name": "Tennessee Titans", "canonical_team": "TEN"},
    {"provider_name": "Washington Commanders", "canonical_team": "WAS"},
)


def _contract(book: str = "book-a") -> BookSettlementContract:
    return BookSettlementContract(
        book_key=book,
        market_semantics_version="nfl-h2h-v1",
        settlement_policy_url="https://example.test/rules",
        settlement_policy_version="v1",
        overtime_included=True,
        tie_handling="push",
        verified_on="2026-08-28",
    )


def _policy(**overrides: object) -> OddsPolicy:
    values: dict[str, object] = {
        "book_allowlist": ("book-a", "book-b"),
        "book_settlement_contracts": (_contract("book-a"), _contract("book-b")),
    }
    if overrides.get("capture_enabled") is True:
        values["provider_team_names"] = NFL_PROVIDER_TEAM_NAMES
    values.update(overrides)
    return OddsPolicy.model_validate(values)


def _event_version(*, kickoff: datetime | None = None, version: int = 1) -> EventVersion:
    return EventVersion(
        canonical_event_id="event-1",
        event_version=version,
        source_event_ids={"the_odds_api": "provider-1"},
        season=2026,
        season_type="REG",
        week=1,
        home_team="CHI",
        away_team="GB",
        kickoff_at_utc=kickoff or NOW + timedelta(hours=1),
        neutral_site=False,
        observed_at_utc=NOW - timedelta(days=1),
        available_at_utc=NOW - timedelta(days=1),
        captured_at_utc=NOW - timedelta(days=1),
        raw_payload_sha256="a" * 64,
    )


def _event(
    *,
    kickoff: datetime | None = None,
    version: int = 1,
    status: str = "pregame",
) -> OfficialEventState:
    return OfficialEventState(
        event=_event_version(kickoff=kickoff, version=version),
        status=status,  # type: ignore[arg-type]
    )


def _prediction(**changes: object) -> Prediction:
    values: dict[str, object] = {
        "prediction_id": "prediction-1",
        "origin_run_id": "run-1",
        "canonical_event_id": "event-1",
        "event_version": 1,
        "origin": Origin.T60,
        "target_at_utc": NOW,
        "decision_at_utc": NOW,
        "model_lane": "football_only",
        "model_role": "champion",
        "model_artifact_id": "model-1",
        "calibrator_artifact_id": None,
        "feature_snapshot_id": "snapshot-1",
        "market_snapshot_id": None,
        "p_home": Decimal("0.55"),
        "p_away": Decimal("0.43"),
        "p_tie": Decimal("0.02"),
        "predicted_winner": "home",
        "provenance_grade": ProvenanceGrade.A,
        "status": "COMPLETE",
        "reason_codes": [],
        "code_sha": "1" * 40,
        "policy_versions": {"forecast": "forecast-v1"},
        "created_at_utc": NOW,
    }
    values.update(changes)
    return Prediction.model_validate(values)


def _quote(
    book: str,
    home_price: str,
    away_price: str,
    *,
    event_id: str = "event-1",
    capture_id: str = "capture-current",
    response_at: datetime = NOW,
    quote_suffix: str = "",
) -> MoneylineQuote:
    return MoneylineQuote(
        quote_id=f"quote-{book}{quote_suffix}",
        capture_id=capture_id,
        canonical_event_id=event_id,
        source_event_id="provider-1",
        book_key=book,
        book_name=book,
        market_key="h2h",
        period="full_game",
        provider_last_update_at_utc=response_at - timedelta(minutes=1),
        response_received_at_utc=response_at,
        capture_lag_seconds=1,
        provider_update_lag_seconds=60,
        selections=(
            MoneylineSelection(
                side="home", team="CHI", decimal_price=Decimal(home_price), raw_pointer="$[0]"
            ),
            MoneylineSelection(
                side="away", team="GB", decimal_price=Decimal(away_price), raw_pointer="$[1]"
            ),
        ),
        overtime_included=True,
        tie_handling="push",
        market_semantics_version="nfl-h2h-v1",
        settlement_policy_url="https://example.test/rules",
        settlement_policy_version="v1",
        provenance_grade=ProvenanceGrade.A,
    )


def _valid_bin() -> CalibrationBinEvidence:
    return CalibrationBinEvidence(
        one_sided_upper_absolute_error=Decimal("0.03"),
        sample_count=50,
        confidence=0.95,
        cutoff_at_utc=NOW - timedelta(seconds=1),
        origin=Origin.T60,
        binning_method="equal_count",
        out_of_sample=True,
        frozen=True,
        evidence_id="calibration-evidence-1",
    )


class _Bins:
    def __init__(self, found: bool = True) -> None:
        self.found = found

    def lookup(self, **kwargs):
        assert kwargs["strictly_before_utc"] == NOW
        assert kwargs["minimum_games"] == 50
        return _valid_bin() if self.found else None


class _Ids:
    def candidate(self, **values) -> DisplayedQuoteCandidate:
        prediction = values["prediction"]
        quote = values["quote"]
        return DisplayedQuoteCandidate(
            candidate_id=f"candidate-{values['side']}",
            prediction_id=prediction.prediction_id,
            quote_id=quote.quote_id,
            origin=prediction.origin,
            side=values["side"],
            decision_at_utc=prediction.decision_at_utc,
            decimal_price=values["price"],
            raw_p_win=values["raw_p_win"],
            buffered_p_win=values["buffered_p_win"],
            p_loss=values["raw_p_loss"],
            buffered_p_loss=values["buffered_p_loss"],
            p_push=values["p_push"],
            displayed_ev_per_unit=values["ev"],
            quarter_kelly_fraction=values["kelly"],
            policy_version=values["policy_version"],
            decision_status="candidate",
            reason_codes=[],
        )

    def decision(self, **values) -> BettingDecision:
        prediction = values["prediction"]
        return BettingDecision(
            decision_id=f"decision-{values['side']}",
            prediction_id=prediction.prediction_id,
            canonical_event_id=prediction.canonical_event_id,
            origin=prediction.origin,
            side=values["side"],
            quote_id=values["quote_id"] if values["candidate_id"] else None,
            candidate_id=values["candidate_id"],
            evaluated_at_utc=prediction.decision_at_utc,
            policy_version=values["policy_version"],
            status=values["status"],
            reason_codes=values["reason_codes"],
        )


def _evaluate(
    *,
    prediction: Prediction | None = None,
    event: OfficialEventState | None = None,
    quotes=None,
    bins: _Bins | None = None,
    repository: InMemoryDecisionRepository | None = None,
    ids: _Ids | None = None,
    context: CandidateContext | None = None,
):
    policy = _policy()
    chosen_event = event or _event()
    source_match = policy.trusted_source_match(EventMatch(chosen_event.event, "provider-1", None))
    return CandidatePolicy(
        policy,
        policy,
        ids or _Ids(),
        context
        or CandidateContext(
            expected_champion_artifact_id="model-1",
            required_prediction_policy_versions={"forecast": "forecast-v1"},
        ),
        repository or InMemoryDecisionRepository(),
    ).evaluate(
        prediction or _prediction(),
        chosen_event,
        quotes or (_quote("book-a", "2.05", "1.80"), _quote("book-b", "2.10", "1.75")),
        bins or _Bins(),
        source_match,
    )


def test_default_policy_loads_frozen_capture_disabled_values() -> None:
    policy = load_odds_policy(ROOT / "configs" / "odds_policy_v1.toml")

    assert policy.policy_version == "odds-v1"
    assert policy.capture_enabled is False
    assert policy.monthly_hard_stop == 400
    assert policy.provider_source_key == "the_odds_api"
    assert policy.book_allowlist == ()
    assert len(policy.provider_team_names) == 32
    assert {item.canonical_team for item in policy.provider_team_names} == set(
        CANONICAL_TEAM_ALIASES.values()
    )
    with pytest.raises(ValidationError):
        policy.monthly_hard_stop = 401  # type: ignore[misc]


@pytest.mark.parametrize(
    "overrides",
    [
        {"capture_enabled": True, "book_allowlist": (), "book_settlement_contracts": ()},
        {
            "capture_enabled": True,
            "book_allowlist": ("book-a",),
            "book_settlement_contracts": (),
            "worst_case_monthly_credits": 100,
        },
        {
            "capture_enabled": True,
            "book_allowlist": ("book-a",),
            "book_settlement_contracts": (_contract(),),
            "worst_case_monthly_credits": 401,
        },
    ],
)
def test_enabling_capture_fails_closed_without_books_contracts_and_budget(overrides) -> None:
    with pytest.raises(ValidationError):
        _policy(**overrides)


def test_complete_verified_capture_policy_may_be_enabled_under_hard_stop() -> None:
    policy = _policy(capture_enabled=True, worst_case_monthly_credits=400)
    assert policy.capture_enabled is True


@pytest.mark.parametrize(
    "provider_team_names",
    [
        NFL_PROVIDER_TEAM_NAMES[:-1],
        (
            *NFL_PROVIDER_TEAM_NAMES[:-1],
            {"provider_name": "Washington Commanders", "canonical_team": "ARI"},
        ),
    ],
)
def test_enabled_capture_requires_complete_unique_provider_team_coverage(
    provider_team_names: tuple[dict[str, str], ...],
) -> None:
    with pytest.raises(ValidationError, match="provider team"):
        _policy(
            capture_enabled=True,
            worst_case_monthly_credits=100,
            provider_team_names=provider_team_names,
        )


def test_absolute_capture_hard_stop_cannot_be_configured_above_400() -> None:
    with pytest.raises(ValidationError):
        _policy(monthly_hard_stop=401)


def test_enabled_capture_requires_the_frozen_h2h_market() -> None:
    with pytest.raises(ValidationError):
        _policy(capture_enabled=True, markets=(), worst_case_monthly_credits=100)


def test_candidate_uses_best_price_and_moves_calibration_error_from_win_to_loss() -> None:
    home, away = _evaluate()

    assert home.decision.status == "candidate"
    assert home.candidate is not None
    assert home.candidate.quote_id == "quote-book-b"
    assert home.candidate.raw_p_win == Decimal("0.55")
    assert home.candidate.buffered_p_win == Decimal("0.52")
    assert home.candidate.buffered_p_loss == Decimal("0.46")
    assert home.candidate.displayed_ev_per_unit == Decimal("0.112")
    assert away.decision.status == "rejected"
    assert "BUFFERED_EV_NOT_POSITIVE" in away.decision.reason_codes


def test_aliasless_first_capture_source_match_is_usable_and_competing_ids_are_quarantined() -> None:
    policy = _policy(provider_team_names=NFL_PROVIDER_TEAM_NAMES)
    aliasless_event = _event_version().model_copy(
        update={"source_event_ids": {"nflverse": "2026_01_GB_CHI"}}
    )
    event_state = OfficialEventState(aliasless_event, "pregame")
    provider_event = {
        "id": "provider-1",
        "commence_time": aliasless_event.kickoff_at_utc.isoformat(),
        "home_team": "Chicago Bears",
        "away_team": "Green Bay Packers",
    }
    source_match = policy.trusted_source_match(
        policy.match_event_result(provider_event, (aliasless_event,))
    )
    manifest = CaptureManifest(
        capture_id="capture-bootstrap",
        run_id="run-bootstrap",
        source="the_odds_api",
        request_fingerprint="request-bootstrap",
        request_started_at_utc=NOW - timedelta(seconds=2),
        response_received_at_utc=NOW,
        http_status=200,
        raw_path="raw/bootstrap.json",
        raw_payload_sha256="b" * 64,
        response_headers_allowlisted={},
        code_sha="1" * 40,
        dependency_lock_sha256="2" * 64,
        schema_version="raw-v1",
    )
    normalized_quote = policy.parse_verified_full_game_h2h(
        bookmaker={
            "key": "book-a",
            "title": "Book A",
            "last_update": (NOW - timedelta(minutes=1)).isoformat(),
            "markets": [
                {
                    "key": "h2h",
                    "outcomes": [
                        {"name": "Chicago Bears", "price": 2.05},
                        {"name": "Green Bay Packers", "price": 1.80},
                    ],
                }
            ],
        },
        event=aliasless_event,
        source_event_id=source_match.source_event_id,
        manifest=manifest,
        raw_pointer="$[0].bookmakers[0]",
    )
    assert isinstance(normalized_quote, MoneylineQuote)
    candidate_policy = CandidatePolicy(
        policy,
        policy,
        _Ids(),
        CandidateContext(
            expected_champion_artifact_id="model-1",
            required_prediction_policy_versions={"forecast": "forecast-v1"},
        ),
        InMemoryDecisionRepository(),
    )
    first_capture = (normalized_quote,)
    competing = (
        normalized_quote,
        _quote("book-b", "2.10", "1.75").model_copy(
            update={
                "capture_id": normalized_quote.capture_id,
                "source_event_id": "provider-competing",
            }
        ),
    )

    accepted = candidate_policy.evaluate(
        _prediction(), event_state, first_capture, _Bins(), source_match
    )
    rejected = CandidatePolicy(
        policy,
        policy,
        _Ids(),
        CandidateContext(
            expected_champion_artifact_id="model-1",
            required_prediction_policy_versions={"forecast": "forecast-v1"},
        ),
        InMemoryDecisionRepository(),
    ).evaluate(_prediction(), event_state, competing, _Bins(), source_match)

    assert accepted[0].candidate is not None
    assert all("COMPETING_PROVIDER_EVENT_IDS" in item.decision.reason_codes for item in rejected)


def test_candidate_rejects_mismatched_or_untyped_trusted_source_context() -> None:
    policy = _policy()
    with pytest.raises(ValueError, match="established provider alias"):
        policy.trusted_source_match(EventMatch(_event_version(), "provider-other", None))
    candidate_policy = CandidatePolicy(
        policy,
        policy,
        _Ids(),
        CandidateContext(
            expected_champion_artifact_id="model-1",
            required_prediction_policy_versions={"forecast": "forecast-v1"},
        ),
        InMemoryDecisionRepository(),
    )
    wrong_source = TrustedEventSourceMatch(
        canonical_event_id="event-1",
        event_version=1,
        provider_source_key="the_odds_api",
        source_event_id="provider-other",
    )

    rejected = candidate_policy.evaluate(
        _prediction(),
        _event(),
        (_quote("book-a", "2.05", "1.80"),),
        _Bins(),
        wrong_source,
    )

    assert all("PROVIDER_EVENT_ID_MISMATCH" in item.decision.reason_codes for item in rejected)
    with pytest.raises(TypeError, match="TrustedEventSourceMatch"):
        CandidatePolicy(
            policy,
            policy,
            _Ids(),
            CandidateContext(
                expected_champion_artifact_id="model-1",
                required_prediction_policy_versions={"forecast": "forecast-v1"},
            ),
            InMemoryDecisionRepository(),
        ).evaluate(
            _prediction(),
            _event(),
            (_quote("book-a", "2.05", "1.80"),),
            _Bins(),
            object(),  # type: ignore[arg-type]
        )


@pytest.mark.parametrize(
    ("prediction", "event", "quotes", "bins", "reason"),
    [
        (
            _prediction(provenance_grade=ProvenanceGrade.B),
            _event(),
            None,
            _Bins(),
            "PREDICTION_NOT_PROSPECTIVE_GRADE_A",
        ),
        (
            _prediction(model_role="fallback"),
            _event(),
            None,
            _Bins(),
            "BETTING_REQUIRES_FROZEN_CHAMPION",
        ),
        (
            _prediction(),
            _event(status="started"),
            None,
            _Bins(),
            "EVENT_NOT_OFFICIAL_PREGAME",
        ),
        (
            _prediction(),
            _event(version=2),
            None,
            _Bins(),
            "EVENT_VERSION_MISMATCH",
        ),
        (
            _prediction(),
            _event(),
            (_quote("book-a", "2.05", "1.80", event_id="other"),),
            _Bins(),
            "NO_ELIGIBLE_DISPLAYED_QUOTE",
        ),
        (
            _prediction(),
            _event(),
            None,
            _Bins(False),
            "NO_QUALIFYING_CALIBRATION_BIN",
        ),
    ],
)
def test_candidate_policy_fail_closed_branches(
    prediction, event, quotes, bins, reason: str
) -> None:
    result = _evaluate(
        prediction=prediction,
        event=event,
        quotes=quotes,
        bins=bins,
    )

    assert any(reason in evaluation.decision.reason_codes for evaluation in result)
    assert all(
        evaluation.candidate is None
        for evaluation in result
        if reason in evaluation.decision.reason_codes
    )


@pytest.mark.parametrize("status", ["cancelled", "postponed", "suspended", "started"])
def test_every_non_pregame_official_state_rejects_candidate_creation(status: str) -> None:
    result = _evaluate(event=_event(status=status))
    assert all("EVENT_NOT_OFFICIAL_PREGAME" in item.decision.reason_codes for item in result)


def test_candidate_requires_frozen_champion_artifact_and_prediction_policy_versions() -> None:
    artifact = _evaluate(prediction=_prediction(model_artifact_id="other-model"))
    policy = _evaluate(prediction=_prediction(policy_versions={"forecast": "wrong"}))

    assert all("CHAMPION_ARTIFACT_MISMATCH" in item.decision.reason_codes for item in artifact)
    assert all(
        "PREDICTION_POLICY_VERSION_MISMATCH" in item.decision.reason_codes for item in policy
    )


def test_candidate_and_comparator_share_newest_unambiguous_capture_rule() -> None:
    old_best = _quote(
        "book-a",
        "5.00",
        "1.10",
        capture_id="capture-old",
        response_at=NOW - timedelta(minutes=5),
        quote_suffix="-old",
    )
    current = _quote(
        "book-a",
        "2.05",
        "1.80",
        capture_id="capture-current",
        quote_suffix="-current",
    )

    home, _ = _evaluate(quotes=(old_best, current))

    assert home.candidate is not None
    assert home.candidate.quote_id == "quote-book-a-current"


def test_candidate_rejects_outside_window_and_ambiguous_latest_capture() -> None:
    outside = _quote(
        "book-a",
        "2.05",
        "1.80",
        capture_id="outside",
        response_at=NOW - timedelta(minutes=11),
    )
    ambiguous = (
        _quote("book-a", "2.05", "1.80", capture_id="one"),
        _quote("book-b", "2.10", "1.75", capture_id="two"),
    )

    outside_result = _evaluate(quotes=(outside,))
    ambiguous_result = _evaluate(quotes=ambiguous)

    assert all(
        "NO_ELIGIBLE_DISPLAYED_QUOTE" in item.decision.reason_codes for item in outside_result
    )
    assert all(
        "AMBIGUOUS_LATEST_CAPTURE" in item.decision.reason_codes for item in ambiguous_result
    )


class _InvalidBin:
    def __init__(
        self,
        error: Decimal = Decimal("0.03"),
        sample_count: int = 50,
        confidence: float = 0.95,
        cutoff_at_utc: datetime = NOW - timedelta(seconds=1),
        origin: Origin = Origin.T60,
        binning_method: str = "equal_count",
        out_of_sample: bool = True,
        frozen: bool = True,
        evidence_id: str = "calibration-evidence-1",
    ) -> None:
        self.one_sided_upper_absolute_error = error
        self.sample_count = sample_count
        self.confidence = confidence
        self.cutoff_at_utc = cutoff_at_utc
        self.origin = origin
        self.binning_method = binning_method
        self.out_of_sample = out_of_sample
        self.frozen = frozen
        self.evidence_id = evidence_id


class _BareErrorOnlyBin:
    one_sided_upper_absolute_error = Decimal("0.03")


class _ReturningBins(_Bins):
    def __init__(self, value: object) -> None:
        super().__init__()
        self.value = value

    def lookup(self, **kwargs):
        super().lookup(**kwargs)
        return self.value


@pytest.mark.parametrize(
    "calibration_bin",
    [
        _BareErrorOnlyBin(),
        _InvalidBin(error=Decimal("NaN")),
        _InvalidBin(error=Decimal("-0.01")),
        _InvalidBin(error=Decimal("1.01")),
        _InvalidBin(sample_count=49),
        _InvalidBin(confidence=0.90),
        _InvalidBin(cutoff_at_utc=NOW),
        _InvalidBin(cutoff_at_utc=NOW + timedelta(seconds=1)),
        _InvalidBin(origin=Origin.T72),
        _InvalidBin(binning_method="fixed_width"),
        _InvalidBin(out_of_sample=False),
        _InvalidBin(frozen=False),
        _InvalidBin(evidence_id=""),
    ],
)
def test_candidate_rejects_invalid_calibration_evidence(calibration_bin: object) -> None:
    result = _evaluate(bins=_ReturningBins(calibration_bin))
    assert all("INVALID_CALIBRATION_EVIDENCE" in item.decision.reason_codes for item in result)


def test_complete_equal_count_frozen_out_of_sample_calibration_record_is_accepted() -> None:
    home, _ = _evaluate(bins=_ReturningBins(_valid_bin()))

    assert home.decision.status == "candidate"
    assert home.candidate is not None


class _CountingIds(_Ids):
    def __init__(self) -> None:
        self.lock = Lock()
        self.candidate_calls = 0
        self.decision_calls = 0

    def candidate(self, **values) -> DisplayedQuoteCandidate:
        with self.lock:
            self.candidate_calls += 1
        return super().candidate(**values)

    def decision(self, **values) -> BettingDecision:
        with self.lock:
            self.decision_calls += 1
        return super().decision(**values)


def test_decision_repository_returns_prior_immutable_evaluation_on_retry_and_concurrency() -> None:
    repository = InMemoryDecisionRepository()
    ids = _CountingIds()

    def evaluate_once():
        return _evaluate(repository=repository, ids=ids)

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda _: evaluate_once(), range(8)))

    assert ids.candidate_calls == 1
    assert ids.decision_calls == 2
    assert all(result[0] is results[0][0] and result[1] is results[0][1] for result in results)
