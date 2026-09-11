import importlib
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parents[1] / "ops"))
live = importlib.import_module("season_live")


def record(at, kick):
    return {
        "game_id": "game-1",
        "generated_at": live.stamp(at),
        "kickoff": live.stamp(kick),
        "p_home": 0.6,
        "p_away": 0.39,
        "p_tie": 0.01,
    }


def test_publication_after_kickoff_never_visible(tmp_path):
    kick = datetime(2030, 9, 10, tzinfo=UTC)
    assert not live.publish(tmp_path, record(kick, kick), kick, lambda: kick)
    assert live.all_records(tmp_path, "game-1") == []


def test_receipt_flush_crossing_deadline_never_visible(tmp_path, monkeypatch):
    kick = datetime(2030, 9, 10, tzinfo=UTC)
    clock = [kick - timedelta(seconds=1)]
    original = live.write_once

    def crossing(path, value):
        original(path, value)
        if str(path).endswith(".receipt.json"):
            clock[0] = kick

    monkeypatch.setattr(live, "write_once", crossing)
    assert not live.publish(tmp_path, record(clock[0], kick), kick, lambda: clock[0])
    assert live.all_records(tmp_path, "game-1") == []


def test_immutable_revisions_and_tamper_detection(tmp_path):
    at = datetime(2030, 9, 10, tzinfo=UTC)
    kick = at + timedelta(hours=1)
    r = record(at, kick)
    assert live.publish(tmp_path, r, kick, lambda: at)
    assert live.publish(tmp_path, r, kick, lambda: at)
    assert len(live.all_records(tmp_path, "game-1")) == 1
    path = tmp_path / "forecasts" / "game-1" / (live.digest(live.canonical(r)) + ".json")
    path.write_text("{}")
    with pytest.raises(ValueError, match="HASH_MISMATCH"):
        live.all_records(tmp_path, "game-1")


def test_schedule_version_change_excludes_old_forecast():
    assert (
        live.current_records(
            {
                "kickoff": "2030-09-10T01:00:00Z",
                "schedule_version": "new",
                "predictions": [
                    {"kickoff": "2030-09-10T00:00:00Z"},
                    {"kickoff": "2030-09-10T01:00:00Z", "schedule_version": "old"},
                ],
            }
        )
        == []
    )


def test_worker_runs_beyond_week_one_and_final_window():
    at = datetime(2030, 10, 10, tzinfo=UTC)
    cfg = {
        "check_seconds": 7200,
        "near_game_seconds": 7200,
        "near_game_check_seconds": 300,
        "result_poll_hours": 8,
        "origin_seconds": {"T72": 259200, "T60": 3600, "FINAL": 300},
        "origin_window_seconds": 600,
    }
    kick = at + timedelta(minutes=6)
    assert live.next_check(
        [{"kickoff": live.stamp(kick), "status": "STATUS_SCHEDULED"}], at, cfg
    ) == live.stamp(at + timedelta(minutes=1))
    assert live.origin_state(kick, "FINAL", kick, cfg)[0] == "MISSED"
    assert live.next_check([], at, cfg) == live.stamp(at + timedelta(hours=2))


def test_policy_is_frozen_and_not_backdated(tmp_path):
    first = datetime(2030, 9, 10, tzinfo=UTC)
    a = live.freeze_policy(tmp_path, first)
    assert live.freeze_policy(tmp_path, first + timedelta(days=1)) == a
    assert a["effective_at"] == live.stamp(first)


def fake_tick(monkeypatch, tmp_path, stale=False):
    import copy
    import tomllib

    from nfl_predictor.ratings.elo import EloRater

    cfg = tomllib.loads((Path(__file__).parents[1] / "configs/season_live.toml").read_text())
    cfg["legacy_data_root"] = str(tmp_path / "legacy")
    cfg["analysis_enabled"] = False
    cfg["postgame_enabled"] = False
    at = datetime(2030, 9, 10, tzinfo=UTC)
    kick = at + timedelta(days=2)
    policy = tomllib.loads(
        (Path(__file__).parents[1] / "configs/model_policy_v1.toml").read_text()
    )["elo"]
    rater = EloRater(**policy)
    rater.ratings = {"CHI": 1500.0, "CAR": 1500.0}
    monkeypatch.setattr(live, "code_identity", lambda: {"code_sha": "a" * 40})
    monkeypatch.setattr(
        live,
        "model_for_current_results",
        lambda *a: (rater, {"p_tie": 0.01, "model_state_sha256": "state"}),
    )
    captured = at - timedelta(hours=3) if stale else at
    source = {
        "rows": [],
        "checks": {
            k: {"status": "AVAILABLE", "captured_at": live.stamp(captured)}
            for k in ("nflverse_history", "schedule")
        },
        "games": [
            {
                "game_id": "2030_01_CHI_CAR",
                "season": 2030,
                "week": 1,
                "home": "CAR",
                "away": "CHI",
                "kickoff": live.stamp(kick),
                "schedule_version": "v1",
                "venue": {},
                "neutral_site": False,
                "status": "STATUS_SCHEDULED",
                "inputs": {"qb": {"status": "MISSING", "data": None}},
            }
        ],
    }
    return cfg, at, lambda *a: copy.deepcopy(source)


def test_duplicate_checks_do_not_create_duplicate_revisions(tmp_path, monkeypatch):
    cfg, at, fetcher = fake_tick(monkeypatch, tmp_path)
    first = live.run_once(cfg, tmp_path, clock=lambda: at, fetcher=fetcher)
    second = live.run_once(cfg, tmp_path, clock=lambda: at + timedelta(seconds=1), fetcher=fetcher)
    assert len(first["games"][0]["predictions"]) == 1
    assert first["games"][0]["predictions"] == second["games"][0]["predictions"]


def test_stale_required_sources_block_before_any_forecast(tmp_path, monkeypatch):
    cfg, at, fetcher = fake_tick(monkeypatch, tmp_path, stale=True)
    with pytest.raises(ValueError, match="REQUIRED_INPUT_STALE"):
        live.run_once(cfg, tmp_path, clock=lambda: at, fetcher=fetcher)
    assert not (tmp_path / "forecasts").exists()


def test_schedule_and_result_journals_recover_without_view(tmp_path):
    value = {"game_id": "g", "observed_at": "2030-09-10T00:00:00Z", "version": 1, "status": "FINAL"}
    live.journal(tmp_path, "outcomes", value)
    correction = {
        **value,
        "observed_at": "2030-09-11T00:00:00Z",
        "version": 2,
        "status": "UNRESOLVED",
    }
    live.journal(tmp_path, "outcomes", correction)
    assert live.recovered_history(tmp_path, "outcomes", "g") == [value, correction]


def test_final_result_before_kickoff_is_rejected(tmp_path, monkeypatch):
    cfg, at, fetcher = fake_tick(monkeypatch, tmp_path)

    def invalid(*args):
        source = fetcher(*args)
        source["games"][0]["result"] = {"status": "FINAL", "home_score": 21, "away_score": 10}
        return source

    with pytest.raises(ValueError, match="FINAL_RESULT_BEFORE_KICKOFF"):
        live.run_once(cfg, tmp_path, clock=lambda: at, fetcher=invalid)
    assert not (tmp_path / "forecasts").exists()


def test_evidence_tampering_cannot_redirect_record_reads(tmp_path):
    at = datetime(2030, 9, 10, tzinfo=UTC)
    kick = at + timedelta(hours=1)
    live.publish(tmp_path, record(at, kick), kick, lambda: at)
    path = next((tmp_path / "forecasts" / "game-1").glob("*.evidence.json"))
    proof = live.read(path)
    proof["record_sha256"] = "../../private"
    path.write_bytes(live.canonical(proof))
    with pytest.raises(ValueError, match="INVALID_PUBLICATION_EVIDENCE"):
        live.all_records(tmp_path, "game-1")


def test_backward_publication_clock_rejects_forecast(tmp_path):
    at = datetime(2030, 9, 10, tzinfo=UTC)
    kick = at + timedelta(hours=1)
    assert not live.publish(tmp_path, record(at, kick), kick, lambda: at - timedelta(seconds=1))
    assert not live.all_records(tmp_path, "game-1")


def test_missing_view_and_source_game_does_not_erase_coverage(tmp_path, monkeypatch):
    cfg, at, fetcher = fake_tick(monkeypatch, tmp_path)
    live.run_once(cfg, tmp_path, clock=lambda: at, fetcher=fetcher)
    (tmp_path / "view.json").unlink()

    def missing(*args):
        source = fetcher(*args)
        source["games"] = []
        return source

    restored = live.run_once(
        cfg, tmp_path, clock=lambda: at + timedelta(seconds=1), fetcher=missing
    )
    assert len(restored["games"]) == 1
    assert restored["games"][0]["status"] == "SOURCE_EVENT_MISSING"
    assert restored["scorecards"]["summary"]["season"]["games"] == 1


def test_original_pregame_record_survives_equivalent_kickoff_format():
    original = {"kickoff": "2026-09-11T00:35:00Z", "generated_at": "2026-09-11T00:13:04Z"}
    assert live.current_records(
        {"kickoff": "2026-09-11T00:35Z", "schedule_version": "current", "predictions": [original]}
    ) == [original]


def test_venue_metadata_migration_preserves_actual_schedule_but_not_reschedule():
    old = {"home":"LA", "away":"SF", "neutral_site":True, "venue":{"id":"123"},
           "kickoff":"2026-09-11T00:35Z", "schedule_version":"recorded"}
    current = {**old, "kickoff":"2026-09-11T00:35:00Z", "venue":{"id":"123", "fullName":"New display spelling"}, "schedule_version":"new-schema"}
    live.preserve_schedule_identity(current, old)
    assert current["schedule_version"] == "recorded"
    current.update(kickoff="2026-09-12T00:35:00Z", schedule_version="rescheduled")
    live.preserve_schedule_identity(current, old)
    assert current["schedule_version"] == "rescheduled"
    current.update(kickoff=old["kickoff"], venue={"id":"456"}, schedule_version="relocated")
    live.preserve_schedule_identity(current, old)
    assert current["schedule_version"] == "relocated"


def test_live_final_correction_retraction_restart_keeps_forecasts(tmp_path, monkeypatch):
    cfg, at, base_fetch = fake_tick(monkeypatch, tmp_path)
    cfg.pop("simulation_config", None)
    initial = live.run_once(cfg, tmp_path, clock=lambda: at, fetcher=base_fetch)
    original = initial["games"][0]["predictions"]
    after = at + timedelta(days=3)
    result = {"status":"FINAL", "home_score":21, "away_score":10}

    def source(*args):
        value = base_fetch(*args)
        for check in value["checks"].values():
            check["captured_at"] = live.stamp(after)
        value["games"][0]["status"] = "STATUS_FINAL" if result else "STATUS_IN_PROGRESS"
        if result:
            value["games"][0]["result"] = dict(result)
        return value

    first = live.run_once(cfg, tmp_path, clock=lambda: after, fetcher=source)
    assert first["scorecards"]["summary"]["season"]["winner_accuracy_denominator"] == 1
    result.update(home_score=10, away_score=21)
    corrected = live.run_once(cfg, tmp_path, clock=lambda: after, fetcher=source)
    assert corrected["scorecards"]["summary"]["season"]["correct"] != first["scorecards"]["summary"]["season"]["correct"]
    result.clear()
    retracted = live.run_once(cfg, tmp_path, clock=lambda: after, fetcher=source)
    assert retracted["scorecards"]["summary"]["season"]["winner_accuracy_denominator"] == 0
    (tmp_path / "view.json").unlink()
    result.update(status="FINAL", home_score=10, away_score=21)
    restored = live.run_once(cfg, tmp_path, clock=lambda: after, fetcher=source)
    game = restored["games"][0]
    assert game["predictions"] == original
    assert [o["version"] for o in game["outcomes"]] == [1, 2, 3, 4]
    assert [o["status"] for o in game["outcomes"]] == ["FINAL", "FINAL", "UNRESOLVED", "FINAL"]
    assert restored["scorecards"]["summary"]["season"]["winner_accuracy_denominator"] == 1


def test_new_forecast_carries_saved_explanation_and_never_rewrites_at_kickoff(tmp_path, monkeypatch):
    import copy
    cfg, at, fetcher = fake_tick(monkeypatch, tmp_path)
    cfg["analysis_enabled"] = True
    original_model = live.model_for_current_results
    def model(*args):
        rater, proof = original_model(*args)
        proof.update(ratings=dict(rater.ratings), policy_sha256=live.digest((live.CODE_ROOT / cfg["model_policy"]).read_bytes()))
        return rater, proof
    monkeypatch.setattr(live,"model_for_current_results",model)
    first=live.run_once(cfg,tmp_path,clock=lambda:at,fetcher=fetcher)
    saved=copy.deepcopy(first["games"][0]["predictions"])
    assert saved[0]["explanation"]["status"] == "VERIFIED"
    assert saved[0]["explanation"]["provenance"] == "PREGAME"
    assert saved[0]["explanation"]["created_at"] == saved[0]["generated_at"]
    after=at+timedelta(days=2)
    def changed(*args):
        source=fetcher(*args)
        for check in source["checks"].values(): check["captured_at"]=live.stamp(after)
        source["games"][0]["inputs"]={"qb":{"status":"AVAILABLE","data":"new information at kickoff"}}
        return source
    second=live.run_once(cfg,tmp_path,clock=lambda:after,fetcher=changed)
    assert second["games"][0]["predictions"] == saved
