from __future__ import annotations

import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "ops"))
import season_qb

NOW = datetime(2026, 9, 12, tzinfo=UTC)


def config():
    return {
        "experiment_freeze_season": 2026,
        "development_end_season": 2023,
        "validation_season": 2024,
        "known_benchmark_season": 2025,
        "qb_coefficient_min": -2.0,
        "qb_coefficient_max": 2.0,
        "qb_fit_iterations": 30,
        "minimum_qb_games": 2,
        "minimum_qb_attempts": 20,
        "qb_model_version": "qb-v1",
        "production_policy_sha256": "policy",
        "qb_state_as_of": "2026-02-01T00:00:00Z",
        "qb_state_through_season": 2025,
        "player_values": {},
        "team_references": {},
    }


def row(game_id, season, x, result):
    return {
        "game_id": game_id,
        "season": season,
        "kickoff": f"{season}-09-10T00:00:00Z",
        "feature_as_of": f"{season}-09-09T00:00:00Z",
        "home_qb_value": x,
        "home_team_reference": 0.0,
        "away_qb_value": 0.0,
        "away_team_reference": 0.0,
        "baseline_p_home": 0.5,
        "baseline_p_away": 0.45,
        "baseline_p_tie": 0.05,
        "result_home": result,
    }


def artifact():
    a = config()
    a.update(
        {
            "schema_version": "qb-residual-v1",
            "coefficient": 0.2,
            "artifact_id": "a",
            "through_season": 2025,
            "qb_shrinkage_attempts": 20,
            "eligibility": "INELIGIBLE_FOR_PROMOTION",
            "player_values": {
                "h": {"epa": 50.0, "games": 3, "attempts": 50, "as_of": "2026-02-01T00:00:00Z"},
                "a": {"epa": 0.0, "games": 3, "attempts": 50, "as_of": "2026-02-01T00:00:00Z"},
            },
            "team_references": {"ATL": 0.0, "PIT": 0.0},
            "data_as_of": "2026-02-01T00:00:00Z",
        }
    )
    return a


def game():
    return {
        "home": "ATL",
        "away": "PIT",
        "season": 2026,
        "kickoff": "2026-09-13T17:00:00Z",
        "inputs": {
            "expected_qb": {
                "status": "AVAILABLE",
                "source_check_status": "AVAILABLE",
                "fetch_failed": False,
                "captured_at": "2026-09-12T00:00:00Z",
                "source_updated_at": "2026-09-11T12:00:00Z",
                "raw_sha256": "raw",
                "source_url": "https://github.com/x",
                "data": {
                    "ATL": {"gsis_id": "h", "player_name": "Tua Tagovailoa"},
                    "PIT": {"gsis_id": "a", "player_name": "Aaron Rodgers"},
                },
            },
            "injuries": {"status": "AVAILABLE", "data": []},
        },
    }


BASE = {"p_home": 0.5, "p_away": 0.45, "p_tie": 0.05}


def test_fit_uses_development_only_and_labels_known_periods():
    rows = [row("d", 2023, 1, 1), row("v", 2024, -5, 0), row("b", 2025, -5, 0)]
    value = season_qb.fit_qb(
        rows,
        config(),
        {
            "expected_starter_evidence_grade": "C",
            "source": "retrospective actual-starter field",
            "shrinkage_attempts": 100,
        },
        "code",
        lambda: NOW,
    )
    assert value["coefficient"] > 0
    assert value["metrics"]["validation"]["games"] == 1
    assert value["metrics"]["known_benchmark"]["games"] == 1
    assert value["eligibility"] == "INELIGIBLE_FOR_PROMOTION"
    assert value["production_policy_sha256"] == "policy"
    assert {"baseline_brier", "qb_brier", "delta_log_loss"} <= value["metrics"][
        "development"
    ].keys()


def test_future_feature_rejected():
    r = row("d", 2023, 1, 1)
    r["feature_as_of"] = r["kickoff"]
    with pytest.raises(ValueError, match="QB_FEATURE_NOT_PREGAME"):
        season_qb.fit_qb([r], config(), {}, "c", lambda: NOW)


def test_available_adjustment_preserves_tie_mass():
    out = season_qb.predict_qb(BASE, game(), artifact(), lambda: NOW)
    assert out["status"] == "AVAILABLE" and out["p_home"] > 0.5
    assert out["p_tie"] == 0.05 and sum(
        out[k] for k in ("p_home", "p_away", "p_tie")
    ) == pytest.approx(1)


def test_real_shape_injury_out_for_expected_qb_falls_back():
    g = game()
    g["inputs"]["injuries"]["data"] = [
        {"team": "ATL", "player": "Tua Tagovailoa", "game_status": "Out"}
    ]
    out = season_qb.predict_qb(BASE, g, artifact(), lambda: NOW)
    assert out["status"] == "FALLBACK" and out["p_home"] == BASE["p_home"]
    assert out["limitations"] == ["EXPECTED_QB_INJURY_STATUS_OUT"]


@pytest.mark.parametrize("status", ["Questionable", "Doubtful"])
def test_uncertain_expected_qb_is_not_unconditional(status):
    g = game()
    g["inputs"]["injuries"]["data"] = [
        {"team": "ATL", "player": "Tua Tagovailoa", "game_status": status}
    ]
    assert season_qb.predict_qb(BASE, g, artifact(), lambda: NOW)["status"] == "FALLBACK"


def test_failed_source_and_insufficient_history_fallback_exactly():
    g = game()
    g["inputs"]["expected_qb"]["fetch_failed"] = True
    assert season_qb.predict_qb(BASE, g, artifact(), lambda: NOW)["p_home"] == 0.5
    g = game()
    artifact()["player_values"]["h"]["attempts"] = 1
    a = artifact()
    a["player_values"]["h"]["attempts"] = 1
    assert season_qb.predict_qb(BASE, g, a, lambda: NOW)["limitations"] == [
        "QB_HISTORY_INSUFFICIENT"
    ]


def test_stale_state_after_team_final_falls_back():
    g = game()
    g["qb_state_required_after"] = "2026-09-11T00:00:00Z"
    assert season_qb.predict_qb(BASE, g, artifact(), lambda: NOW)["limitations"] == [
        "QB_STATE_STALE"
    ]


def test_refresh_state_archives_free_source_lineage(tmp_path, monkeypatch):
    import io

    import polars as pl

    frame = pl.DataFrame(
        {
            "player_id": ["h", "backup", "a"],
            "player_name": ["Home QB", "Home QB", "Away QB"],
            "recent_team": ["ATL", "ATL", "PIT"],
            "opponent_team": ["PIT", "PIT", "ATL"],
            "position": ["QB", "QB", "QB"],
            "season": [2025, 2025, 2025],
            "week": [1, 1, 1],
            "season_type": ["REG", "REG", "REG"],
            "attempts": [20, 20, 40],
            "passing_epa": [4.0, 6.0, -2.0],
        }
    )
    buffer = io.BytesIO()
    frame.write_parquet(buffer)
    body = buffer.getvalue()
    receipt = {
        "captured_at": "2026-02-01T00:00:00Z",
        "source_url": "https://github.com/nflverse",
        "raw_sha256": "raw",
        "source_last_modified": "x",
    }
    monkeypatch.setattr(season_qb.season_sources, "_fetch", lambda *args: (body, receipt))
    a = artifact()
    a["artifact_id"] = season_qb._hash({k: v for k, v in a.items() if k != "artifact_id"})
    c = config() | {
        "qb_player_stats_url": "https://github.com/nflverse",
        "qb_shrinkage_attempts": 20,
        "team_aliases": {},
    }
    finals = [
        {
            "game_id": "2025_01_PIT_ATL",
            "season": 2025,
            "week": 1,
            "home": "ATL",
            "away": "PIT",
            "status": "FINAL",
        }
    ]
    updated = season_qb.refresh_state(a, c, tmp_path, finals, lambda: NOW)
    assert updated["state_raw_sha256"] == "raw"
    assert updated["player_values"]["h"]["attempts"] == 20
    assert updated["included_game_ids"] == ["2025_01_PIT_ATL"]
    assert (
        season_qb.load_artifact(
            tmp_path / "research" / "qb" / "artifacts" / f"{updated['artifact_id']}.json", "policy"
        )
        == updated
    )


def test_artifact_roundtrip_and_policy_gate(tmp_path):
    value = season_qb.fit_qb(
        [row("d", 2023, 1, 1)],
        config(),
        {"expected_starter_evidence_grade": "C", "shrinkage_attempts": 100},
        "code",
        lambda: NOW,
    )
    path = season_qb.write_artifact(tmp_path, value)
    assert season_qb.load_artifact(path, "policy") == value
    with pytest.raises(ValueError, match="QB_PRODUCTION_POLICY_MISMATCH"):
        season_qb.load_artifact(path, "different")


def test_missing_injury_evidence_fails_closed():
    g = game()
    g["inputs"]["injuries"]["status"] = "MISSING"
    assert season_qb.predict_qb(BASE, g, artifact(), lambda: NOW)["limitations"] == [
        "QB_INJURY_EVIDENCE_UNAVAILABLE"
    ]


def test_expected_qb_listed_inactive_falls_back():
    g = game()
    g["inputs"]["inactives"] = {"status": "AVAILABLE", "data": [{"gsis_id": "h"}]}
    assert season_qb.predict_qb(BASE, g, artifact(), lambda: NOW)["limitations"] == [
        "EXPECTED_QB_LISTED_INACTIVE"
    ]


def test_real_shape_inactive_name_falls_back():
    g = game()
    g["inputs"]["inactives"] = {
        "status": "AVAILABLE",
        "data": [{"team": "ATL", "position": "QB", "player": "Tua Tagovailoa"}],
    }
    assert season_qb.predict_qb(BASE, g, artifact(), lambda: NOW)["limitations"] == [
        "EXPECTED_QB_LISTED_INACTIVE"
    ]


def test_missing_required_final_in_state_falls_back():
    g = game()
    g["qb_state_required_game_ids"] = ["2026_01_SF_LA"]
    assert season_qb.predict_qb(BASE, g, artifact(), lambda: NOW)["limitations"] == [
        "QB_STATE_STALE"
    ]


def test_prepare_uses_warmup_and_strict_final_availability():
    c = config() | {"primary_horizon": "T60", "team_aliases": {}, "minimum_qb_games": 1}
    baselines = [
        {
            "horizon": "T60",
            "game_id": "2018_01_A_B",
            "season": 2018,
            "cutoff": "2018-09-01T00:00:00Z",
            "kickoff": "2018-09-02T00:00:00Z",
            "home": "A",
            "away": "B",
            "home_qb_id": "ha",
            "away_qb_id": "ab",
            "p_home": 0.5,
            "p_away": 0.45,
            "p_tie": 0.05,
            "result": "home",
        }
    ]
    games = []
    stats = []
    for week, available in [(1, "2016-09-03T00:00:00Z"), (2, "2018-09-01T00:00:00Z")]:
        games.append(
            {
                "game_id": f"g{week}",
                "season": 2016 if week == 1 else 2018,
                "week": week,
                "home": "A",
                "away": "B",
                "status": "FINAL",
                "available_at": available,
            }
        )
        for player, team, opp in [("ha", "A", "B"), ("ab", "B", "A")]:
            stats.append(
                {
                    "player_id": player,
                    "position": "QB",
                    "season_type": "REG",
                    "season": 2016 if week == 1 else 2018,
                    "week": week,
                    "recent_team": team,
                    "opponent_team": opp,
                    "attempts": 100,
                    "passing_epa": 10.0,
                }
            )
    prepared, lineage = season_qb.prepare_training_rows(baselines, stats, games, c)
    assert len(prepared) == 1
    assert lineage["shrinkage_attempts"] == 100
    # The game available exactly at cutoff is excluded; only the 2016 warmup contributes.
    assert prepared[0]["home_qb_value"] == pytest.approx(0.1)


def test_ties_do_not_fit_conditional_qb_coefficient():
    decisive = [row("a", 2023, 1, 1), row("b", 2023, -1, 0)]
    fitted = season_qb._fit_coefficient(decisive, -10, 10, 30)
    assert season_qb._fit_coefficient(decisive + [row("tie", 2023, 10, 0.5)], -10, 10, 30) == fitted
    metrics = season_qb._score(decisive + [row("tie", 2023, 10, 0.5)], fitted)
    assert metrics["accuracy_sample_size"] == 2 and metrics["ties"] == 1
    assert metrics["qb_accuracy"] == 1
    with pytest.raises(ValueError, match="DECISIVE_EMPTY"):
        season_qb._fit_coefficient([row("tie", 2023, 10, 0.5)], -10, 10, 30)


@pytest.mark.parametrize("epa", [float("nan"), float("inf"), "bad"])
def test_nonfinite_epa_rejected(epa):
    with pytest.raises(ValueError, match="QB_EPA_INVALID"):
        season_qb._validate_stat({"passing_epa": epa, "attempts": 20})


def test_predict_freezes_at_kickoff():
    g = game()
    assert season_qb.predict_qb(BASE, g, artifact(), lambda: season_qb._time(g["kickoff"]))[
        "limitations"
    ] == ["KICKOFF_PASSED"]


def test_uncertain_starter_scenarios_have_no_weights_and_preserve_evidence():
    import copy

    g = game()
    g["inputs"]["injuries"]["data"] = [
        {"team": "ATL", "player": "Tua Tagovailoa", "game_status": "Out"}
    ]
    g["inputs"]["expected_qb"]["data"]["ATL"]["alternatives"] = [
        {"player_name": "Backup", "gsis_id": "b"},
        {"player_name": "Rookie", "gsis_id": "rookie"},
    ]
    a = artifact()
    a["player_values"]["b"] = copy.deepcopy(a["player_values"]["h"])
    preserved = copy.deepcopy(g)
    result = season_qb.predict_qb(BASE, g, a, lambda: NOW)
    assert g == preserved and result["status"] == "FALLBACK"
    scenarios = result["conditional_scenarios"]
    assert len(scenarios) == 2
    assert scenarios[0]["status"] == "AVAILABLE" and "Backup" in scenarios[0]["assumption"]
    assert scenarios[1]["status"] == "FALLBACK" and "p_home" not in scenarios[1]
    assert all("weight" not in value for value in scenarios)
    assert scenarios[0]["evidence"]["expected_qb"] == g["inputs"]["expected_qb"]


@pytest.mark.parametrize("mode", ["duplicate", "nan", "history_start"])
def test_refresh_rejects_corrupt_stats_and_respects_history_start(tmp_path, monkeypatch, mode):
    import io

    import polars as pl

    rows = [
        {
            "player_id": "h",
            "player_name": "Home",
            "recent_team": "ATL",
            "opponent_team": "PIT",
            "position": "QB",
            "season": 2015,
            "week": 1,
            "season_type": "REG",
            "attempts": 30,
            "passing_epa": 2.0,
        }
    ]
    if mode != "history_start":
        rows[0]["season"] = 2025
    if mode == "duplicate":
        rows.append(dict(rows[0]))
    if mode == "nan":
        rows[0]["passing_epa"] = float("nan")
    buffer = io.BytesIO()
    pl.DataFrame(rows).write_parquet(buffer)
    receipt = {"captured_at": "2026-09-12T00:00:00Z", "source_url": "source", "raw_sha256": "raw"}
    monkeypatch.setattr(
        season_qb.season_sources, "_fetch", lambda *args: (buffer.getvalue(), receipt)
    )
    c = config() | {"qb_player_stats_url": "source", "history_start": 2016}
    finals = [
        {
            "game_id": "g",
            "season": rows[0]["season"],
            "week": 1,
            "home": "ATL",
            "away": "PIT",
            "status": "FINAL",
        }
    ]
    if mode == "history_start":
        assert (
            season_qb.refresh_state(artifact(), c, tmp_path, finals, lambda: NOW)["player_values"]
            == {}
        )
    else:
        with pytest.raises(
            ValueError,
            match="QB_PLAYER_GAME_DUPLICATE" if mode == "duplicate" else "QB_EPA_INVALID",
        ):
            season_qb.refresh_state(artifact(), c, tmp_path, finals, lambda: NOW)


def test_explicit_inactive_fetch_failure_blocks_qb_publication():
    g = game()
    g["inputs"]["inactives"] = {"status": "MISSING", "fetch_failed": True}
    assert season_qb.predict_qb(BASE, g, artifact(), lambda: NOW)["limitations"] == [
        "QB_INACTIVES_FETCH_FAILED"
    ]


def test_optional_unverified_inactives_are_disclosed():
    result = season_qb.predict_qb(BASE, game(), artifact(), lambda: NOW)
    assert result["status"] == "AVAILABLE"
    assert any("inactives not yet verified" in reason for reason in result["limitations"])
