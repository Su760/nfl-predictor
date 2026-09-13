import copy
import importlib
import math
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parents[1] / "ops"))
probability = importlib.import_module("season_probability")


def settings():
    cfg = probability.configuration()
    cfg["bootstrap_replicates"] = 40
    cfg["minimum_fit_non_ties"] = 2
    return cfg


def policy():
    import tomllib

    return tomllib.loads((Path(__file__).parents[1] / "configs/model_policy_v1.toml").read_text())[
        "elo"
    ]


def game(gid, season, kickoff, home_score=20, away_score=10, kind="REG"):
    at = datetime.fromisoformat(kickoff)
    return {
        "game_id": gid,
        "season": season,
        "week": 1,
        "home": "LA",
        "away": "SF",
        "kickoff": kickoff,
        "available_at": probability.stamp(at + timedelta(hours=24)),
        "home_score": home_score,
        "away_score": away_score,
        "neutral_site": True,
        "game_type": kind,
        "result": "home"
        if home_score > away_score
        else "away"
        if away_score > home_score
        else "tie",
    }


def artifact():
    a = {
        "schema_version": "season-probability-artifact-v1",
        "applies_to": "production_elo_conditional_home",
        "role": "challenger",
        "production_change": "NONE",
        "train_through": 2023,
        "validation_season": 2024,
        "known_benchmark_season": 2025,
        "prospective_season": 2026,
        "calibration": {"family": "sigmoid", "slope": 0.8, "intercept": 0.1, "epsilon": 1e-15},
        "calibration_bounds": {
            "minimum_slope": 0.05,
            "maximum_slope": 5,
            "maximum_abs_intercept": 3,
        },
    }
    a["artifact_sha256"] = probability.digest(probability.canonical(a))
    return a


def test_replay_cannot_use_target_or_future_result():
    games = [
        game("prior", 2017, "2017-09-01T00:00:00Z"),
        game("target", 2018, "2018-09-01T00:00:00Z"),
        game("future", 2018, "2018-09-08T00:00:00Z"),
    ]
    before = probability.replay(games, policy(), settings())
    games[1]["home_score"] = 99
    games[1]["result"] = "home"
    games[2]["away_score"] = 99
    after = probability.replay(games, policy(), settings())
    for left, right in zip(before, after, strict=True):
        if left["game_id"] == "target":
            assert left["p_home"] == right["p_home"]
            assert left["history_games"] == 1
        assert datetime.fromisoformat(left["last_history_available_at"]) < datetime.fromisoformat(
            left["cutoff"]
        )


def test_horizon_history_and_all_postseason_updates():
    cfg = settings()
    previous = game("postseason", 2017, "2018-02-01T00:00:00Z", kind="SB")
    regular = game("regular", 2017, "2017-10-01T00:00:00Z")
    target = game("target", 2018, "2018-09-01T00:00:00Z")
    rows = probability.replay([regular, previous, target], policy(), cfg)
    p = policy()
    rater = probability.EloRater(**p)
    rater.snapshot(
        [
            probability.CompletedGame(
                g["game_id"],
                g["season"],
                g["home"],
                g["away"],
                g["home_score"],
                g["away_score"],
                True,
                datetime.fromisoformat(g["available_at"]),
            )
            for g in [regular, previous]
        ],
        datetime.fromisoformat(target["kickoff"]),
    )
    rater.ratings = {
        t: p["initial"] + (r - p["initial"]) * p["offseason_retention"]
        for t, r in rater.ratings.items()
    }
    assert all(row["q_home"] == rater.home_probability("LA", "SF", True) for row in rows)
    assert all(row["p_tie"] == 0.25 for row in rows)
    assert len(rows) == 3


def test_t72_excludes_recent_results_included_at_t60():
    games = [
        game("prior", 2017, "2017-09-01T00:00:00Z"),
        game("recent", 2018, "2018-08-29T00:00:00Z"),
        game("target", 2018, "2018-09-01T00:00:00Z"),
    ]
    rows = [r for r in probability.replay(games, policy(), settings()) if r["game_id"] == "target"]
    assert next(r for r in rows if r["horizon"] == "T72")["history_games"] == 1
    assert next(r for r in rows if r["horizon"] == "T60")["history_games"] == 2


def test_validated_calibration_matches_saved_formula_and_is_monotone():
    a = artifact()
    expected = 1 / (1 + math.exp(-(0.1 + 0.8 * math.log(0.7 / 0.3))))
    assert probability.calibrated_probability(0.7, a) == pytest.approx(expected)
    assert probability.calibrated_probability(0.2, a) < probability.calibrated_probability(0.8, a)
    for p in (0, 1):
        assert 0 < probability.calibrated_probability(p, a) < 1


@pytest.mark.parametrize("value", [True, float("nan"), float("inf"), -0.01, 1.01])
def test_invalid_probability_rejected(value):
    with pytest.raises((TypeError, ValueError)):
        probability.calibrated_probability(value, artifact())


def test_artifact_tampering_and_scope_rejected():
    a = artifact()
    a["calibration"]["slope"] = 3
    with pytest.raises(ValueError, match="HASH_MISMATCH"):
        probability.calibrated_probability(0.6, a)
    a = artifact()
    a["applies_to"] = "tuned_elo"
    with pytest.raises(ValueError, match="SCOPE"):
        probability.calibrated_probability(0.6, a)


def test_paired_bootstrap_alignment_and_reproducibility():
    cfg = settings()
    games = [
        game("prior", 2017, "2017-09-01T00:00:00Z"),
        game("target", 2018, "2018-09-01T00:00:00Z"),
    ]
    rows = probability.replay(games, policy(), cfg)
    result = probability.paired_uncertainty(rows, copy.deepcopy(rows), cfg)
    assert result == probability.paired_uncertainty(rows, rows, cfg)
    assert result["intervals"]["multinomial_log_loss"] == {"delta": 0, "lower": 0, "upper": 0}
    with pytest.raises(ValueError, match="MATCHED_GAME_HORIZONS"):
        probability.paired_uncertainty(rows, rows[:-1], cfg)


def test_known_benchmark_cannot_enter_development_fit():
    cfg = settings()
    rows = [{"horizon": "T60", "season": year} for year in (2017, 2018, 2023, 2024, 2025)]
    assert [r["season"] for r in probability._period(rows, cfg, "development")] == [2018, 2023]
    assert probability._period(rows, cfg, "validation")[0]["season"] == 2024
    assert probability._period(rows, cfg, "known_benchmark")[0]["season"] == 2025


def test_historical_duplicate_and_unknown_venue_fail_closed(tmp_path):
    source = tmp_path / "games.csv"
    header = "game_id,season,week,home_team,away_team,home_score,away_score,gameday,gametime,location,game_type\n"
    row = "g,2018,1,LA,SF,20,10,2018-09-01,13:00,Home,REG\n"
    source.write_text(header + row + row)
    with pytest.raises(ValueError, match="DUPLICATE"):
        probability.historical_games(source, settings())
    source.write_text((header + row).replace(",Home,", ",Unknown,"))
    with pytest.raises(ValueError, match="UNKNOWN_HISTORICAL_VENUE"):
        probability.historical_games(source, settings())


def test_evaluation_index_reuses_archive_without_refitting(tmp_path, monkeypatch):
    cfg = settings()
    cfg["research_root"] = str(tmp_path)
    bundle = {"files": {"model.py": "preserved source"}, "environment": {"python": "test"}}
    bundle_path = probability._object(tmp_path, "source-versions", bundle)
    identity = {"freeze_sha256": "freeze", "source_bundle_sha256": bundle_path.stem}
    a = artifact()
    a.pop("artifact_sha256")
    a.update(source_bundle_sha256=bundle_path.stem, freeze_sha256="freeze")
    a["artifact_sha256"] = probability.digest(probability.canonical(a))
    artifact_path = probability._object(tmp_path, "artifacts", a)
    report_path = probability._object(
        tmp_path, "reports", {"source_bundle_sha256": bundle_path.stem}
    )
    index_path = (
        tmp_path
        / "evaluation-index"
        / (probability.digest(probability.canonical(identity)) + ".json")
    )
    expected = {
        "artifact_path": str(artifact_path),
        "report_path": str(report_path),
        "source_bundle_path": str(bundle_path),
        "experiment_identity": identity,
        "artifact_file_sha256": artifact_path.stem,
    }
    probability.write_once(index_path, expected)
    monkeypatch.setattr(probability, "load_freeze", lambda *args: ({}, cfg, {}))
    monkeypatch.setattr(probability, "_source_bundle", lambda: bundle)

    def unexpected_fit(*args, **kwargs):
        raise AssertionError("cached evaluation must not replay or fit")

    monkeypatch.setattr(probability, "export_baseline", unexpected_fit)
    assert probability.evaluate(tmp_path / "freeze.json") == expected
    assert probability.evaluate(tmp_path / "freeze.json") == expected
    artifact_path.write_text("tampered")
    with pytest.raises(ValueError, match="ARCHIVE_HASH_MISMATCH"):
        probability.evaluate(tmp_path / "freeze.json")


def test_source_bundle_and_period_roles_preserve_reproducibility():
    bundle = probability._source_bundle()
    current = (Path(__file__).parents[1] / "ops/season_probability.py").read_text()
    assert bundle["files"]["ops/season_probability.py"] == current
    assert "src/nfl_predictor/ratings/elo.py" in bundle["files"]
    assert bundle["environment"]["scikit-learn"]
    assert "not out-of-sample" in probability._period_roles()["development"]
    assert "not untouched" in probability._period_roles()["known_benchmark"]
