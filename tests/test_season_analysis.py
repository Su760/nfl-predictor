import copy
import importlib
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parents[1] / "ops"))
analysis = importlib.import_module("season_analysis")
score_season = importlib.import_module("season_scoring").score_season
canonical = analysis.canonical
digest = analysis.digest

NOW = datetime(2026, 9, 11, 20, tzinfo=UTC)


def fixture(tmp_path, neutral=True):
    model = {
        "ratings": {"LA": 1600, "SF": 1500},
        "p_tie": 0.01,
        "elo_policy": {"home_field_points": 65, "logistic_scale": 400},
    }
    mid = digest(canonical(model))
    (tmp_path / "models").mkdir()
    (tmp_path / "models" / (mid + ".json")).write_bytes(canonical(model))
    q = 1 / (1 + 10 ** (-(100 + (0 if neutral else 65)) / 400))
    prediction = {
        "game_id": "2026_01_SF_LA",
        "home": "LA",
        "away": "SF",
        "model_id": mid,
        "model_version": "elo",
        "revision_id": "r1",
        "origin": "T60",
        "kickoff": "2026-09-11T00:00:00Z",
        "generated_at": "2026-09-10T23:00:00Z",
        "published_at": "2026-09-10T23:00:00Z",
        "neutral_site": neutral,
        "p_home": q * 0.99,
        "p_away": (1 - q) * 0.99,
        "p_tie": 0.01,
        "inputs": {
            "injuries": {"status": "AVAILABLE", "used_by_model": False},
            "weather": {"status": "MISSING", "used_by_model": False},
        },
    }
    game = {
        "game_id": prediction["game_id"],
        "home": "LA",
        "away": "SF",
        "season": 2026,
        "week": 1,
        "kickoff": prediction["kickoff"],
        "predictions": [prediction],
        "inputs": {"weather": {"status": "AVAILABLE"}},
        "outcomes": [
            {
                "version": 1,
                "observed_at": "2026-09-11T04:00:00Z",
                "status": "FINAL",
                "home_score": 10,
                "away_score": 20,
            }
        ],
    }
    policy = {"effective_at": "2026-09-01T00:00:00Z"}
    view = {"games": [game], "scorecards": score_season([game], NOW, policy)}
    return model, prediction, game, view, policy


def test_exact_saved_math_and_neutral_venue(tmp_path):
    model, pred, *_ = fixture(tmp_path)
    result = analysis.explain_forecast(pred, model, NOW)
    assert result["status"] == "VERIFIED"
    assert result["provenance"] == "PREGAME"
    assert result["calculation"]["home_advantage"] == 0
    assert result["calculation"]["home_rating"] == 1600
    assert result["calculation"]["p_home"] == pred["p_home"]
    assert "injuries" in result["saved_inputs"]


def test_home_venue_and_missing_inputs_fail_closed(tmp_path):
    model, pred, *_ = fixture(tmp_path, neutral=False)
    assert analysis.explain_forecast(pred, model, NOW)["calculation"]["home_advantage"] == 65
    model["ratings"].pop("LA")
    result = analysis.explain_forecast(pred, model, NOW)
    assert result["status"] == "MISSING"
    assert result["calculation"] is None
    assert not result["factors"]


def test_current_state_cannot_rewrite_old_explanation(tmp_path):
    _, pred, game, view, _ = fixture(tmp_path)
    original = copy.deepcopy(pred)
    analysis.refresh_analysis(view, tmp_path, {}, NOW)
    explanation = copy.deepcopy(view["analysis"][game["game_id"]]["explanations"]["r1"])
    view["model"] = {"ratings": {"LA": 9999}}
    game["inputs"] = {"injuries": {"status": "STALE"}}
    analysis.refresh_analysis(view, tmp_path, {}, NOW + timedelta(hours=1))
    assert explanation == view["analysis"][game["game_id"]]["explanations"]["r1"]
    assert pred == original
    detail = analysis.game_detail(view, game["game_id"], tmp_path, {})
    assert detail["explanation"]["provenance"] == "RECONSTRUCTED"
    assert detail["current_context"] != detail["explanation"]["saved_inputs"]
    assert len(list((tmp_path / "analysis/reviews").glob("*.json"))) == 1


def test_embedded_pregame_explanation_remains_pregame(tmp_path):
    model, pred, game, view, _ = fixture(tmp_path)
    pred["explanation"] = analysis.explain_forecast(pred, model, pred["generated_at"])
    analysis.refresh_analysis(view, tmp_path, {}, NOW)
    assert view["analysis"][game["game_id"]]["explanations"]["r1"] == pred["explanation"]
    assert not list((tmp_path / "analysis/explanations").glob("*.json"))


@pytest.mark.parametrize(
    "scores,status", [((30, 20), "CORRECT"), ((10, 20), "INCORRECT"), ((20, 20), "TIE")]
)
def test_official_scoring_and_corrections_preserve_reviews(tmp_path, scores, status):
    _, _, game, view, policy = fixture(tmp_path)
    analysis.refresh_analysis(view, tmp_path, {}, NOW)
    first = copy.deepcopy(view["analysis"][game["game_id"]]["review"])
    game["outcomes"].append(
        {
            "version": 2,
            "observed_at": "2026-09-11T19:00:00Z",
            "status": "FINAL",
            "home_score": scores[0],
            "away_score": scores[1],
        }
    )
    view["scorecards"] = score_season([game], NOW, policy)
    analysis.refresh_analysis(view, tmp_path, {}, NOW)
    review = view["analysis"][game["game_id"]]["review"]
    assert review["status"] == status
    assert (
        review["metrics"]["multiclass_brier"] == view["scorecards"]["games"][0]["multiclass_brier"]
    )
    assert review["record_id"] != first["record_id"]
    assert len(view["analysis"][game["game_id"]]["review_history"]) == 2
    analysis.refresh_analysis(view, tmp_path, {}, NOW + timedelta(minutes=5))
    assert len(list((tmp_path / "analysis/reviews").glob("*.json"))) == 2


def test_missing_forecast_is_coverage_not_loss(tmp_path):
    _, _, game, view, policy = fixture(tmp_path)
    game["predictions"] = []
    view["scorecards"] = score_season([game], NOW, policy)
    analysis.refresh_analysis(view, tmp_path, {}, NOW)
    review = view["analysis"][game["game_id"]]["review"]
    assert review["status"] == "MISSING_FORECAST"
    assert review["metrics"]["multinomial_log_loss"] is None
    assert review["causal_claims"] == []


def test_altered_model_archive_rejected(tmp_path):
    model, pred, _, view, _ = fixture(tmp_path)
    model["ratings"]["LA"] = 9999
    (tmp_path / "models" / (pred["model_id"] + ".json")).write_bytes(canonical(model))
    with pytest.raises(ValueError, match="HASH_MISMATCH"):
        analysis.refresh_analysis(view, tmp_path, {}, NOW)


def test_legacy_policy_hash_required(tmp_path):
    model, pred, game, view, _ = fixture(tmp_path)
    policy = b"[elo]\nhome_field_points=65\nlogistic_scale=400\n"
    model.pop("elo_policy")
    model["policy_sha256"] = digest(policy)
    mid = digest(canonical(model))
    pred["model_id"] = mid
    (tmp_path / "models" / (mid + ".json")).write_bytes(canonical(model))
    policy_path = tmp_path / "policy.toml"
    policy_path.write_bytes(policy)
    analysis.refresh_analysis(view, tmp_path, {"model_policy": str(policy_path)}, NOW)
    assert view["analysis"][game["game_id"]]["explanations"]["r1"]["status"] == "VERIFIED"
    policy_path.write_text("[elo]\nhome_field_points=999\nlogistic_scale=400\n")
    analysis.refresh_analysis(view, tmp_path, {"model_policy": str(policy_path)}, NOW)
    assert view["analysis"][game["game_id"]]["explanations"]["r1"]["status"] == "VERIFIED"
    assert len(list((tmp_path / "analysis/explanations").glob("*.json"))) == 1
    recovered = analysis._saved_model(
        pred, tmp_path, {}, analysis._policies(tmp_path, {"model_policy": str(policy_path)})
    )
    assert analysis.explain_forecast(pred, recovered, NOW)["status"] == "MISSING"


def test_recheck_time_does_not_duplicate_review_but_new_evidence_does(tmp_path):
    _, _, game, view, _ = fixture(tmp_path)
    evidence = {
        game["game_id"]: {
            "status": "AVAILABLE",
            "captured_at": "2026-09-11T05:00:00Z",
            "raw_sha256": "original",
        }
    }
    analysis.refresh_analysis(view, tmp_path, {}, NOW, evidence)
    evidence[game["game_id"]]["captured_at"] = "2026-09-11T06:00:00Z"
    analysis.refresh_analysis(view, tmp_path, {}, NOW + timedelta(hours=1), evidence)
    assert len(list((tmp_path / "analysis/reviews").glob("*.json"))) == 1
    evidence[game["game_id"]]["raw_sha256"] = "correction"
    analysis.refresh_analysis(view, tmp_path, {}, NOW + timedelta(hours=2), evidence)
    assert len(list((tmp_path / "analysis/reviews").glob("*.json"))) == 2


def test_result_retraction_removes_active_metrics_retains_original(tmp_path):
    _, _, game, view, policy = fixture(tmp_path)
    analysis.refresh_analysis(view, tmp_path, {}, NOW)
    game["outcomes"].append(
        {"version": 2, "observed_at": "2026-09-11T19:00:00Z", "status": "RETRACTED"}
    )
    view["scorecards"] = score_season([game], NOW, policy)
    analysis.refresh_analysis(view, tmp_path, {}, NOW)
    detail = analysis.game_detail(view, game["game_id"], tmp_path, {})
    assert detail["postgame_review"]["status"] == "PENDING"
    assert detail["postgame_review"]["metrics"]["multiclass_brier"] is None
    assert len(detail["review_history"]) == 2


def test_historical_experiment_is_measured_rejected_and_immutable(tmp_path):
    _, _, _game, view, _ = fixture(tmp_path)
    directory = tmp_path / "research/evaluations/records"
    directory.mkdir(parents=True)
    report = {
        "evidence_grade": "C",
        "evidence_basis": "historical_reconstruction",
        "promotion_eligible": False,
        "promotion_blockers": ["HISTORICAL_RECONSTRUCTION_GRADE_C"],
        "matched_games": 265,
        "fold": {
            "estimator_seasons": [2016, 2017, 2018, 2019, 2020, 2021, 2022, 2023],
            "calibration_season": 2024,
            "test_season": 2025,
        },
        "models": {
            "elo": {"multinomial_log_loss": 0.66},
            "epa_logistic": {"multinomial_log_loss": 0.69},
        },
        "experiment": {"dataset_sha256": "saved-dataset", "config_sha256": "saved-config"},
    }
    (directory / (digest(canonical(report)) + ".json")).write_bytes(canonical(report))
    cfg = {"legacy_data_root": str(tmp_path)}
    analysis.refresh_analysis(view, tmp_path, cfg, NOW)
    first = copy.deepcopy(view["historical_experiments"])
    analysis.refresh_analysis(view, tmp_path, cfg, NOW + timedelta(hours=1))
    assert first == view["historical_experiments"]
    experiment = first[0]
    assert experiment["status"] == "rejected"
    assert experiment["chronology"]["test_season"] == 2025
    assert experiment["results"]["elo"]["multinomial_log_loss"] == 0.66
    assert experiment["production_change"] == "NONE"
    assert "not a causal" in experiment["relationship"]
    assert len(list((tmp_path / "analysis/historical-improvements").glob("*.json"))) == 1
