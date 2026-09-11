from __future__ import annotations

import hashlib
import json
import socket
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
import numpy as np
import polars as pl
import pytest

from nfl_predictor.betting.policy import (
    BookSettlementContract,
    CalibrationBinEvidence,
    CandidateContext,
    CandidatePolicy,
    InMemoryDecisionRepository,
    load_odds_policy,
)
from nfl_predictor.capture.service import CaptureService
from nfl_predictor.cli import SchedulerFreshnessPolicy
from nfl_predictor.config import AppConfig
from nfl_predictor.contracts.betting import BettingDecision
from nfl_predictor.contracts.enums import Origin
from nfl_predictor.contracts.events import EventVersion, ForecastOrigin
from nfl_predictor.contracts.markets import DisplayedQuoteCandidate
from nfl_predictor.evaluation.promotion import load_evaluation_policy
from nfl_predictor.features.builder import FeatureBuilder
from nfl_predictor.features.policy import load_feature_policy
from nfl_predictor.features.schema import FEATURE_SCHEMA_V1
from nfl_predictor.features.team_strength import PointInTimeRatingService
from nfl_predictor.features.venue import VenueStore
from nfl_predictor.identity.origins import OriginObligation
from nfl_predictor.markets.budget import InMemoryBudgetRepository, OddsBudget
from nfl_predictor.models.artifacts import ArtifactMetadata, ArtifactStore
from nfl_predictor.models.tie import TieLayer
from nfl_predictor.runtime.artifacts import (
    FrozenForecastArtifact,
    VerifiedArtifactRegistry,
    VerifiedForecastPredictor,
)
from nfl_predictor.runtime.capture import (
    ArrowOutcomeAdapter,
    NflverseFootballNormalizer,
    OptionalOddsCapture,
    RequiredFootballCapture,
)
from nfl_predictor.runtime.lineage import DurableLineageRepository, RuntimePaths
from nfl_predictor.runtime.markets import (
    DurableCalibrationBins,
    FrozenCalibrationBin,
    ProductionMarketLayer,
)
from nfl_predictor.runtime.services import RuntimeClients, RuntimeComponents
from nfl_predictor.sources.base import BuildIdentity
from nfl_predictor.sources.nflverse import NflverseAdapter
from nfl_predictor.sources.odds_api import OddsApiAdapter
from nfl_predictor.storage import schema_for, write_contracts
from nfl_predictor.workflows.forecast import (
    DurableForecastRepository,
    ForecastExecutionContext,
    ForecastRepositories,
    ForecastWorkflow,
)
from nfl_predictor.workflows.outcomes import DurableOutcomeReportRepository, OutcomeWorkflow
from nfl_predictor.workflows.report import ReportWorkflow

ROOT = Path(__file__).resolve().parents[2]
NOW = datetime(2026, 9, 10, 22, 0, tzinfo=UTC)
KICKOFF = datetime(2026, 9, 13, 22, 0, tzinfo=UTC)
OUTCOME_AT = datetime(2026, 9, 14, 3, 0, tzinfo=UTC)
CODE_SHA = "1" * 40
LOCK_SHA = "2" * 64
ARTIFACT_ID = "fixture-champion-t72"


@dataclass(frozen=True)
class DeterministicModel:
    def predict_r_home(self, features: np.ndarray) -> np.ndarray:
        assert features.shape == (1, len(FEATURE_SCHEMA_V1))
        assert features.dtype == np.float64
        return np.asarray([0.60], dtype=np.float64)


@dataclass(frozen=True)
class DeterministicCalibrator:
    def transform(self, values: Sequence[float]) -> np.ndarray:
        assert values == [0.60]
        return np.asarray([0.60], dtype=np.float64)


class DeterministicCandidateIds:
    def candidate(self, **values: Any) -> DisplayedQuoteCandidate:
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

    def decision(self, **values: Any) -> BettingDecision:
        prediction = values["prediction"]
        return BettingDecision(
            decision_id=f"decision-{values['side']}",
            prediction_id=prediction.prediction_id,
            canonical_event_id=prediction.canonical_event_id,
            origin=prediction.origin,
            side=values["side"],
            quote_id=values["quote_id"],
            candidate_id=values["candidate_id"],
            evaluated_at_utc=prediction.decision_at_utc,
            policy_version=values["policy_version"],
            status=values["status"],
            reason_codes=values["reason_codes"],
        )


def _event() -> EventVersion:
    return EventVersion(
        canonical_event_id="2026_REG_02_GB_CHI",
        event_version=1,
        source_event_ids={
            "nflverse": "2026_02_GB_CHI",
            "the_odds_api": "odds-event-1",
        },
        season=2026,
        season_type="REG",
        week=2,
        home_team="CHI",
        away_team="GB",
        kickoff_at_utc=KICKOFF,
        venue_id="soldier-field",
        neutral_site=False,
        observed_at_utc=NOW - timedelta(days=7),
        available_at_utc=NOW - timedelta(days=7),
        captured_at_utc=NOW - timedelta(days=7),
        raw_payload_sha256="a" * 64,
    )


def _schedules(*, final: bool) -> pl.DataFrame:
    return pl.DataFrame(
        {
            "game_id": ["2025_01_GB_CHI", "2026_01_SEA_SF", "2026_02_GB_CHI"],
            "season": [2025, 2026, 2026],
            "game_type": ["REG", "REG", "REG"],
            "week": [1, 1, 2],
            "gameday": ["2025-09-07", "2026-09-06", "2026-09-13"],
            "gametime": ["18:00", "18:00", "18:00"],
            "away_team": ["GB", "SEA", "GB"],
            "home_team": ["CHI", "SF", "CHI"],
            "away_score": [17, 14, 17 if final else None],
            "home_score": [20, 21, 24 if final else None],
            "location": ["Home", "Home", "Home"],
            "div_game": [True, False, True],
            "stadium_id": ["soldier-field", "levis-stadium", "soldier-field"],
        }
    )


def _pbp() -> pl.DataFrame:
    return pl.DataFrame(
        {
            "game_id": [
                "2025_01_GB_CHI",
                "2025_01_GB_CHI",
                "2026_01_SEA_SF",
                "2026_01_SEA_SF",
            ],
            "play_id": [1, 2, 1, 2],
            "posteam": ["GB", "CHI", "SEA", "SF"],
            "defteam": ["CHI", "GB", "SF", "SEA"],
            "passer_player_id": ["qb-gb", "qb-chi", "qb-sea", "qb-sf"],
            "pass_attempt": [1, 1, 1, 1],
            "rush_attempt": [0, 0, 0, 0],
            "epa": [0.1, 0.2, -0.1, 0.3],
            "qb_epa": [0.1, 0.2, -0.1, 0.3],
            "cpoe": [1.0, 2.0, -1.0, 3.0],
        }
    )


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _registry(private_root: Path) -> VerifiedArtifactRegistry:
    artifact_root = private_root / "artifacts"
    metadata = ArtifactMetadata(
        artifact_id=ARTIFACT_ID,
        model_family="deterministic-logistic",
        calibrator_family="deterministic-identity",
        origin=Origin.T72,
        model_lane="football_only",
        feature_schema_version="feature-schema-v1",
        feature_policy_version="feature-v1",
        model_policy_version="model-v1",
        calibration_policy_version="calibration-v1",
        split_policy_version="split-v1",
        input_manifest_sha256s=("3" * 64,),
        training_event_ids=("training-1",),
        calibration_event_ids=("calibration-1",),
        training_cutoff_at_utc=NOW - timedelta(days=30),
        calibration_cutoff_at_utc=NOW - timedelta(days=20),
        code_sha=CODE_SHA,
        dependency_lock_sha256=LOCK_SHA,
        seeds={"model": 1},
        fold_ledger_path="folds/fixture.json",
        fold_ledger_sha256="4" * 64,
        metrics={"brier": 0.20},
        python_version="3.11.13",
    )
    ArtifactStore(artifact_root).save(
        FrozenForecastArtifact(
            DeterministicModel(),
            DeterministicCalibrator(),
            "fixture-calibrator",
            TieLayer(0.02),
        ),
        metadata,
    )
    store = ArtifactStore(artifact_root)
    registry_path = private_root / "config" / "artifact-registry.json"
    registry_path.parent.mkdir(parents=True)
    registry_path.write_text(
        json.dumps(
            {
                "entries": [
                    {
                        "artifact_id": ARTIFACT_ID,
                        "origin": "T72",
                        "model_role": "champion",
                        "model_lane": "football_only",
                        "feature_schema_version": "feature-schema-v1",
                        "feature_policy_version": "feature-v1",
                        "policy_versions": {
                            "model": "model-v1",
                            "calibration": "calibration-v1",
                        },
                        "market_policy_version": "odds-v1",
                        "candidate_policy_version": "candidate-v1",
                        "code_sha": CODE_SHA,
                        "dependency_lock_sha256": LOCK_SHA,
                        "marker_sha256": _sha(store.marker_path(ARTIFACT_ID)),
                        "metadata_sha256": _sha(store.metadata_path(ARTIFACT_ID)),
                        "payload_sha256": _sha(store.payload_path(ARTIFACT_ID)),
                    }
                ]
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    return VerifiedArtifactRegistry.from_private_config(
        private_root, artifact_root, registry_path, _sha(registry_path)
    )


# Catches the production adapters drifting apart while unit-test doubles still agree.
def test_fixture_source_to_scorecard_chain_never_uses_network(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    network_attempts = 0

    def fail_network(*args: object, **kwargs: object) -> None:
        del args, kwargs
        nonlocal network_attempts
        network_attempts += 1
        raise AssertionError("network access is forbidden")

    monkeypatch.setattr(socket, "socket", fail_network)
    monkeypatch.setattr(socket, "getaddrinfo", fail_network)
    real_stat = Path.stat

    def fixture_receipt_metadata(path: Path, *args: object, **kwargs: object):
        result = real_stat(path, *args, **kwargs)  # type: ignore[arg-type]
        if path.parent.name.startswith("forecast-terminal-receipt-"):
            publication_ns = int((NOW - timedelta(seconds=1)).timestamp() * 1_000_000_000)
            return SimpleNamespace(
                st_ctime_ns=publication_ns,
                st_mtime_ns=result.st_mtime_ns,
                st_nlink=result.st_nlink,
            )
        return result

    monkeypatch.setattr(Path, "stat", fixture_receipt_metadata)
    private_root = tmp_path / "private"
    private_root.mkdir()
    lineage = DurableLineageRepository(private_root / "lineage", private_root)
    policy = load_feature_policy(ROOT / "configs" / "feature_policy_v1.toml")
    capture_service = CaptureService(
        private_root, lambda *_: None, BuildIdentity(CODE_SHA, LOCK_SHA)
    )

    def load_schedules(*, seasons: list[int]) -> pl.DataFrame:
        assert seasons == [2025, 2026]
        return _schedules(final=False)

    def load_pbp(*, seasons: list[int]) -> pl.DataFrame:
        assert seasons == [2025, 2026]
        return _pbp()

    football_source = NflverseAdapter(
        {"schedules": load_schedules, "pbp": load_pbp},
        lambda: NOW - timedelta(seconds=1),
    )
    required_capture = RequiredFootballCapture(
        capture_service=capture_service,
        adapter=football_source,
        normalizer=NflverseFootballNormalizer(policy),
        lineage=lineage,
    )
    settlement_contracts = tuple(
        BookSettlementContract(
            book_key=book,
            market_semantics_version="nfl-h2h-v1",
            settlement_policy_url=f"https://{book}.test/rules",
            settlement_policy_version="v1",
            overtime_included=True,
            tie_handling="push",
            verified_on="2026-09-01",
        )
        for book in ("book-a", "book-b")
    )
    odds_policy = load_odds_policy(ROOT / "configs" / "odds_policy_v1.toml").model_copy(
        update={
            "capture_enabled": True,
            "book_allowlist": ("book-a", "book-b"),
            "book_settlement_contracts": settlement_contracts,
        }
    )
    odds_http_calls = 0

    def odds_response(request: httpx.Request) -> httpx.Response:
        nonlocal odds_http_calls
        odds_http_calls += 1
        assert request.url.params["apiKey"] == "fixture-key"
        return httpx.Response(
            200,
            headers={
                "x-requests-used": "1",
                "x-requests-remaining": "499",
                "x-requests-last": "1",
            },
            json=[
                {
                    "id": "odds-event-1",
                    "sport_key": "americanfootball_nfl",
                    "sport_title": "NFL",
                    "commence_time": KICKOFF.isoformat(),
                    "home_team": "Chicago Bears",
                    "away_team": "Green Bay Packers",
                    "bookmakers": [
                        {
                            "key": "book-a",
                            "title": "Book A",
                            "last_update": (NOW - timedelta(minutes=1)).isoformat(),
                            "markets": [
                                {
                                    "key": "h2h",
                                    "last_update": (
                                        NOW - timedelta(minutes=1)
                                    ).isoformat(),
                                    "outcomes": [
                                        {"name": "Chicago Bears", "price": 2.0},
                                        {"name": "Green Bay Packers", "price": 1.9},
                                    ],
                                }
                            ],
                        },
                        {
                            "key": "book-b",
                            "title": "Book B",
                            "last_update": (NOW - timedelta(minutes=1)).isoformat(),
                            "markets": [
                                {
                                    "key": "h2h",
                                    "last_update": (
                                        NOW - timedelta(minutes=1)
                                    ).isoformat(),
                                    "outcomes": [
                                        {"name": "Chicago Bears", "price": 1.98},
                                        {"name": "Green Bay Packers", "price": 1.92},
                                    ],
                                }
                            ],
                        },
                    ],
                }
            ],
        )

    odds_http = httpx.Client(transport=httpx.MockTransport(odds_response))
    optional_odds = OptionalOddsCapture(
        policy=odds_policy,
        api_key="fixture-key",
        budget=OddsBudget(InMemoryBudgetRepository(), odds_policy.monthly_hard_stop),
        capture_service=capture_service,
        adapter_factory=lambda key: OddsApiAdapter(key, client=odds_http, clock=lambda: NOW),
        active_events=lambda: (_event(),),
        month=lambda _obligation: "2026-09",
        lineage=lineage,
    )
    feature_builder = FeatureBuilder(
        lineage,
        policy,
        VenueStore.from_csv(ROOT / "configs" / "venues_v1.csv"),
        PointInTimeRatingService(policy=policy),
    )
    registry = _registry(private_root)
    predictor = VerifiedForecastPredictor(registry)
    candidate_policy = CandidatePolicy(
        odds_policy,
        odds_policy,
        DeterministicCandidateIds(),
        CandidateContext(
            ARTIFACT_ID,
            {"forecast": "forecast-v1", "model": "model-v1"},
        ),
        InMemoryDecisionRepository(),
    )
    bins = DurableCalibrationBins(
        private_root,
        private_root / "config" / "calibration-bins.json",
        (
            FrozenCalibrationBin(
                Decimal("0.30"),
                Decimal("0.50"),
                CalibrationBinEvidence(
                    one_sided_upper_absolute_error=Decimal("0.01"),
                    sample_count=50,
                    confidence=0.95,
                    cutoff_at_utc=NOW - timedelta(days=1),
                    origin=Origin.T72,
                    binning_method="equal_count",
                    out_of_sample=True,
                    frozen=True,
                    evidence_id="fixture-away-bin",
                ),
            ),
            FrozenCalibrationBin(
                Decimal("0.50"),
                Decimal("0.70"),
                CalibrationBinEvidence(
                    one_sided_upper_absolute_error=Decimal("0.01"),
                    sample_count=50,
                    confidence=0.95,
                    cutoff_at_utc=NOW - timedelta(days=1),
                    origin=Origin.T72,
                    binning_method="equal_count",
                    out_of_sample=True,
                    frozen=True,
                    evidence_id="fixture-home-bin",
                ),
            ),
        ),
    )
    market_layer = ProductionMarketLayer(
        active_event=lambda event_id: _event()
        if event_id == _event().canonical_event_id
        else None,
        odds_policy=odds_policy,
        candidate_policy=candidate_policy,
        calibration_bins=bins,
        comparator_id=lambda prediction, _quote_ids: f"comparator-{prediction.prediction_id}",
    )
    app = AppConfig(code_root=ROOT, data_root=private_root)
    paths = RuntimePaths.from_config(app)
    forecast_repository = DurableForecastRepository(
        paths.prospective_forecast_root, "prospective", artifact_resolver=registry
    )
    obligation = OriginObligation(
        _event(), ForecastOrigin.for_kickoff(Origin.T72, KICKOFF), "forecast-v1"
    )
    forecast_workflow = ForecastWorkflow(
        repositories=ForecastRepositories(forecast_repository),
        required_capture=required_capture,
        market_capture=optional_odds,
        feature_builder=feature_builder,
        lineage_repository=lineage,
        artifact_registry=registry,
        predictor=predictor,
        market_layer=market_layer,
        clock=lambda: NOW,
        code_sha=CODE_SHA,
        id_factory=lambda: "attempt-fixture",
    )
    forecast = forecast_workflow.run(
        obligation, "fixture", ForecastExecutionContext.live(NOW)
    )
    assert forecast is not None
    assert forecast.status == "COMPLETE", forecast.reason_codes

    outcome_repository = DurableOutcomeReportRepository(paths.outcome_report_root, registry)

    def load_final_schedules(*, seasons: list[int]) -> pl.DataFrame:
        assert seasons == [2026]
        return _schedules(final=True)

    outcome_source = NflverseAdapter(
        {"schedules": load_final_schedules},
        lambda: OUTCOME_AT,
    )
    outcome_adapter = ArrowOutcomeAdapter(capture_service, outcome_source)
    outcome_workflow = OutcomeWorkflow(outcome_adapter, outcome_repository)
    report_workflow = ReportWorkflow(
        outcome_repository,
        evaluation_policy=load_evaluation_policy(
            ROOT / "configs" / "evaluation_policy_v1.toml"
        ),
        interval_confidence=Decimal("0.95"),
    )
    event_path = private_root / "events" / "active.parquet"
    event_sha = write_contracts(event_path, (_event(),), schema_for(EventVersion))
    active_manifest = private_root / "active-events.json"
    active_manifest.write_text(
        json.dumps(
            {
                "schema_version": "active-events-v1",
                "season": 2026,
                "files": [
                    {
                        "path": "events/active.parquet",
                        "sha256": event_sha,
                        "events": [
                            {
                                "canonical_event_id": _event().canonical_event_id,
                                "event_version": 1,
                            }
                        ],
                    }
                ],
            },
            sort_keys=True,
            separators=(",", ":"),
        ),
        encoding="utf-8",
    )
    (private_root / "config" / "data_repo.toml").write_text(
        "\n".join(
            (
                "[schedule]",
                f'active_event_version_manifest_sha256 = "{_sha(active_manifest)}"',
            )
        ),
        encoding="utf-8",
    )
    components = RuntimeComponents(
        app=app,
        paths=paths,
        clock=lambda: NOW,
        clients=RuntimeClients(),
        lineage=lineage,
        artifact_registry=registry,
        required_capture=required_capture,
        optional_odds_capture=optional_odds,
        predictor=predictor,
        market_layer=market_layer,
        feature_builder=feature_builder,
        odds_policy=odds_policy,
        outcome_adapter=outcome_adapter,
        outcome_repository=outcome_repository,
        outcome_workflow=outcome_workflow,
        report_workflow=report_workflow,
        scheduler_freshness_policy=SchedulerFreshnessPolicy(
            "scheduler-freshness-v1", timedelta(minutes=5)
        ),
        dependency_lock_sha256=LOCK_SHA,
        forecast_policy_version="forecast-v1",
        code_sha=CODE_SHA,
        expected_policy_sha256={},
    )
    services = components.service_registry()

    outcome_result = services.outcomes_sync(
        season=2026,
        through_week=2,
        context=ForecastExecutionContext.live(OUTCOME_AT),
    )
    settlement_result = services.settle(
        season=2026,
        through_week=2,
        context=ForecastExecutionContext.live(OUTCOME_AT),
    )
    scorecards = services.report_weekly(
        season=2026,
        through_week=2,
        context=ForecastExecutionContext.live(OUTCOME_AT),
    )

    assert outcome_result["runs"][0].outcome.result == "home"
    assert settlement_result["imported_forecast_graphs"] == 1
    assert settlement_result["reconciled_settlements"] == 1
    assert settlement_result["settlement_count"] == 1
    probability = next(row for row in scorecards.probability if row.artifact_id == ARTIFACT_ID)
    winner = next(row for row in scorecards.winner if row.artifact_id == ARTIFACT_ID)
    market = next(row for row in scorecards.market if row.artifact_id == ARTIFACT_ID)
    assert probability.n_all_settled == 1
    assert winner.straight_up_accuracy == 1.0
    assert market.quote_ids
    assert optional_odds.enabled is True
    assert odds_http_calls == 1
    assert network_attempts == 0
    odds_http.close()
