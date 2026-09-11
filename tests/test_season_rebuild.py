from __future__ import annotations

import importlib.util
import sys
from datetime import UTC, datetime
from pathlib import Path

import numpy as np

from nfl_predictor.contracts.enums import ProvenanceGrade
from nfl_predictor.features.schema import FEATURE_NAMES_V1

MODULE_PATH = Path(__file__).parents[1] / "ops" / "season_rebuild.py"
SPEC = importlib.util.spec_from_file_location("season_rebuild", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
season_rebuild = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = season_rebuild
SPEC.loader.exec_module(season_rebuild)
RebuildRow = season_rebuild.RebuildRow
choose_calibrator_family = season_rebuild.choose_calibrator_family
chronological_fold = season_rebuild.chronological_fold
evaluation_report = season_rebuild.evaluation_report
evaluate_if_changed = season_rebuild.evaluate_if_changed
eligible_history_ids = season_rebuild.eligible_history_ids


def _row(season: int, week: int, suffix: str, label: str, elo: float, epa: float) -> RebuildRow:
    values = np.zeros(len(FEATURE_NAMES_V1), dtype=np.float64)
    values[2] = elo
    values[11] = epa
    values[14] = -epa
    return RebuildRow(
        event_id=f"{season}-{suffix}",
        season=season,
        week=week,
        kickoff_at_utc=datetime(season, 9, week, 17, tzinfo=UTC),
        result=label,
        features=tuple(values.tolist()),
        snapshot_id=f"snapshot-{season}-{suffix}",
        input_manifest_sha256s=("a" * 64,),
        provenance_grade=ProvenanceGrade.C,
    )


def test_chronological_fold_reserves_2025() -> None:
    fold = chronological_fold(2025, range(2016, 2026))
    assert fold.estimator_seasons == tuple(range(2016, 2024))
    assert fold.calibration_season == 2024
    assert fold.test_season == 2025


def test_history_filter_excludes_target_and_future_games() -> None:
    rows = [
        {"game_id": "prior", "kickoff_at_utc": "2025-09-01T17:00:00+00:00"},
        {"game_id": "target", "kickoff_at_utc": "2025-09-08T17:00:00+00:00"},
        {"game_id": "future", "kickoff_at_utc": "2025-09-15T17:00:00+00:00"},
    ]
    assert eligible_history_ids(rows, "target") == {"prior"}


def test_calibrator_selection_never_reads_holdout() -> None:
    rows = [
        _row(season, 1, str(i), "home" if i % 2 else "away", i - 2.0, i / 10)
        for season in range(2016, 2025)
        for i in range(1, 5)
    ]
    family, used = choose_calibrator_family(rows, holdout_season=2025)
    assert family in {"identity", "sigmoid"}
    assert 2025 not in used
    assert max(used) <= 2024


def test_evaluation_is_matched_and_explicitly_not_promotion_eligible() -> None:
    rows = [
        _row(season, week, str(week), "home" if week % 2 else "away", week, week / 10)
        for season in range(2016, 2026)
        for week in range(1, 5)
    ]
    report = evaluation_report(rows, holdout_season=2025)
    assert report["holdout_season"] == 2025
    assert report["matched_games"] == 4
    assert report["evidence_grade"] == "C"
    assert report["promotion_eligible"] is False
    assert report["promotion_blockers"] == ["HISTORICAL_RECONSTRUCTION_GRADE_C"]
    assert set(report["models"]) == {"elo", "epa_logistic"}
    assert set(report["calibrator_family_by_model"]) == {"elo", "epa_logistic"}


def test_evaluate_if_changed_reuses_identity_and_invalidates_dataset_or_config(
    tmp_path, monkeypatch
) -> None:
    root = tmp_path / "research"
    dataset = root / "datasets/current.jsonl"
    dataset.parent.mkdir(parents=True)
    coverage = root / "reports/coverage.json"
    coverage.parent.mkdir(parents=True)
    coverage.write_text('{"included_games": 1, "excluded_games": 0}')
    config = {
        "dataset_path": "datasets/current.jsonl",
        "report_path": "reports/latest.json",
        "coverage_path": "reports/coverage.json",
        "evaluation_records_path": "evaluations/records",
        "evaluation_index_path": "evaluations/index",
        "model_policy": "configs/model_policy_v1.toml",
        "holdout_season": 2025,
    }
    dataset.write_bytes(season_rebuild._row_bytes(_row(2025, 1, "a", "home", 1, 1)) + b"\n")
    calls = []

    def fake_report(rows, holdout):
        calls.append([row.event_id for row in rows])
        return {"holdout_season": holdout, "matched_games": len(rows), "promotion_eligible": False}

    monkeypatch.setattr(season_rebuild, "evaluation_report", fake_report)
    first, reused = evaluate_if_changed(config, root, b"config-a")
    duplicate, duplicate_reused = evaluate_if_changed(config, root, b"config-a")
    assert reused is False and duplicate_reused is True
    assert first == duplicate and len(calls) == 1
    assert len(list((root / "evaluations/records").glob("*.json"))) == 1

    dataset.write_bytes(season_rebuild._row_bytes(_row(2025, 1, "b", "away", 2, 2)) + b"\n")
    changed_dataset, reused = evaluate_if_changed(config, root, b"config-a")
    changed_config, reused_config = evaluate_if_changed(config, root, b"config-b")
    assert reused is False and reused_config is False
    assert changed_dataset["experiment"]["signature"] != first["experiment"]["signature"]
    assert changed_config["experiment"]["signature"] != changed_dataset["experiment"]["signature"]

    coverage.write_text('{"included_games": 1, "excluded_games": 1}')
    changed_coverage, reused_coverage = evaluate_if_changed(config, root, b"config-b")
    assert reused_coverage is False
    assert changed_coverage["coverage"]["excluded_games"] == 1
    assert changed_coverage["experiment"]["coverage_sha256"] != changed_config["experiment"]["coverage_sha256"]
    assert changed_coverage["experiment"]["signature"] != changed_config["experiment"]["signature"]
    assert len(calls) == 4
    assert len(list((root / "evaluations/records").glob("*.json"))) == 4
