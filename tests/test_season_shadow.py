import copy
import json
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parents[1] / "ops"))
import season_shadow as shadow
from season_live import all_records, canonical, digest, stamp
from season_scoring import score_season


def fixture(tmp_path):
    at = datetime(2030, 9, 1, tzinfo=UTC)
    kick = at + timedelta(hours=2)
    baseline = {
        "game_id": "2030_01_ATL_PIT",
        "home": "PIT",
        "away": "ATL",
        "generated_at": stamp(at - timedelta(minutes=1)),
        "published_at": stamp(at - timedelta(minutes=1)),
        "kickoff": stamp(kick),
        "schedule_version": "s1",
        "origin": "ON_DEMAND",
        "role": "fallback",
        "status": "VALID",
        "model_version": "elo-season-v1",
        "revision_id": "original",
        "p_home": 0.6,
        "p_away": 0.39,
        "p_tie": 0.01,
    }
    game = {
        "game_id": baseline["game_id"],
        "home": "PIT",
        "away": "ATL",
        "season": 2030,
        "week": 1,
        "kickoff": stamp(kick),
        "schedule_version": "s1",
        "status": "STATUS_SCHEDULED",
        "prediction": baseline,
        "predictions": [baseline],
        "outcomes": [],
        "inputs": {"expected_qb": {"captured_at": stamp(at), "status": "MISSING"}},
    }
    policy = {"effective_at": stamp(at - timedelta(days=1))}
    view = {
        "games": [game],
        "model": {
            "model_version": "elo-season-v1",
            "model_state_sha256": "state",
            "policy_sha256": "policy",
        },
        "sources": {},
        "scoring_policy": policy,
    }
    artifact = tmp_path / "artifact.json"
    artifact.write_bytes(canonical({"created_at": stamp(at), "production_policy_sha256": "policy"}))
    cfg = {
        "zero_dollar_mode": True,
        "allow_paid_usage": False,
        "slate_days": 8,
        "origin_seconds": {"T72": 259200, "T60": 3600, "FINAL": 300},
        "origin_window_seconds": 600,
        "shadow_models": [
            {
                "name": "qb-test",
                "kind": "qb",
                "artifact_path": str(artifact),
                "artifact_sha256": digest(artifact.read_bytes()),
                "prospective_start": stamp(at),
            }
        ],
    }
    return at, kick, view, cfg


def candidate(*args):
    return {
        "status": "VALID",
        "p_home": 0.4,
        "p_away": 0.59,
        "p_tie": 0.01,
        "evidence": {"known": "before kickoff"},
    }


def enable_live_comparison(cfg, at):
    cfg["live_model_comparison"] = {
        "schema_version": "live-model-comparison-v1",
        "collection_start": stamp(at),
        "primary_horizon": "T60",
        "secondary_horizons": ["T72"],
        "accuracy_interval": "wilson_95",
        "small_sample_non_ties": 30,
    }


def test_live_comparison_policy_freezes_models_cutoffs_outcomes_and_metrics(tmp_path):
    at, _kick, _view, cfg = fixture(tmp_path)
    enable_live_comparison(cfg, at)
    policy = shadow.freeze_live_comparison_policy(tmp_path, cfg)
    saved = json.loads((tmp_path / "shadow/comparison-policy.json").read_text())

    assert saved == policy
    assert policy["collection_start"] == stamp(at)
    assert policy["primary_horizon"] == "T60"
    assert policy["secondary_horizons"] == ["T72"]
    assert policy["horizon_seconds"] == {"T60": 3600, "T72": 259200}
    assert policy["origin_window_seconds"] == 600
    assert policy["models"] == [
        {
            "name": "qb-test",
            "kind": "qb",
            "artifact_sha256": cfg["shadow_models"][0]["artifact_sha256"],
            "prospective_start": stamp(at),
        }
    ]
    assert policy["outcomes"] == "latest observed official FINAL; retractions unresolved"
    assert policy["ties"] == (
        "excluded from winner accuracy; included in three-outcome Brier and log loss"
    )
    assert policy["brier_convention"] == "sum of three squared outcome errors; range 0-2"
    assert policy["promotion"] == "manual review only; no automatic production rewrite"

    cfg["live_model_comparison"]["collection_start"] = stamp(at + timedelta(seconds=1))
    with pytest.raises(ValueError, match="IMMUTABLE_RECORD_CONFLICT"):
        shadow.freeze_live_comparison_policy(tmp_path, cfg)


def test_live_comparison_scores_identical_games_and_exposes_missing_input_coverage(tmp_path):
    at, kick, view, cfg = fixture(tmp_path)
    enable_live_comparison(cfg, at)
    due = kick - timedelta(hours=1)
    shadow.run_shadow(view, tmp_path, cfg, lambda: due, predictor=candidate)
    first = view["games"][0]
    records = [
        row
        for row in all_records(tmp_path / "shadow/qb-test", first["game_id"])
        if row["origin"] == "T60"
    ]
    assert len(records) == 1

    missing = copy.deepcopy(first)
    missing.update(game_id="2030_01_BUF_NYJ", home="NYJ", away="BUF")
    first["outcomes"] = missing["outcomes"] = [
        {
            "version": 1,
            "observed_at": stamp(kick + timedelta(hours=4)),
            "status": "FINAL",
            "home_score": 7,
            "away_score": 20,
        }
    ]
    policy = shadow.freeze_live_comparison_policy(tmp_path, cfg)
    states = {
        missing["game_id"]: {
            "qb-test": {"status": "FALLBACK", "reason": "QB_INJURY_EVIDENCE_UNAVAILABLE"}
        }
    }
    card = shadow.score_shadow(
        [first, missing],
        {"qb-test": records},
        kick + timedelta(hours=5),
        {"qb-test": at},
        policy=policy,
        states=states,
    )["qb-test"]
    t60 = card["horizons"]["T60"]

    assert card["collection_start"] == stamp(at)
    assert card["primary_horizon"] == "T60"
    assert t60["operational_coverage"] == {
        "eligible_games": 2,
        "forecasted": 1,
        "settled": 1,
        "awaiting_result": 0,
        "scheduled": 0,
        "due": 0,
        "missed": 1,
        "missing_predictions": [
            {
                "game_id": missing["game_id"],
                "reason": "QB_INJURY_EVIDENCE_UNAVAILABLE",
            }
        ],
    }
    paired = t60["paired_comparison"]
    assert paired["n"] == 1 and paired["game_ids"] == [first["game_id"]]
    assert paired["shadow"]["correct"] == 1
    assert paired["baseline"]["correct"] == 0
    assert len(paired["shadow"]["accuracy_interval_95"]) == 2
    assert paired["small_sample"] is True
    assert paired["brier_convention"] == "sum of three squared outcome errors; range 0-2"


def test_shadow_is_immutable_independent_and_duplicate_safe(tmp_path):
    at, kick, view, cfg = fixture(tmp_path)
    before = copy.deepcopy(view)
    a = shadow.run_shadow(view, tmp_path, cfg, lambda: at, predictor=candidate)
    b = shadow.run_shadow(
        view, tmp_path, cfg, lambda: at + timedelta(seconds=1), predictor=candidate
    )
    assert (
        a["models"]["qb-test"]["saved_forecasts"] == b["models"]["qb-test"]["saved_forecasts"] == 1
    )
    assert view == before
    records = all_records(tmp_path / "shadow/qb-test", view["games"][0]["game_id"])
    assert records[0]["paired_baseline"] == view["games"][0]["prediction"]
    assert records[0]["role"] == "challenger"
    view["games"][0]["outcomes"] = [
        {
            "version": 1,
            "observed_at": stamp(kick + timedelta(hours=4)),
            "status": "FINAL",
            "home_score": 7,
            "away_score": 20,
        }
    ]
    combined = copy.deepcopy(view["games"])
    combined[0]["predictions"] += records
    assert (
        score_season(combined, kick + timedelta(hours=5), view["scoring_policy"])["games"][0][
            "prediction_id"
        ]
        == "original"
    )
    cards = shadow.score_shadow(
        view["games"], {"qb-test": records}, kick + timedelta(hours=5), {"qb-test": at}
    )
    c = cards["qb-test"]["horizons"]["LATEST"]
    assert c["summary"]["correct"] == 1
    assert c["paired_comparison"]["baseline"]["correct"] == 0 and c["paired_comparison"]["n"] == 1
    assert c["paired_comparison"]["delta_log_loss"] < 0


def test_missing_qb_records_gap_and_prospective_evidence_not_fake_adjustment(tmp_path):
    at, _kick, view, cfg = fixture(tmp_path)
    out = shadow.run_shadow(
        view,
        tmp_path,
        cfg,
        lambda: at,
        predictor=lambda *args: {"status": "FALLBACK", "reason": "QB_MISSING"},
    )
    assert out["games"][view["games"][0]["game_id"]]["qb-test"]["status"] == "FALLBACK"
    assert out["models"]["qb-test"]["saved_forecasts"] == 0
    assert len(list((tmp_path / "shadow/prospective-inputs").glob("*.json"))) == 1


def test_kickoff_crossing_and_future_inputs_never_publish(tmp_path):
    at, kick, view, cfg = fixture(tmp_path)
    times = iter([at, kick, kick, kick])
    out = shadow.run_shadow(view, tmp_path, cfg, lambda: next(times, kick), predictor=candidate)
    assert out["models"]["qb-test"]["saved_forecasts"] == 0
    view["games"][0]["inputs"]["expected_qb"]["captured_at"] = stamp(at + timedelta(minutes=1))
    out = shadow.run_shadow(view, tmp_path, cfg, lambda: at, predictor=candidate)
    assert out["models"]["qb-test"]["saved_forecasts"] == 0
    assert out["games"][view["games"][0]["game_id"]]["evidence"]["status"] == "BLOCKED"


def test_changed_schedule_and_corrections_keep_shadow_history(tmp_path):
    at, kick, view, cfg = fixture(tmp_path)
    shadow.run_shadow(view, tmp_path, cfg, lambda: at, predictor=candidate)
    g = view["games"][0]
    g["kickoff"] = stamp(kick + timedelta(hours=1))
    g["schedule_version"] = "s2"
    g["prediction"] = {
        **g["prediction"],
        "kickoff": g["kickoff"],
        "schedule_version": "s2",
        "revision_id": "rescheduled",
    }
    out = shadow.run_shadow(
        view, tmp_path, cfg, lambda: at + timedelta(minutes=1), predictor=candidate
    )
    assert out["models"]["qb-test"]["saved_forecasts"] == 2
    records = all_records(tmp_path / "shadow/qb-test", g["game_id"])
    final = kick + timedelta(hours=5)
    g["outcomes"] = [
        {
            "version": 1,
            "observed_at": stamp(final),
            "status": "FINAL",
            "home_score": 10,
            "away_score": 10,
        }
    ]
    c = shadow.score_shadow([g], {"qb-test": records}, final, {"qb-test": at})["qb-test"][
        "horizons"
    ]["LATEST"]
    assert c["summary"]["ties"] == 1 and c["paired_comparison"]["delta_log_loss"] == 0
    g["outcomes"].append(
        {"version": 2, "observed_at": stamp(final + timedelta(seconds=1)), "status": "UNRESOLVED"}
    )
    c = shadow.score_shadow(
        [g], {"qb-test": records}, final + timedelta(seconds=1), {"qb-test": at}
    )["qb-test"]["horizons"]["LATEST"]
    assert c["summary"]["settled"] == 0 and c["paired_comparison"]["n"] == 0


def test_artifact_hash_mismatch_and_zero_dollar_guard(tmp_path):
    at, _, view, cfg = fixture(tmp_path)
    Path(cfg["shadow_models"][0]["artifact_path"]).write_text(json.dumps({"tampered": True}))
    out = shadow.run_shadow(view, tmp_path, cfg, lambda: at, predictor=candidate)
    assert out["models"]["qb-test"]["reason"] == "SHADOW_ARTIFACT_HASH_MISMATCH"
    with pytest.raises(ValueError, match="ZERO_DOLLAR_GUARD"):
        shadow.run_shadow(
            view, tmp_path, {**cfg, "allow_paid_usage": True}, lambda: at, predictor=candidate
        )


def test_future_artifact_and_wrong_calibration_baseline_fail_closed(tmp_path):
    at, _, view, cfg = fixture(tmp_path)
    spec = cfg["shadow_models"][0]
    path = Path(spec["artifact_path"])
    path.write_bytes(canonical({"created_at": stamp(at + timedelta(seconds=1))}))
    spec["artifact_sha256"] = digest(path.read_bytes())
    out = shadow.run_shadow(view, tmp_path, cfg, lambda: at, predictor=candidate)
    assert out["models"]["qb-test"]["reason"] == "SHADOW_ARTIFACT_FROM_FUTURE"
    path.write_bytes(canonical({"created_at": stamp(at), "production_policy_sha256": "different"}))
    spec["artifact_sha256"] = digest(path.read_bytes())
    spec["kind"] = "calibration"
    view["model"]["policy_sha256"] = "production-policy"
    out = shadow.run_shadow(view, tmp_path, cfg, lambda: at, predictor=candidate)
    assert out["models"]["qb-test"]["reason"] == "SHADOW_BASELINE_POLICY_MISMATCH"


def test_failed_publication_does_not_display_unsaved_candidate(tmp_path, monkeypatch):
    at, _, view, cfg = fixture(tmp_path)
    monkeypatch.setattr(shadow, "publish", lambda *args: False)
    out = shadow.run_shadow(view, tmp_path, cfg, lambda: at, predictor=candidate)
    state = out["games"][view["games"][0]["game_id"]]["qb-test"]
    assert state["status"] == "BLOCKED"
    assert state["reason"] == "SHADOW_PUBLICATION_DEADLINE_MISSED"
    assert "p_home" not in state and state["latest_saved"] is None
    assert state["saved_revisions"] == 0


def test_model_name_cannot_silently_change_artifact_or_experiment_start(tmp_path):
    at, _, view, cfg = fixture(tmp_path)
    shadow.run_shadow(view, tmp_path, cfg, lambda: at, predictor=candidate)
    spec = cfg["shadow_models"][0]
    original = copy.deepcopy(spec)
    path = Path(spec["artifact_path"])
    path.write_bytes(canonical({"created_at": stamp(at), "new_model": True}))
    spec["artifact_sha256"] = digest(path.read_bytes())
    out = shadow.run_shadow(
        view, tmp_path, cfg, lambda: at + timedelta(seconds=1), predictor=candidate
    )
    assert out["models"]["qb-test"]["reason"] == "SHADOW_EXPERIMENT_CHANGED_REQUIRES_NEW_MODEL_NAME"
    assert out["models"]["qb-test"]["saved_forecasts"] == 1
    spec.update(original)
    spec["prospective_start"] = stamp(at - timedelta(days=1))
    out = shadow.run_shadow(
        view, tmp_path, cfg, lambda: at + timedelta(seconds=1), predictor=candidate
    )
    assert out["scorecards"]["qb-test"]["prospective_start"] == stamp(at)
    assert out["models"]["qb-test"]["reason"] == "SHADOW_EXPERIMENT_CHANGED_REQUIRES_NEW_MODEL_NAME"


@pytest.mark.parametrize("timestamp", ["source_updated_at", "published_at"])
def test_future_source_publication_never_enters_shadow_evidence(tmp_path, timestamp):
    at, _kick, view, cfg = fixture(tmp_path)
    view["games"][0]["inputs"]["expected_qb"][timestamp] = stamp(at + timedelta(seconds=1))
    out = shadow.run_shadow(view, tmp_path, cfg, lambda: at, predictor=candidate)
    assert out["models"]["qb-test"]["saved_forecasts"] == 0
    assert out["games"][view["games"][0]["game_id"]]["evidence"]["status"] == "BLOCKED"
    assert not list((tmp_path / "shadow/prospective-inputs").glob("*.json"))


def test_qb_adapter_passes_frozen_callable_clock(monkeypatch):
    import season_qb

    at = datetime(2030, 9, 1, tzinfo=UTC)

    def predict(baseline, game, artifact, clock):
        assert clock() == at
        return {"status": "FALLBACK"}

    monkeypatch.setattr(season_qb, "predict_qb", predict)
    assert shadow._candidate("qb", {}, {}, {}, at)["status"] == "FALLBACK"


def test_shadow_evaluation_is_preserved_and_tampering_blocks(tmp_path):
    at, _, view, cfg = fixture(tmp_path)
    path = tmp_path / "evaluation.json"
    path.write_bytes(
        canonical(
            {
                "evaluation": {"known_benchmark": {"n": 272}},
                "evidence_grade": "C",
                "artifact_file_sha256": cfg["shadow_models"][0]["artifact_sha256"],
            }
        )
    )
    cfg["shadow_models"][0].update(
        evaluation_path=str(path), evaluation_sha256=digest(path.read_bytes())
    )
    out = shadow.run_shadow(view, tmp_path, cfg, lambda: at, predictor=candidate)
    evaluation = out["models"]["qb-test"]["historical_evaluation"]
    assert evaluation["results"]["known_benchmark"]["n"] == 272
    assert evaluation["promotion_eligible"] is False
    path.write_text("{}")
    out = shadow.run_shadow(view, tmp_path, cfg, lambda: at, predictor=candidate)
    assert out["models"]["qb-test"]["reason"] == "SHADOW_EVALUATION_HASH_MISMATCH"
    assert out["models"]["qb-test"]["saved_forecasts"] == 1


def test_qb_state_refresh_uses_verified_sources_and_observed_finals(tmp_path, monkeypatch):
    import season_probability
    import season_qb

    at, _, view, cfg = fixture(tmp_path)
    g = copy.deepcopy(view["games"][0])
    g.update(
        game_id="prior",
        kickoff=stamp(at - timedelta(hours=5)),
        outcomes=[
            {
                "version": 1,
                "observed_at": stamp(at - timedelta(hours=1)),
                "status": "FINAL",
                "home_score": 10,
                "away_score": 7,
            }
        ],
    )
    view["games"].append(g)
    config = tmp_path / "runtime.toml"
    config.write_text(
        "zero_dollar_mode = true\nallow_paid_usage = false\nhistory_start = 2016\nknown_benchmark_season = 2025\nhistorical_result_delay_hours = 24\n"
    )
    source = tmp_path / "history.csv"
    source.write_text("verified history")
    spec = {
        "runtime_config_path": str(config),
        "runtime_config_sha256": digest(config.read_bytes()),
        "history_source_path": str(source),
        "history_source_sha256": digest(source.read_bytes()),
    }
    monkeypatch.setattr(
        season_probability,
        "historical_games",
        lambda *args: ([{"game_id": "historical", "game_type": "REG"}], []),
    )

    def refresh(artifact, runtime, root, finals, clock):
        assert runtime["qb_state_through_season"] == 2030
        assert runtime["qb_state_supplements"] == [{"season": 2030, "url": "https://github.com/current"}]
        assert [x["game_id"] for x in finals] == ["historical", "prior"]
        assert finals[-1]["outcome_version"] == 1
        assert artifact["coefficient"] == 0.2
        return {**artifact, "data_as_of": stamp(clock())}

    monkeypatch.setattr(season_qb, "refresh_state", refresh)
    result = shadow.refresh_qb_state(
        view,
        {
            "coefficient": 0.2,
            "lineage": {
                "runtime_config_sha256": spec["runtime_config_sha256"],
                "historical_source_sha256": spec["history_source_sha256"],
            },
        },
        spec,
        tmp_path,
        {**cfg, "season": 2030, "qb_state_supplements": [{"season": 2030, "url": "https://github.com/current"}]},
        lambda: at,
    )
    assert result["data_as_of"] == stamp(at)
    import subprocess

    def failed_fetch(*args):
        raise subprocess.CalledProcessError(28, ["curl", "public-stats-source"])

    monkeypatch.setattr(season_qb, "refresh_state", failed_fetch)
    artifact = {
        "coefficient": 0.2,
        "created_at": stamp(at),
        "production_policy_sha256": "policy",
        "lineage": {
            "runtime_config_sha256": spec["runtime_config_sha256"],
            "historical_source_sha256": spec["history_source_sha256"],
        },
    }
    with pytest.raises(ValueError, match="QB_STATE_REFRESH_FAILED: CalledProcessError"):
        shadow.refresh_qb_state(view, artifact, spec, tmp_path, {**cfg, "season": 2030}, lambda: at)
    artifact_path = Path(cfg["shadow_models"][0]["artifact_path"])
    artifact_path.write_bytes(canonical(artifact))
    cfg["shadow_models"][0].update(**spec, artifact_sha256=digest(artifact_path.read_bytes()))
    before = copy.deepcopy(view)
    output = shadow.run_shadow(view, tmp_path, {**cfg, "season": 2030}, lambda: at)
    assert output["models"]["qb-test"]["status"] == "BLOCKED"
    assert "CalledProcessError" in output["models"]["qb-test"]["reason"]
    assert view == before
    config.write_text(config.read_text() + "# changed")
    with pytest.raises(ValueError, match="RUNTIME_CONFIG_CHANGED"):
        shadow.refresh_qb_state(view, {}, spec, tmp_path, cfg, lambda: at)


def test_target_requires_only_observed_prior_team_results(tmp_path):
    at, _, view, cfg = fixture(tmp_path)
    prior = copy.deepcopy(view["games"][0])
    prior.update(
        game_id="prior",
        kickoff=stamp(at - timedelta(hours=5)),
        outcomes=[
            {
                "version": 1,
                "observed_at": stamp(at - timedelta(hours=1)),
                "status": "FINAL",
                "home_score": 10,
                "away_score": 7,
            }
        ],
    )
    future = copy.deepcopy(prior)
    future.update(
        game_id="future_observation",
        outcomes=[{**prior["outcomes"][0], "observed_at": stamp(at + timedelta(hours=1))}],
    )
    view["games"] += [prior, future]

    def predict(kind, baseline, game, artifact, clock):
        assert game["qb_state_required_game_ids"] == ["prior"]
        return candidate()

    shadow.run_shadow(view, tmp_path, cfg, lambda: at, predictor=predict)
    saved = all_records(tmp_path / "shadow/qb-test", view["games"][0]["game_id"])
    assert saved[0]["required_prior_result_ids"] == ["prior"]
    assert saved[0]["model_definition_sha256"] == cfg["shadow_models"][0]["artifact_sha256"]


def test_report_for_another_model_cannot_be_attached(tmp_path):
    at, _, view, cfg = fixture(tmp_path)
    path = tmp_path / "wrong-model-report.json"
    path.write_bytes(canonical({"artifact_file_sha256": "different"}))
    cfg["shadow_models"][0].update(
        evaluation_path=str(path), evaluation_sha256=digest(path.read_bytes())
    )
    out = shadow.run_shadow(view, tmp_path, cfg, lambda: at, predictor=candidate)
    assert out["models"]["qb-test"]["reason"] == "SHADOW_EVALUATION_MODEL_MISMATCH"
    assert out["models"]["qb-test"]["saved_forecasts"] == 0


def test_conditional_scenarios_are_immutable_unscored_and_freeze(tmp_path):
    at, kick, view, cfg = fixture(tmp_path)

    def conditional(*args):
        return {
            "status": "FALLBACK",
            "conditional_scenarios": [
                {
                    "assumption": "If the timestamped backup starts",
                    "status": "CONDITIONAL",
                    "p_home": 0.55,
                    "p_away": 0.44,
                    "p_tie": 0.01,
                }
            ],
        }

    for _ in range(2):
        out = shadow.run_shadow(view, tmp_path, cfg, lambda: at, predictor=conditional)
    assert out["models"]["qb-test"]["saved_forecasts"] == 0
    rows = all_records(tmp_path / "shadow/qb-test/conditional", view["games"][0]["game_id"])
    assert len(rows) == 1 and rows[0]["role"] == "scenario"
    assert rows[0]["scenarios"][0]["assumption"].startswith("If")
    shadow.run_shadow(view, tmp_path, cfg, lambda: kick, predictor=conditional)
    assert (
        len(all_records(tmp_path / "shadow/qb-test/conditional", view["games"][0]["game_id"])) == 1
    )
    isolated = tmp_path / "crossing"
    times = iter([at, at, kick, kick])
    out = shadow.run_shadow(view, isolated, cfg, lambda: next(times, kick), predictor=conditional)
    assert not all_records(isolated / "shadow/qb-test/conditional", view["games"][0]["game_id"])
    assert out["games"][view["games"][0]["game_id"]]["qb-test"]["conditional_scenarios"] == []


def test_invalid_conditional_distribution_is_not_published(tmp_path):
    at, _, view, cfg = fixture(tmp_path)

    def invalid(*args):
        return {
            "status": "FALLBACK",
            "conditional_scenarios": [{"p_home": 0.9, "p_away": 0.9, "p_tie": 0.01}],
        }

    out = shadow.run_shadow(view, tmp_path, cfg, lambda: at, predictor=invalid)
    assert (
        out["games"][view["games"][0]["game_id"]]["qb-test"]["reason"]
        == "INVALID_CONDITIONAL_DISTRIBUTION"
    )
    assert not all_records(tmp_path / "shadow/qb-test/conditional", view["games"][0]["game_id"])
