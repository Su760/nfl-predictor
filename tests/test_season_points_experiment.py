import json
from datetime import UTC, datetime, timedelta

import pytest
from season_points_experiment import (
    archive_forecast,
    eligible_market,
    latest_final,
    paired_report,
    partition,
    read_archives,
    save_selection,
    sheet,
)


def config():
    return {
        "future_games": 2,
        "maximum_archive_delay_seconds": 30,
        "bootstrap_seed": 42,
        "bootstrap_replicates": 100,
        "gate_mae_improvement": 0.02,
        "maximum_forecast_age_seconds": 86400,
        "maximum_source_age_seconds": 7200,
    }


def record(now, gid="g"):
    return {
        "game_id": gid,
        "kickoff": (now + timedelta(days=1)).isoformat(),
        "generated_at": now.isoformat(),
        "data_cutoff": now.isoformat(),
        "model_frozen_at": (now - timedelta(days=1)).isoformat(),
        "targets": {"margin": {"tracking_eligible": True}},
    }


def test_archive_first_only_future_only_limit_and_integrity(tmp_path):
    now = datetime(2027, 1, 1, tzinfo=UTC)
    r = record(now)
    assert archive_forecast(tmp_path, r, config(), lambda: now) == "ARCHIVED"
    before = next(iter((tmp_path / "forecasts").glob("*.json"))).read_bytes()
    assert (
        archive_forecast(tmp_path, {**r, "candidate": 999}, config(), lambda: now)
        == "ALREADY_ARCHIVED"
    )
    assert next(iter((tmp_path / "forecasts").glob("*.json"))).read_bytes() == before
    past = {**record(now, "past"), "kickoff": now.isoformat()}
    assert archive_forecast(tmp_path, past, config(), lambda: now) == "STARTED_OR_EXPIRED"
    assert archive_forecast(tmp_path, record(now, "g2"), config(), lambda: now) == "ARCHIVED"
    assert archive_forecast(tmp_path, record(now, "g3"), config(), lambda: now) == "COHORT_COMPLETE"
    assert len(read_archives(tmp_path)) == 2
    path = next(iter((tmp_path / "forecasts").glob("*.json")))
    path.write_text(before.decode().replace('"g"', '"tampered"'))
    with pytest.raises(ValueError, match="ARCHIVE_HASH_MISMATCH"):
        read_archives(tmp_path)


def test_expiry_and_model_cutoff_and_failed_gate(tmp_path):
    now = datetime(2027, 1, 1, tzinfo=UTC)
    assert (
        archive_forecast(tmp_path, record(now), config(), lambda: now + timedelta(seconds=31))
        == "STARTED_OR_EXPIRED"
    )
    r = record(now)
    r["model_frozen_at"] = (now + timedelta(seconds=1)).isoformat()
    with pytest.raises(ValueError, match="MODEL_AFTER_CUTOFF"):
        archive_forecast(tmp_path, r, config(), lambda: now)
    r = record(now)
    r["targets"]["margin"]["tracking_eligible"] = False
    assert archive_forecast(tmp_path, r, config(), lambda: now) == "GATE_FAILED"
    assert read_archives(tmp_path) == []


def rows():
    return [
        {
            "game_id": str(i),
            "season": 2025,
            "week": i // 2 + 1,
            "actual": 10,
            "baseline": 12,
            "candidate": 11,
            "radii": {"baseline": 3, "candidate": 3},
        }
        for i in range(8)
    ]


def test_paired_population_week_clusters_and_bias():
    result = paired_report(rows(), config())
    assert result["baseline"]["n"] == result["candidate"]["n"] == 8
    assert result["uncertainty"]["blocks"] == 4
    assert result["candidate"]["bias"] == 1 and result["gate"] == "PASS"
    assert result["uncertainty"]["paired_candidate_minus_baseline_95"]["mae"] == [-1, -1]
    with pytest.raises(ValueError, match="UNIQUE_PAIRED"):
        paired_report(rows() + [rows()[0]], config())
    with pytest.raises(ValueError, match="FINITE_PAIRED"):
        paired_report([{**rows()[0], "candidate": float("nan")}], config())


def test_interval_cutoff_and_season_fit_boundaries():
    rs = [
        {
            "season": y,
            "available_at": f"{y}-12-30T00:00:00+00:00",
            "cutoff": f"{y}-09-01T00:00:00+00:00",
        }
        for y in (2022, 2023, 2024)
    ]
    train, cal, test = partition(rs, 2024)
    assert train[0]["season"] == 2022 and cal[0]["season"] == 2023 and test[0]["season"] == 2024
    rs[1]["available_at"] = "2024-09-01T00:00:00+00:00"
    with pytest.raises(ValueError, match="CALIBRATION_OUTCOME_AFTER_TEST_CUTOFF"):
        partition(rs, 2024)


def test_market_timestamps_and_unknown_closing_are_separate():
    now = datetime(2027, 1, 1, tzinfo=UTC)
    kick = now + timedelta(days=1)
    quote = {
        "provider": "p",
        "sportsbook": "s",
        "target": "margin",
        "side": "home",
        "line": -3,
        "odds": -110,
        "market_timestamp": now.isoformat(),
        "retrieval_timestamp": now.isoformat(),
    }
    result = eligible_market([quote], now, kick)
    assert result["quotes"] == [quote] and result["cover_probability"] is None
    assert (
        eligible_market([{**quote, "retrieval_timestamp": kick.isoformat()}], now, kick)["status"]
        == "UNAVAILABLE"
    )
    assert (
        eligible_market([{**quote, "market_timestamp": None}], now, kick)["status"] == "UNAVAILABLE"
    )
    assert (
        eligible_market([{**quote, "kind": "closing_without_timestamp"}], now, kick)["status"]
        == "UNAVAILABLE"
    )


def test_latest_final_zero_missing_retraction_and_observation_cutoff():
    now = datetime(2027, 1, 1, tzinfo=UTC)
    outcome = {
        "status": "FINAL",
        "home_score": 0,
        "away_score": 0,
        "observed_at": now.isoformat(),
        "version": 1,
    }
    assert latest_final({"outcomes": [outcome]}, now) == outcome
    assert latest_final({"outcomes": [outcome]}, now - timedelta(seconds=1)) is None
    assert latest_final({"outcomes": [{**outcome, "home_score": None}]}, now) is None
    assert (
        latest_final({"outcomes": [outcome, {**outcome, "version": 2, "status": "RETRACTED"}]}, now)
        is None
    )


def test_sheet_unavailable_and_stale(tmp_path):
    from week1_live import canonical

    now = datetime(2027, 1, 1, tzinfo=UTC)
    assert sheet(config(), tmp_path, now)["status"] == "UNAVAILABLE"
    data = {
        "status": "AVAILABLE",
        "generated_at": now.isoformat(),
        "source_timestamp": (now - timedelta(hours=3)).isoformat(),
        "games": [],
    }
    (tmp_path / "view.json").write_bytes(canonical(data))
    assert sheet(config(), tmp_path, now)["status"] == "STALE"


def test_conflicting_same_version_final_is_order_independent():
    now = datetime(2027, 1, 1, tzinfo=UTC)
    a = {
        "status": "FINAL",
        "home_score": 20,
        "away_score": 10,
        "version": 1,
        "observed_at": now.isoformat(),
    }
    b = {**a, "home_score": 30}
    for outcomes in ([a, b], [b, a]):
        assert latest_final({"outcomes": outcomes}, now) is None


def test_selection_resume_preserves_original_timestamp(tmp_path):
    a = save_selection(tmp_path, {"margin": 10}, {"margin": []}, {"selection_seasons": [2019]})
    b = save_selection(tmp_path, {"margin": 10}, {"margin": []}, {"selection_seasons": [2019]})
    assert a == b
    with pytest.raises(ValueError, match="FROZEN_SELECTION_CHANGED"):
        save_selection(tmp_path, {"margin": 100}, {"margin": []}, {"selection_seasons": [2019]})


def test_publication_deadline_refusal_creates_no_archive(tmp_path, monkeypatch):
    import season_points_experiment as experiment

    now = datetime(2027, 1, 1, tzinfo=UTC)
    monkeypatch.setattr(experiment, "write_once", lambda *a, **kw: False)
    assert (
        archive_forecast(tmp_path, record(now), config(), lambda: now)
        == "STARTED_BEFORE_DURABLE_WRITE"
    )
    assert read_archives(tmp_path) == []


def test_real_forecast_preserves_source_before_archive_and_official_bytes(tmp_path, monkeypatch):
    import season_points_experiment as experiment
    from week1_live import canonical, digest

    now = datetime.now(UTC)
    cfg = {
        **config(),
        "version": "test-v1",
        "season_root": str(tmp_path / "official"),
        "slate_days": 8,
        "minimum_team_games": 3,
        "rolling_games": 8,
        "shrinkage_games": 4,
    }
    official = tmp_path / "official"
    official.mkdir()
    game = {
        "game_id": "future",
        "season": now.year,
        "week": 5,
        "home": "A",
        "away": "B",
        "neutral_site": False,
        "kickoff": (now + timedelta(days=1)).isoformat(),
        "schedule_version": "s1",
        "time_valid": True,
        "status": "STATUS_SCHEDULED",
        "outcomes": [],
    }
    unknown = {**game, "game_id": "unknown", "kickoff": None, "time_valid": False}
    source = canonical({"games": [game, unknown], "last_successful_source_check": now.isoformat()})
    (official / "view.json").write_bytes(source)
    linear = {"mean": [0] * 5, "scale": [1] * 5, "beta": [0] * 5, "intercept": 3}
    model = {
        "hfa": 2.5,
        "frozen_at": (now - timedelta(days=1)).isoformat(),
        "targets": {
            t: {"model": linear, "gate": "PASS", "radii": {"candidate": 10, "baseline": 10}}
            for t in ("margin", "total")
        },
    }
    (tmp_path / "model.json").write_bytes(canonical(model))
    (tmp_path / "evaluation.json").write_bytes(
        canonical({"model_sha256": digest(canonical(model))})
    )
    prior = [
        {
            "game_id": str(i),
            "season": now.year,
            "week": i + 1,
            "home": "A",
            "away": "B",
            "home_score": 20,
            "away_score": 17,
            "neutral_site": False,
            "game_type": "REG",
            "pace": {"A": 60, "B": 65},
            "kickoff": (now - timedelta(days=7 + i * 7)).isoformat(),
            "available_at": (now - timedelta(days=6 + i * 7)).isoformat(),
            "pace_available_at": (now - timedelta(days=6 + i * 7)).isoformat(),
        }
        for i in range(3)
    ]
    monkeypatch.setattr(experiment, "verify_freeze", lambda *a: {})
    monkeypatch.setattr(experiment, "historical_games", lambda *a: (prior, []))
    original = experiment.archive_forecast

    def archive(root, record, cfg):
        projection = json.loads(
            (root / "sources" / (record["source_sha256"] + ".json")).read_text()
        )
        assert projection["parent_view_sha256"] == digest(source)
        assert projection["games"] == json.loads(source)["games"]
        return original(root, record, cfg)

    monkeypatch.setattr(experiment, "archive_forecast", archive)
    result = experiment.forecast(cfg, tmp_path)
    assert result["archived_games"] == 1 and result["games"][0]["spread"]["home_spread"] == -3
    assert (official / "view.json").read_bytes() == source
    saved = read_archives(tmp_path)
    experiment.forecast(cfg, tmp_path)
    assert read_archives(tmp_path) == saved
    assert "home_spread" in (tmp_path / "forecast-sheet.csv").read_text()
    grading = experiment.grade(cfg, tmp_path)
    assert grading["exclusions"][0]["reason"] == "GAME_NOT_STARTED"


def test_gate_rejects_lower_mae_with_higher_rmse():
    rs = [
        {**r, "actual": 0, "baseline": 1, "candidate": c}
        for r, c in zip(rows()[:2], [0, 1.96], strict=True)
    ]
    report = paired_report(rs, config())
    assert report["candidate"]["mae"] < report["baseline"]["mae"]
    assert report["gate"] == "FAIL"


def test_concurrent_archive_cohort_limit(tmp_path):
    from concurrent.futures import ThreadPoolExecutor

    now = datetime(2027, 1, 1, tzinfo=UTC)
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(
            pool.map(
                lambda i: archive_forecast(tmp_path, record(now, str(i)), config(), lambda: now),
                range(8),
            )
        )
    assert results.count("ARCHIVED") == 2
    assert len(read_archives(tmp_path)) == 2


def test_grading_corrections_exclusions_and_failed_target(tmp_path, monkeypatch):
    import season_points_experiment as experiment
    from week1_live import canonical, digest

    now = datetime.now(UTC)
    kick = now - timedelta(days=2)
    spec = {
        "candidate": 4,
        "baseline": 7,
        "tracking_eligible": True,
        "interval": [-1, 9],
        "baseline_interval": [2, 12],
    }
    saved = {
        "game_id": "g",
        "home": "A",
        "away": "B",
        "season": now.year,
        "week": 1,
        "kickoff": kick.isoformat(),
        "neutral_site": False,
        "schedule_version": "s1",
        "archived_at": (kick - timedelta(hours=1)).isoformat(),
        "targets": {"margin": spec, "total": {**spec, "tracking_eligible": False}},
    }
    saved["archive_id"] = digest(canonical(saved))
    (tmp_path / "forecasts").mkdir()
    (tmp_path / "forecasts" / (saved["archive_id"] + ".json")).write_bytes(canonical(saved))
    official = tmp_path / "official"
    official.mkdir()
    outcome = {
        "status": "FINAL",
        "home_score": 20,
        "away_score": 17,
        "version": 1,
        "observed_at": (now - timedelta(minutes=1)).isoformat(),
    }
    game = {
        k: saved[k]
        for k in ("game_id", "home", "away", "neutral_site", "kickoff", "schedule_version")
    }
    cfg = {**config(), "version": "test", "season_root": str(official)}
    monkeypatch.setattr(experiment, "verify_freeze", lambda *a: {})
    (official / "view.json").write_bytes(canonical({"games": [{**game, "outcomes": [outcome]}]}))
    first = experiment.grade(cfg, tmp_path)
    assert first["targets"]["margin"]["candidate"]["mae"] == 1
    assert first["targets"]["total"]["n"] == 0
    card = next((tmp_path / "scorecards").glob("*.json"))
    original = card.read_bytes()
    correction = {**outcome, "version": 2, "home_score": 21}
    (official / "view.json").write_bytes(
        canonical({"games": [{**game, "outcomes": [outcome, correction]}]})
    )
    second = experiment.grade(cfg, tmp_path)
    assert second["targets"]["margin"]["candidate"]["mae"] == 0
    assert (
        card.read_bytes() == original and len(list((tmp_path / "scorecards").glob("*.json"))) == 2
    )
    (official / "view.json").write_bytes(
        canonical({"games": [{**game, "schedule_version": "s2", "outcomes": [correction]}]})
    )
    assert experiment.grade(cfg, tmp_path)["exclusions"][0]["reason"] == "SCHEDULE_CHANGED"
