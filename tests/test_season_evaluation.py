import importlib
import sys
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).parents[1] / "ops"))
e = importlib.import_module("season_evaluation")


def test_lineage_rejects_equal_or_later_availability_and_unknown_history():
    cutoff = datetime(2025, 9, 5, tzinfo=UTC)
    games = {"prior": {"available_at": "2025-09-05T00:00:00Z"}}
    assert e.eligible_selection("target", ["target", "prior"], games, cutoff) == (
        False,
        "HISTORY_AFTER_CUTOFF",
    )
    games["prior"]["available_at"] = "2025-09-04T23:59:59Z"
    assert e.eligible_selection("target", ["target", "prior"], games, cutoff) == (True, None)
    assert not e.eligible_selection("target", ["target", "unknown"], games, cutoff)[0]
    assert not e.eligible_selection("target", ["prior"], games, cutoff)[0]


def row(gid="a", result="home"):
    return {
        "game_id": gid,
        "season": 2024,
        "week": 1,
        "horizon": "T60",
        "cutoff": "2024-09-05T00:00:00Z",
        "q_home": 0.6,
        "p_home": 0.594,
        "p_away": 0.396,
        "p_tie": 0.01,
        "result": result,
    }


def test_game_scoring_ties_and_duplicate_revisions():
    cfg = e.probability_configuration()
    scored = e.score([row(), row("b", "tie"), row("c", "away")], cfg)
    assert (scored["correct"], scored["n_non_ties"], scored["ties"], scored["n"]) == (1, 2, 1, 3)
    assert scored["accuracy_interval_95"][0] < 0.5 < scored["accuracy_interval_95"][1]
    with pytest.raises(ValueError, match="DUPLICATE"):
        e.score([row(), row()], cfg)


def test_pairing_rejects_different_game_and_cutoff():
    cfg = {"bootstrap_seed": 42, "bootstrap_replicates": 20}
    with pytest.raises(ValueError, match="MATCHED"):
        e.paired_delta([row()], [row("b")], cfg)
    with pytest.raises(ValueError, match="CUTOFF"):
        e.paired_delta([row()], [{**row(), "horizon": "T72"}], cfg)
    for field, value in (("result", "away"), ("season", 2023), ("week", 2)):
        with pytest.raises(ValueError, match="CONTEXT"):
            e.paired_delta([row()], [{**row(), field: value}], cfg)
    assert e.paired_delta([row()], [row()], cfg)["interval_95"] == [0, 0]


def test_test_season_labels_and_future_features_cannot_change_fitted_probabilities():
    from dataclasses import replace

    from season_rebuild import RebuildRow

    from nfl_predictor.contracts.enums import ProvenanceGrade

    rows = [
        RebuildRow(
            f"{season}_{i}",
            season,
            i + 1,
            datetime(season, 9, 1, tzinfo=UTC),
            "home" if i % 2 else "away",
            tuple([float(i % 3)] * 42),
            str(i),
            (),
            ProvenanceGrade.C,
        )
        for season in range(2016, 2025)
        for i in range(12)
    ]
    cfg = e.configuration()
    policy = e.load_model_policy(e.CODE_ROOT / "configs/model_policy_v1.toml")
    _, original, _, fold = e.fitted_rows(rows, 2023, [11, 14, 17, 20], policy, cfg)
    altered = [
        replace(r, result="away" if r.result == "home" else "home")
        if r.season == 2023
        else replace(r, features=tuple([999.0] * 42))
        if r.season > 2023
        else r
        for r in rows
    ]
    _, changed, _, _ = e.fitted_rows(altered, 2023, [11, 14, 17, 20], policy, cfg)
    np.testing.assert_array_equal(original, changed)
    assert max(fold["estimator_seasons"]) == 2021
    assert fold["calibration_season"] == 2022
    _, _, _, recent = e.fitted_rows(rows, 2023, [11, 14, 17, 20], policy, cfg, recent=True)
    assert recent["estimator_seasons"] == [2019, 2020, 2021]


def test_four_feature_adapter_matches_original_pipeline_and_excludes_future_labels():
    from dataclasses import replace

    from season_rebuild import RebuildRow

    from nfl_predictor.contracts.enums import ProvenanceGrade

    rng = np.random.default_rng(42)
    rows = [
        RebuildRow(
            f"{season}_{i}",
            season,
            i + 1,
            datetime(season, 9, 1, tzinfo=UTC),
            "tie" if i == 0 else "home" if i % 2 else "away",
            tuple(rng.normal(size=42)),
            str(i),
            (),
            ProvenanceGrade.C,
        )
        for season in range(2016, 2025)
        for i in range(30)
    ]
    small = [replace(r, features=tuple(r.features[i] for i in [11, 14, 17, 20])) for r in rows]
    cfg = e.configuration()
    policy = e.load_model_policy(e.CODE_ROOT / "configs/model_policy_v1.toml")
    _, old, _, _ = e.fitted_rows(rows, 2023, [11, 14, 17, 20], policy, cfg)
    _, new, _, _ = e.fitted_rows(small, 2023, [0, 1, 2, 3], policy, cfg)
    np.testing.assert_array_equal(old, new)
    changed = [
        replace(r, result="away" if r.result == "home" else "home") if r.season >= 2023 else r
        for r in small
    ]
    _, future, _, _ = e.fitted_rows(changed, 2023, [0, 1, 2, 3], policy, cfg)
    np.testing.assert_array_equal(new, future)
    assert e.evaluation_indices([11, 14, 20], {"feature_indices": [11, 14, 17, 20]}) == [0, 1, 3]
    with pytest.raises(ValueError, match="FEATURE_NOT_REBUILT"):
        e.evaluation_indices([21], {"feature_indices": [11, 14, 17, 20]})


def test_legacy_model_labels_cannot_mix_elo_and_shadow_samples():
    rows = [
        {"horizon": "T60", "model_name": "production_elo", "game_id": "a"},
        {"horizon": "T60", "model_name": "production_elo", "model": "football_epa", "game_id": "a"},
        {"horizon": "T72", "model_name": "production_elo", "game_id": "a"},
    ]
    assert e.rows_for_model(rows, "T60", "production_elo") == [rows[0]]
    assert e.rows_for_model(rows, "T60", "football_epa") == [rows[1]]


def test_period_comparison_uses_matching_saved_games_and_separates_legacy_subset():
    predictions = []
    for horizon in ("T72", "T60"):
        for model, q_home in (("production_elo", 0.6), ("football_epa", 0.55)):
            for gid, week, result in (
                ("w1", 1, "home"),
                ("w2", 2, "away"),
                ("w3", 3, "tie"),
            ):
                predictions.append(
                    {
                        **row(gid, result),
                        "horizon": horizon,
                        "cutoff": f"2024-09-0{week}T00:00:00Z",
                        "week": week,
                        "model": model,
                        "model_name": model,
                        "q_home": q_home,
                        "p_home": q_home * 0.99,
                        "p_away": (1 - q_home) * 0.99,
                    }
                )
    evaluation = {
        "reports": [
            {
                "season": "overall",
                "horizon": "T60",
                "available_games": 3,
                "matched_games": 3,
                "prior_sample_comparison": {
                    "production_elo": {
                        "prior": {"correct": 1, "n_non_ties": 2, "n": 2, "ties": 0}
                    }
                },
            },
            {"season": "overall", "horizon": "T72", "available_games": 3, "matched_games": 3},
        ],
        "declaration": {"reference_prediction_sha256": "legacy-hash"},
        "limitations": ["Reconstructed inputs."],
    }
    result = e.period_comparison(
        predictions,
        evaluation,
        {"horizons": {"T72": 259200, "T60": 3600}, "bootstrap_seed": 42, "bootstrap_replicates": 20},
        e.probability_configuration(),
    )
    assert result["ties_policy"] == "included in Brier/log loss; excluded from winner accuracy"
    assert result["horizons"]["T60"]["weeks_1_2"]["production_elo"]["n"] == 2
    assert result["horizons"]["T60"]["full_season"]["production_elo"]["ties"] == 1
    assert result["horizons"]["T60"]["legacy_subset"]["correct"] == 1
    assert result["horizons"]["T60"]["legacy_subset"]["prediction_sha256"] == "legacy-hash"
    assert result["horizons"]["T72"]["legacy_subset"] is None
    assert result["horizons"]["T60"]["prediction_horizon_seconds"] == 3600

    predictions[-1]["game_id"] = "different"
    with pytest.raises(ValueError, match="MATCHED_PERIOD_SAMPLE_REQUIRED"):
        e.period_comparison(
            predictions,
            evaluation,
            {"horizons": {"T72": 259200, "T60": 3600}, "bootstrap_seed": 42, "bootstrap_replicates": 20},
            e.probability_configuration(),
        )


def test_report_update_identity_hashes_every_metric_dependency(tmp_path):
    evaluation = tmp_path / "evaluation.json"
    predictions = tmp_path / "predictions.json"
    dependency = tmp_path / "metric.py"
    evaluation.write_text("evaluation")
    predictions.write_text("predictions")
    dependency.write_text("version one")
    first = e.report_update_identity("run", evaluation, predictions, [dependency])
    dependency.write_text("version two")
    second = e.report_update_identity("run", evaluation, predictions, [dependency])
    assert first["dependencies"] != second["dependencies"]
    assert e.digest(e.canonical(first)) != e.digest(e.canonical(second))
