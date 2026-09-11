from __future__ import annotations

import importlib.util
import math
from datetime import UTC, datetime
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location("season_scoring", Path(__file__).parents[1] / "ops/season_scoring.py")
scoring = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(scoring)

CLOCK = datetime(2026, 9, 12, tzinfo=UTC)
POLICY = {"policy_id": "official-v1", "effective_at": "2026-09-10T00:00:00Z"}


def prediction(revision, generated, *, origin="FINAL", schedule_version="s2", role="champion", probabilities=(0.7, 0.25, 0.05), kickoff="2026-09-11T00:00:00Z", model="model-v2"):
    return {"revision_id": revision, "generated_at": generated, "published_at": generated,
            "kickoff": kickoff, "origin": origin, "schedule_version": schedule_version,
            "model_version": model, "role": role, "status": "VALID",
            "p_home": probabilities[0], "p_away": probabilities[1], "p_tie": probabilities[2]}


def game(game_id="g1", *, kickoff="2026-09-11T00:00:00Z", predictions=None, outcomes=None, week=1):
    return {"game_id": game_id, "season": 2026, "week": week, "home": "H", "away": "A",
            "kickoff": kickoff, "schedule_version": "s2", "predictions": predictions or [], "outcomes": outcomes or []}


def final(version=1, observed="2026-09-11T04:00:00Z", scores=(24, 17)):
    return {"status": "FINAL", "home_score": scores[0], "away_score": scores[1],
            "observed_at": observed, "version": version, "supersedes": None, "source": "official"}


def test_revision_selection_is_independent_of_outcome_and_each_horizon_selects_latest():
    forecasts = [prediction("t72-old", "2026-09-07T00:00:00Z", origin="T72"),
                 prediction("t72-new", "2026-09-07T01:00:00Z", origin="T72"),
                 prediction("t60", "2026-09-10T23:00:00Z", origin="T60"),
                 prediction("challenger", "2026-09-10T23:50:00Z", role="challenger", probabilities=(0.1, .85, .05))]
    home = scoring.score_season([game(predictions=forecasts, outcomes=[final(scores=(24, 17))])], CLOCK, POLICY)
    away = scoring.score_season([game(predictions=forecasts, outcomes=[final(scores=(17, 24))])], CLOCK, POLICY)
    for report in (home, away):
        ids = report["games"][0]["selected_prediction_ids"]
        assert ids == {"official": "t60", "T72": "t72-new", "T60": "t60", "FINAL": None}


def test_wrong_schedule_version_post_kickoff_and_changed_kickoff_are_rejected():
    forecasts = [prediction("wrong-version", "2026-09-10T20:00:00Z", schedule_version="s1"),
                 prediction("late", "2026-09-11T00:00:00Z"),
                 prediction("old-kickoff", "2026-09-10T20:00:00Z", kickoff="2026-09-10T23:00:00Z")]
    report = scoring.score_season([game(predictions=forecasts, outcomes=[final()])], CLOCK, POLICY)
    assert report["games"][0]["coverage"] == "MISSED"
    assert report["games"][0]["prediction_id"] is None


def test_legacy_forecast_is_valid_only_for_exact_current_kickoff():
    legacy = prediction(None, "2026-09-10T20:00:00Z")
    legacy.pop("revision_id"); legacy.pop("schedule_version"); legacy["prediction_id"] = "legacy-1"
    accepted = scoring.score_season([game(predictions=[legacy], outcomes=[final()])], CLOCK, POLICY)
    assert accepted["games"][0]["prediction_id"] == "legacy-1"
    legacy["kickoff"] = "2026-09-10T23:00:00Z"
    rejected = scoring.score_season([game(predictions=[legacy], outcomes=[final()])], CLOCK, POLICY)
    assert rejected["games"][0]["coverage"] == "MISSED"


def test_all_game_coverage_pending_tbd_missed_and_tie_metrics():
    games = [game("tbd", kickoff=None),
             game("future", kickoff="2026-09-20T00:00:00Z"),
             game("missed", outcomes=[final()]),
             game("tie", predictions=[prediction("tie-p", "2026-09-10T20:00:00Z", probabilities=(.2, .3, .5))], outcomes=[final(scores=(20, 20))])]
    report = scoring.score_season(games, CLOCK, POLICY)
    assert [row["coverage"] for row in report["games"]] == ["PENDING", "PENDING", "MISSED", "SETTLED"]
    season = report["summary"]["season"]
    assert season["games"] == 4 and season["settled"] == 1 and season["ties"] == 1
    assert season["pending_coverage"] == 2 and season["missed_coverage"] == 1
    assert season["correct"] == 0 and season["missed"] == 0 and season["not_yet_forecast"] == 3
    assert report["horizons"]["FINAL"]["games"] == 4
    assert season["winner_accuracy_denominator"] == 0 and season["straight_up_accuracy"] is None
    assert season["multinomial_log_loss"] == pytest.approx(math.log(2))
    assert season["calibration_bins"][0]["count"] == 1
    assert report["horizons"]["T72"]["games"] == 4
    assert report["weekly_horizons"]["FINAL"]["1"]["games"] == 4


def test_latest_observed_final_correction_is_used_without_changing_prediction():
    outcomes = [final(1, scores=(24, 17)), final(2, "2026-09-11T05:00:00Z", scores=(17, 24))]
    report = scoring.score_season([game(predictions=[prediction("p1", "2026-09-10T20:00:00Z")], outcomes=outcomes)], CLOCK, POLICY)
    row = report["games"][0]
    assert row["prediction_id"] == "p1" and row["outcome_version"] == 2 and row["result"] == "away"
    assert row["multinomial_log_loss"] == pytest.approx(-math.log(.25))


def test_policy_marks_already_kicked_games_retrospective_and_error_analysis_never_promotes():
    report = scoring.score_season([game(predictions=[prediction("p1", "2026-09-10T20:00:00Z")], outcomes=[final()])], CLOCK,
                                  {"effective_at": "2026-09-11T01:00:00Z"})
    assert report["games"][0]["retrospective_policy"] is True
    analysis = report["weekly_error_analysis"][1]
    assert analysis["feature_scope"] == "pregame_only" and analysis["causal_claims"] == []
    assert analysis["promotion_action"] == "NONE"


def test_model_breakdowns_compare_elo_only_on_identical_settled_games():
    forecasts = [
        prediction("elo", "2026-09-10T18:00:00Z", model="Elo-v1", probabilities=(.55, .4, .05)),
        prediction("champ", "2026-09-10T20:00:00Z", model="champ-v2", probabilities=(.7, .25, .05)),
    ]
    report = scoring.score_season(
        [game("matched", predictions=forecasts, outcomes=[final()]),
         game("unmatched", predictions=[prediction("only", "2026-09-10T20:00:00Z", model="champ-v2")], outcomes=[final()])],
        CLOCK, POLICY,
    )
    comparison = report["matched_elo_comparisons"][0]
    assert comparison["game_ids"] == ["matched"]
    assert comparison["model_scores"]["settled"] == comparison["elo_scores"]["settled"] == 1


def test_error_analysis_reports_recorded_stale_and_missing_pregame_inputs():
    forecast = prediction("p1", "2026-09-10T20:00:00Z")
    forecast["inputs"] = {"weather": {"status": "STALE"}, "injuries": None, "schedule": {"status": "FRESH"}}
    report = scoring.score_season([game(predictions=[forecast], outcomes=[final()])], CLOCK, POLICY)
    analysis = report["weekly_error_analysis"][1]
    assert analysis["capture_staleness"] == [{"game_id": "g1", "inputs": ["weather"]}]
    assert analysis["missing_inputs"] == [{"game_id": "g1", "inputs": ["injuries"]}]


def test_latest_observation_retraction_makes_previous_final_unresolved():
    retraction = {"status": "UNRESOLVED", "home_score": None, "away_score": None,
                  "observed_at": "2026-09-11T05:00:00Z", "version": 2, "supersedes": 1, "source": "official"}
    report = scoring.score_season(
        [game(predictions=[prediction("p1", "2026-09-10T20:00:00Z")], outcomes=[final(), retraction])], CLOCK, POLICY
    )
    row = report["games"][0]
    assert row["result"] is None and row["coverage"] == "PENDING"
    assert report["summary"]["season"]["settled"] == 0


def test_future_generated_and_published_before_generated_are_rejected():
    future = prediction("future", "2026-09-13T00:00:00Z", kickoff="2026-09-20T00:00:00Z")
    reversed_times = prediction("reversed", "2026-09-10T20:00:00Z")
    reversed_times["published_at"] = "2026-09-10T19:00:00Z"
    report = scoring.score_season(
        [game("future", kickoff="2026-09-20T00:00:00Z", predictions=[future]),
         game("reversed", predictions=[reversed_times], outcomes=[final()])], CLOCK, POLICY
    )
    assert [row["prediction_id"] for row in report["games"]] == [None, None]


def test_horizon_and_challenger_metrics_use_their_own_probabilities():
    forecasts = [
        prediction("elo-t72", "2026-09-07T00:00:00Z", origin="T72", model="Elo-v1", probabilities=(.8, .15, .05)),
        prediction("challenger-t72", "2026-09-07T01:00:00Z", origin="T72", model="challenger-v1", role="challenger", probabilities=(.2, .75, .05)),
        prediction("official-t60", "2026-09-10T23:00:00Z", origin="T60", model="champ-v1", probabilities=(.8, .15, .05)),
    ]
    report = scoring.score_season([game(predictions=forecasts, outcomes=[final(scores=(17, 24))])], CLOCK, POLICY)
    assert report["horizons"]["T72"]["missed"] == 1
    comparison = next(item for item in report["matched_elo_comparisons"] if item["horizon"] == "T72")
    assert comparison["model"] == "challenger-v1"
    assert comparison["model_scores"]["correct"] == 1
    assert comparison["elo_scores"]["missed"] == 1



def test_same_timestamp_retraction_uses_numeric_outcome_version():
    clock = datetime(2030, 9, 10, tzinfo=UTC)
    game = {'outcomes': [
        {'status':'FINAL','observed_at':clock.isoformat(),'version':9,'home_score':21,'away_score':10},
        {'status':'UNRESOLVED','observed_at':clock.isoformat(),'version':10},
    ]}
    assert scoring._latest_outcome(game,clock) is None
