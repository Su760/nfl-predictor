"""Cutoff and lineage checks for prospective QB-change collection."""

import io
import json
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import polars as pl

sys.path.insert(0, str(Path(__file__).parents[1] / "ops"))
from season_live import canonical, digest, journal, publish, stamp, write_once
from season_qb_evidence import archive_t60


def _setup(tmp_path):
    at = datetime(2030, 9, 22, 19, tzinfo=UTC)
    kickoff = at + timedelta(hours=1)
    earlier = at - timedelta(days=7)
    inputs = {
        "expected_qb": {
            "status": "AVAILABLE", "captured_at": stamp(at - timedelta(minutes=2)),
            "source_updated_at": stamp(at - timedelta(hours=2)),
            "raw_sha256": "d" * 64, "source_url": "https://example.test/depth",
            "data": {
                "ATL": {"gsis_id": "qb-a", "player_name": "New QB"},
                "PIT": {"gsis_id": "qb-p", "player_name": "PIT QB"},
            },
        },
        "injuries": {
            "status": "AVAILABLE", "captured_at": stamp(at - timedelta(minutes=2)),
            "data": [{"team": "ATL", "player": "New QB", "game_status": "Questionable"}],
        },
        "inactives": {
            "status": "MISSING", "reason": "REPORT_NOT_PUBLISHED",
            "captured_at": stamp(at - timedelta(minutes=2)), "data": [],
        },
    }
    game = {
        "game_id": "2030_03_ATL_PIT", "season": 2030, "week": 3,
        "home": "PIT", "away": "ATL", "kickoff": stamp(kickoff),
        "schedule_version": "schedule-1", "status": "STATUS_SCHEDULED",
        "inputs": inputs,
    }
    previous = [
        {
            "game_id": gid, "season": 2030, "week": 2,
            "home": home, "away": away, "kickoff": stamp(earlier),
            "outcomes": [{"status": "FINAL", "observed_at": stamp(earlier + timedelta(hours=4)),
                          "version": 1}],
        }
        for gid, home, away in (
            ("2030_02_ATL_GB", "GB", "ATL"),
            ("2030_02_CLE_PIT", "PIT", "CLE"),
        )
    ]
    stats = pl.DataFrame([
        {"season": 2030, "week": 2, "recent_team": team, "opponent_team": opponent,
         "position": "QB", "season_type": "REG", "player_id": player,
         "player_name": player, "attempts": attempts}
        for team, opponent, player, attempts in (
            ("ATL", "GB", "old-qb", 25), ("ATL", "GB", "qb-a", 5),
            ("PIT", "CLE", "qb-p", 31),
        )
    ])
    buffer = io.BytesIO()
    stats.write_parquet(buffer)
    raw = buffer.getvalue()
    raw_hash = digest(raw)
    write_once(tmp_path / "raw" / f"{raw_hash}.parquet", raw)
    artifact = {
        "state_raw_sha256": raw_hash, "data_as_of": stamp(at - timedelta(seconds=5)),
        "state_source_components": [{"captured_at": stamp(at - timedelta(seconds=5)),
                                     "source_url": "https://example.test/stats",
                                     "raw_sha256": raw_hash, "source_last_modified": None}],
    }
    artifact_id = journal(tmp_path / "shadow", "artifacts", artifact)
    baseline = {
        "game_id": game["game_id"], "home": "PIT", "away": "ATL",
        "kickoff": game["kickoff"], "schedule_version": game["schedule_version"],
        "origin": "T60", "generated_at": stamp(at - timedelta(seconds=1)),
        "status": "VALID", "role": "fallback", "model_version": "elo-season-v1",
        "p_home": 0.6, "p_away": 0.39, "p_tie": 0.01,
    }
    deadline = at + timedelta(minutes=10)
    assert publish(tmp_path, baseline, deadline, lambda: at)
    from season_live import all_records
    saved_baseline = all_records(tmp_path, game["game_id"])[0]
    challenger = {
        **baseline, "role": "challenger", "model_version": "qb-test",
        "model_id": artifact_id, "model_definition_sha256": "m" * 64,
        "paired_baseline": saved_baseline,
    }
    assert publish(tmp_path / "shadow/qb-test", challenger, deadline, lambda: at)
    cfg = {
        "season": 2030, "origin_seconds": {"T60": 3600},
        "origin_window_seconds": 600, "team_aliases": {},
        "shadow_models": [{"name": "qb-test", "kind": "qb"}],
    }
    view = {"games": [*previous, game],
            "model": {"model_state_sha256": "state", "policy_sha256": "policy"}}
    return at, view, cfg, artifact_id


def test_archives_genuine_t60_qb_change_evidence_with_forecast_links(tmp_path):
    at, view, cfg, artifact_id = _setup(tmp_path)
    first = archive_t60(view, tmp_path, cfg, at, {"qb-test": {"artifact_id": artifact_id}})
    assert len(first["saved"]) == 1
    path = tmp_path / "shadow/qb-change-evidence" / f"{first['saved'][0]}.json"
    record = json.loads(path.read_text())
    assert digest(canonical(record)) == path.stem
    assert record["teams"]["ATL"]["expected_qb_id"] == "qb-a"
    assert record["teams"]["ATL"]["confirmed_starter_id"] is None
    assert record["expected_qb_source"]["source_published_at"] is None
    assert record["expected_qb_source"]["captured_at"] != record["expected_qb_source"]["source_updated_at"]
    assert record["qb_stats_source"]["provider_publication_at"] is None
    assert record["qb_stats_source"]["raw_sha256"]
    prior = record["teams"]["ATL"]["prior_games"]
    assert [(row["game_id"], row["game_date"], row["team_qb_pass_attempts"]) for row in prior] == [
        ("2030_02_ATL_GB", "2030-09-15", 30)
    ]
    assert {row["player_id"]: row["pass_attempts"] for row in prior[0]["quarterbacks"]} == {
        "old-qb": 25, "qb-a": 5
    }
    assert "EXPECTED_QB_INJURY_UNCERTAIN_OR_OUT" in record["teams"]["ATL"]["uncertainty"]
    assert "INACTIVES_MISSING" in record["teams"]["ATL"]["uncertainty"]
    assert record["forecasts"]["official"]["revision_id"]
    assert record["forecasts"]["qb-test"]["paired_baseline_revision_id"] == record["forecasts"]["official"]["revision_id"]
    assert record["forecasts"]["qb-test"]["model_id"] == artifact_id
    policy = json.loads((tmp_path / "shadow/qb-change-policy.json").read_text())
    assert record["policy_sha256"] == digest(canonical(policy))
    assert policy["origin"] == "T60"
    assert archive_t60(view, tmp_path, cfg, at, {"qb-test": {"artifact_id": artifact_id}})["saved"] == first["saved"]
    assert len(list(path.parent.glob("*.json"))) == 1


def test_rejects_future_source_and_does_not_backfill_missed_t60(tmp_path):
    at, view, cfg, artifact_id = _setup(tmp_path)
    game = view["games"][-1]
    game["inputs"]["expected_qb"]["captured_at"] = stamp(at + timedelta(seconds=1))
    saved = archive_t60(view, tmp_path, cfg, at, {"qb-test": {"artifact_id": artifact_id}})
    record = json.loads((tmp_path / "shadow/qb-change-evidence" / f"{saved['saved'][0]}.json").read_text())
    assert record["expected_qb_source"]["reason"] == "CAPTURE_OUTSIDE_CUTOFF"
    assert record["teams"]["ATL"]["expected_qb_id"] is None
    assert archive_t60(view, tmp_path, cfg, at + timedelta(minutes=11),
                       {"qb-test": {"artifact_id": artifact_id}})["saved"] == []
    assert len(list((tmp_path / "shadow/qb-change-evidence").glob("*.json"))) == 1


def test_retracted_prior_final_is_missing_and_article_publication_is_distinct(tmp_path):
    at, view, cfg, artifact_id = _setup(tmp_path)
    view["games"][0]["outcomes"].append({
        "status": "UNRESOLVED", "observed_at": stamp(at - timedelta(minutes=1)),
        "version": 2,
    })
    view["games"][-1]["inputs"]["inactives"] = {
        "status": "AVAILABLE", "captured_at": stamp(at - timedelta(minutes=1)),
        "source_updated_at": stamp(at - timedelta(minutes=5)),
        "data": [{"team": "ATL", "player": "Other QB", "published_at": stamp(at - timedelta(minutes=10))}],
    }
    saved = archive_t60(view, tmp_path, cfg, at, {"qb-test": {"artifact_id": artifact_id}})
    record = json.loads((tmp_path / "shadow/qb-change-evidence" / f"{saved['saved'][0]}.json").read_text())
    assert record["teams"]["ATL"]["prior_games"] == []
    assert record["teams"]["ATL"]["missing_data_reasons"] == [
        "PRIOR_FINAL_UNAVAILABLE:2030_02_ATL_GB"
    ]
    assert record["inactives_source"]["source_published_at"] == stamp(at - timedelta(minutes=10))
    assert record["inactives_source"]["captured_at"] == stamp(at - timedelta(minutes=1))
