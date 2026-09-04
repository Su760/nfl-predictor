"""Durable calibration evidence and production market-policy composition."""

from __future__ import annotations

import json
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from nfl_predictor.betting.policy import (
    CalibrationBinEvidence,
    CandidatePolicy,
    EventMatch,
    OddsPolicy,
    OfficialEventState,
)
from nfl_predictor.contracts.enums import Origin
from nfl_predictor.contracts.events import EventVersion
from nfl_predictor.contracts.forecasts import Prediction
from nfl_predictor.contracts.markets import MoneylineQuote
from nfl_predictor.markets.comparator import build_market_comparator
from nfl_predictor.workflows.forecast import MarketEvaluation


@dataclass(frozen=True)
class FrozenCalibrationBin:
    """Local bin coordinates that preserve the unchanged shared evidence contract."""

    lower: Decimal
    upper: Decimal
    evidence: CalibrationBinEvidence

    def __post_init__(self) -> None:
        if (
            not self.lower.is_finite()
            or not self.upper.is_finite()
            or self.lower < Decimal(0)
            or self.upper > Decimal(1)
            or self.lower >= self.upper
        ):
            raise ValueError("frozen calibration bin bounds must satisfy 0 <= lower < upper <= 1")

    def contains(self, probability: Decimal) -> bool:
        return self.lower <= probability < self.upper or (
            probability == Decimal(1) and self.upper == Decimal(1) and self.lower <= probability
        )


def _private_path(private_root: Path, path: Path) -> tuple[Path, Path]:
    try:
        root = private_root.resolve(strict=True)
    except OSError as exc:
        raise ValueError("trusted private root must exist") from exc
    if not root.is_dir():
        raise ValueError("trusted private root must be a directory")
    resolved = path.resolve(strict=False)
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise ValueError("calibration evidence path must resolve under trusted private root") from exc
    return root, resolved


class DurableCalibrationBins:
    """A local immutable evidence store used by the fail-closed candidate policy."""

    def __init__(
        self,
        private_root: Path,
        path: Path,
        evidence: Sequence[FrozenCalibrationBin] | None = None,
    ) -> None:
        self._private_root, self._path = _private_path(private_root, path)
        if evidence is not None:
            self._write(tuple(evidence))

    def _write(self, evidence: tuple[FrozenCalibrationBin, ...]) -> None:
        if len({item.evidence.evidence_id for item in evidence}) != len(evidence):
            raise ValueError("calibration evidence IDs must be unique")
        _, path = _private_path(self._private_root, self._path)
        path.parent.mkdir(parents=True, exist_ok=True)
        records = [
            {
                "lower": str(item.lower),
                "upper": str(item.upper),
                "evidence": {
                    "one_sided_upper_absolute_error": str(
                        item.evidence.one_sided_upper_absolute_error
                    ),
                    "sample_count": item.evidence.sample_count,
                    "confidence": item.evidence.confidence,
                    "cutoff_at_utc": item.evidence.cutoff_at_utc.isoformat(),
                    "origin": item.evidence.origin.value,
                    "binning_method": item.evidence.binning_method,
                    "out_of_sample": item.evidence.out_of_sample,
                    "frozen": item.evidence.frozen,
                    "evidence_id": item.evidence.evidence_id,
                },
            }
            for item in evidence
        ]
        path.write_text(json.dumps(records, sort_keys=True), encoding="utf-8")

    def _read(self) -> tuple[FrozenCalibrationBin, ...]:
        try:
            _, path = _private_path(self._private_root, self._path)
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, ValueError, json.JSONDecodeError) as exc:
            raise ValueError("calibration evidence store is unreadable") from exc
        if not isinstance(raw, list):
            raise TypeError("calibration evidence store must contain a list")
        records: list[FrozenCalibrationBin] = []
        for item in raw:
            if not isinstance(item, dict):
                raise TypeError("calibration evidence entry is invalid")
            try:
                stored = item["evidence"]
                if not isinstance(stored, dict):
                    raise TypeError("calibration evidence is invalid")
                cutoff = datetime.fromisoformat(str(stored["cutoff_at_utc"]))
                if cutoff.tzinfo is None or cutoff.utcoffset() != UTC.utcoffset(cutoff):
                    raise ValueError("calibration evidence cutoff must be UTC")
                records.append(
                    FrozenCalibrationBin(
                        lower=Decimal(str(item["lower"])),
                        upper=Decimal(str(item["upper"])),
                        evidence=CalibrationBinEvidence(
                            one_sided_upper_absolute_error=Decimal(
                                str(stored["one_sided_upper_absolute_error"])
                            ),
                            sample_count=stored["sample_count"],
                            confidence=stored["confidence"],
                            cutoff_at_utc=cutoff,
                            origin=Origin(stored["origin"]),
                            binning_method=stored["binning_method"],
                            out_of_sample=stored["out_of_sample"],
                            frozen=stored["frozen"],
                            evidence_id=stored["evidence_id"],
                        ),
                    )
                )
            except (KeyError, TypeError, ValueError) as exc:
                raise ValueError("calibration evidence entry is invalid") from exc
        if len({item.evidence.evidence_id for item in records}) != len(records):
            raise ValueError("calibration evidence IDs must be unique")
        return tuple(records)

    def lookup(self, **values: Any) -> CalibrationBinEvidence | None:
        origin = values.get("origin")
        probability = values.get("probability")
        strictly_before_utc = values.get("strictly_before_utc")
        minimum_games = values.get("minimum_games")
        confidence = values.get("confidence")
        if (
            not isinstance(origin, Origin)
            or not isinstance(probability, Decimal)
            or not isinstance(strictly_before_utc, datetime)
            or isinstance(minimum_games, bool)
            or not isinstance(minimum_games, int)
            or isinstance(confidence, bool)
            or not isinstance(confidence, (int, float))
        ):
            raise TypeError("calibration lookup arguments are invalid")
        if (
            strictly_before_utc.tzinfo is None
            or strictly_before_utc.utcoffset() != UTC.utcoffset(strictly_before_utc)
        ):
            raise ValueError("calibration lookup cutoff must be UTC")
        eligible = tuple(
            item
            for item in self._read()
            if item.evidence.origin is origin
            and item.evidence.cutoff_at_utc < strictly_before_utc
            and item.evidence.sample_count >= minimum_games
            and item.evidence.confidence == float(confidence)
            and item.evidence.binning_method == "equal_count"
            and item.evidence.out_of_sample is True
            and item.evidence.frozen is True
            and item.contains(probability)
        )
        if not eligible:
            return None
        if len(eligible) != 1:
            return None
        return eligible[0].evidence


class ProductionMarketLayer:
    def __init__(
        self,
        *,
        active_event: Callable[[str], EventVersion | None],
        odds_policy: OddsPolicy,
        candidate_policy: CandidatePolicy,
        calibration_bins: DurableCalibrationBins,
        comparator_id: Callable[[Prediction, Sequence[str]], str],
    ) -> None:
        self._active_event = active_event
        self._odds_policy = odds_policy
        self._candidate_policy = candidate_policy
        self._calibration_bins = calibration_bins
        self._comparator_id = comparator_id

    def evaluate(
        self,
        prediction: Prediction,
        market_payload: object,
        decision_at_utc: datetime,
    ) -> MarketEvaluation:
        if decision_at_utc != prediction.decision_at_utc:
            raise ValueError("market decision time must match prediction decision time")
        if not isinstance(market_payload, tuple) or not all(
            isinstance(item, MoneylineQuote) for item in market_payload
        ):
            raise TypeError("market payload must be the immutable normalized quote tuple")
        event = self._active_event(prediction.canonical_event_id)
        if event is None or event.canonical_event_id != prediction.canonical_event_id:
            raise ValueError("active event does not match prediction")
        event_state = OfficialEventState(event, "pregame")
        selected = self._odds_policy.select_frozen_capture(market_payload, prediction)
        if selected.reason_code is not None or not selected.quotes:
            raise ValueError("market payload has no unambiguous eligible capture")
        source_ids = {item.source_event_id for item in selected.quotes}
        if len(source_ids) != 1:
            raise ValueError("selected capture has competing provider source IDs")
        source_match = self._odds_policy.trusted_source_match(
            EventMatch(event, next(iter(source_ids)), None)
        )
        quote_ids = tuple(item.quote_id for item in selected.quotes)
        comparator = build_market_comparator(
            selected.quotes,
            prediction,
            tie_prior=prediction.p_tie,
            policy=self._odds_policy,
            market_comparator_id=self._comparator_id(prediction, quote_ids),
            event_state=event_state,
            source_match=source_match,
        )
        evaluations = self._candidate_policy.evaluate(
            prediction,
            event_state,
            selected.quotes,
            self._calibration_bins,
            source_match,
        )
        return MarketEvaluation(
            comparator=comparator,
            decisions=tuple(item.decision for item in evaluations),
            candidates=tuple(item.candidate for item in evaluations if item.candidate is not None),
            quotes=selected.quotes,
        )
