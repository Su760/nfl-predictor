"""Read-only freshness, integrity and display semantics of the playoff consumer."""

import copy
import hashlib
import importlib
import json
import shutil
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "ops"))
fixture = importlib.import_module("tests.test_season_simulation").fixture
picture_module = importlib.import_module("season_playoffs")
sim = importlib.import_module("season_simulation")
NOW = datetime(2026, 10, 2, 23, tzinfo=UTC)


def evidence():
    view, cfg = fixture()
    view["sources"] = {"nflverse_history": {"raw_sha256": "a" * 64}}
    view["last_successful_source_check"] = view["updated_at"]
    return view, cfg


def save(root, view, cfg, blocked=None, indexed=True, mutate=None):
    snapshot = sim.simulate(view, cfg)
    snapshot["evidence_signature"] = sim.evidence_signature(view, cfg)
    snapshot["history_raw_sha256"] = view["sources"]["nflverse_history"]["raw_sha256"]
    snapshot["elo_source_sha256"] = hashlib.sha256((ROOT / "src/nfl_predictor/ratings/elo.py").read_bytes()).hexdigest()
    if blocked:
        snapshot.update(status="BLOCKED", blocked_reason=blocked, team_probabilities=None)
    if mutate:
        mutate(snapshot)
    identity = sim.digest(snapshot)
    (root / "simulations").mkdir(exist_ok=True)
    (root / "simulations" / (identity + ".json")).write_bytes(sim.canonical(snapshot))
    if indexed:
        (root / "simulation-index").mkdir(exist_ok=True)
        (root / "simulation-index" / (snapshot["evidence_signature"] + ".json")).write_text(json.dumps({"snapshot_id": identity}))
    return identity


def test_fresh_complete_read_only_totals_and_full_distribution(tmp_path, monkeypatch):
    view, cfg = evidence()
    identity = save(tmp_path, view, cfg)
    before = {str(p): p.read_bytes() for p in tmp_path.rglob("*.json")}
    monkeypatch.setattr(sim, "snapshot", lambda *a: pytest.fail("Endpoint must not refresh"))
    monkeypatch.setattr(sim, "simulate", lambda *a: pytest.fail("Endpoint must not simulate"))
    result = picture_module.saved_picture(view, tmp_path, cfg, NOW)
    assert result["status"] == "COMPLETE" and result["fresh"]
    assert result["snapshot"]["snapshot_id"] == identity
    assert len(result["teams"]) == 32 and result["favorite"]
    assert result["totals"] == pytest.approx(similar_totals())
    assert {str(p): p.read_bytes() for p in tmp_path.rglob("*.json")} == before


def similar_totals():
    return {"playoffs": 14, "division": 8, "one_seed": 2, "conference": 2, "super_bowl": 1}


@pytest.mark.parametrize("field,value", [("home", "SEA"), ("away", "SEA"), ("neutral_site", True), ("kickoff", "2026-10-04T13:30:00Z"), ("schedule_version", 99)])
def test_actual_schedule_context_invalidates_saved_evidence(tmp_path, field, value):
    view, cfg = evidence()
    save(tmp_path, view, cfg)
    changed = copy.deepcopy(view)
    changed["games"][0][field] = value
    assert sim.evidence_signature(view, cfg) != sim.evidence_signature(changed, cfg)
    assert picture_module.saved_picture(changed, tmp_path, cfg, NOW)["status"] == "STALE"


def test_heartbeat_does_not_stale_unchanged_material_evidence(tmp_path):
    view, cfg = evidence()
    save(tmp_path, view, cfg)
    view["updated_at"] = "2026-10-02T22:00:00Z"
    view["games"][0]["inputs"] = {}
    assert picture_module.saved_picture(view, tmp_path, cfg, NOW)["status"] == "COMPLETE"


def test_changed_model_history_config_and_result_are_stale(tmp_path):
    view, cfg = evidence()
    save(tmp_path, view, cfg)
    mutations = [
        lambda v, c: v["model"].update(model_state_sha256="new-model"),
        lambda v, c: v["sources"]["nflverse_history"].update(raw_sha256="b" * 64),
        lambda v, c: c.update(seed=c["seed"] + 1),
        lambda v, c: v["games"][0].update(outcomes=[{"status":"FINAL", "home_score":3, "away_score":6, "observed_at":"2026-09-09T00:00:00Z"}]),
    ]
    for mutate in mutations:
        changed, settings = copy.deepcopy(view), copy.deepcopy(cfg)
        mutate(changed, settings)
        result = picture_module.saved_picture(changed, tmp_path, settings, NOW)
        assert result["status"] == "STALE" and not result["fresh"]
        assert len(result["teams"]) == 32


def test_current_blocked_run_does_not_fall_back_to_stale_probabilities(tmp_path):
    view, cfg = evidence()
    save(tmp_path, view, cfg, indexed=False)
    cfg["seed"] += 1
    save(tmp_path, view, cfg, blocked="MISSING_NET_TOUCHDOWNS:BUF,NYJ")
    result = picture_module.saved_picture(view, tmp_path, cfg, NOW)
    assert result["status"] == "BLOCKED" and result["fresh"]
    assert result["reason"] == "MISSING_NET_TOUCHDOWNS:BUF,NYJ"
    assert result["snapshot"]["team_probabilities"] is None
    assert not result["favorite"] and result["totals"] is None
    assert all("probabilities" not in t for t in result["teams"])


def test_absent_artifact_preserves_current_records_and_all_teams(tmp_path):
    view, cfg = evidence()
    view["games"][0]["outcomes"] = [{"status":"FINAL", "home_score":3, "away_score":6, "observed_at":"2026-09-09T00:00:00Z"}]
    result = picture_module.saved_picture(view, tmp_path, cfg, NOW)
    assert result["status"] == "ABSENT" and len(result["teams"]) == 32
    assert sum(t["wins"] for t in result["teams"]) == 1
    assert sum(t["losses"] for t in result["teams"]) == 1


@pytest.mark.parametrize("mutation,reason", [
    (lambda s: s["team_probabilities"]["BUF"].update(super_bowl=0.9), "INVALID_SIMULATION"),
    (lambda s: s["team_probabilities"].pop("BUF"), "INVALID_SIMULATION_DISTRIBUTION"),
    (lambda s: s.update(samples=0), "INVALID_SIMULATION_DISTRIBUTION"),
    (lambda s: s["team_probabilities"]["BUF"].update(playoffs=1.01), "INVALID_SIMULATION_PROBABILITY"),
])
def test_hash_valid_but_invalid_distribution_is_blocked(tmp_path, mutation, reason):
    view, cfg = evidence()
    save(tmp_path, view, cfg, mutate=mutation)
    result = picture_module.saved_picture(view, tmp_path, cfg, NOW)
    assert result["status"] == "BLOCKED" and reason in result["reason"]
    assert result["snapshot"] is None and not result["favorite"]


def test_hash_tampering_or_bad_reference_fails_closed(tmp_path):
    view, cfg = evidence()
    identity = save(tmp_path, view, cfg)
    path = tmp_path / "simulations" / (identity + ".json")
    snapshot = json.loads(path.read_text()); snapshot["samples"] += 1
    path.write_text(json.dumps(snapshot))
    assert "SIMULATION_HASH_MISMATCH" in picture_module.saved_picture(view, tmp_path, cfg, NOW)["reason"]
    index = next((tmp_path / "simulation-index").glob("*.json"))
    index.write_text('{"snapshot_id":"../view"}')
    assert picture_module.saved_picture(view, tmp_path, cfg, NOW)["reason"] == "INVALID_SIMULATION_REFERENCE"


def test_missing_invalid_or_future_final_is_not_an_invented_record(tmp_path):
    view, cfg = evidence()
    view["games"][0]["status"] = "STATUS_FINAL"
    with pytest.raises(ValueError, match="MISSING_FINAL_OUTCOME"):
        picture_module.current_teams(view, cfg)
    view["games"][0]["outcomes"] = [{"status":"FINAL", "home_score":-1, "away_score":6, "observed_at":"2026-09-09T00:00:00Z"}]
    with pytest.raises(ValueError, match="INVALID_FINAL_SCORE"):
        picture_module.current_teams(view, cfg)
    view["games"][0]["outcomes"][0].update(home_score=3, observed_at="2026-12-01T00:00:00Z")
    with pytest.raises(ValueError, match="INVALID_CURRENT_OUTCOME"):
        picture_module.current_teams(view, cfg)


def test_playoff_ui_filters_sorts_blocked_and_stale_semantics(tmp_path):
    view, cfg = evidence()
    save(tmp_path, view, cfg)
    data = picture_module.saved_picture(view, tmp_path, cfg, NOW)
    script = ROOT / "ops/viewer/playoffs.js"
    harness = r'''
const ui=require(process.argv[1]), data=JSON.parse(process.argv[2]);
const sorted=ui.orderedTeams(data), afc=ui.orderedTeams(data,'AFC'), nfc=ui.orderedTeams(data,'NFC');
const blocked={...data,status:'BLOCKED',reason:'MISSING_NET_TOUCHDOWNS:<BUF>',favorite:[],snapshot:{...data.snapshot,status:'BLOCKED',team_probabilities:null}};
const stale={...data,status:'STALE',freshness_reasons:['Model snapshot changed.']};
console.log(JSON.stringify({count:sorted.length,afc:afc.length,nfc:nfc.length,
values:sorted.map(t=>t.probabilities.super_bowl),html:ui.pageHtml(data),blocked:ui.pageHtml(blocked),stale:ui.pageHtml(stale),table:ui.tableHtml(data,'all','super_bowl','desc'),
nearFull:ui.percent(.9995),full:ui.percent(1),zero:ui.percent(0),nearZero:ui.percent(.0001),ascending:ui.orderedTeams(data,'all','team','asc').map(t=>t.name)}));
'''
    run = subprocess.run([shutil.which("node"), "-e", harness, str(script), json.dumps(data)], check=True, capture_output=True, text=True)
    output = json.loads(run.stdout)
    assert (output["count"], output["afc"], output["nfc"]) == (32,16,16)
    assert output["values"] == sorted(output["values"], reverse=True)
    assert output["ascending"] == sorted(output["ascending"])
    assert output["nearFull"] == ">99.9%" and output["full"] == "100.0%"
    assert output["zero"] == "0.0%" and output["nearZero"] == "<0.1%"
    assert "completed draws" in output["html"] and "Model favorite" in output["html"]
    assert "requested draws" in output["blocked"] and "completed draws" not in output["blocked"]
    assert "MISSING_NET_TOUCHDOWNS:&lt;BUF&gt;" in output["blocked"]
    assert "Stale snapshot" in output["stale"] and "Saved snapshot favorite" in output["stale"]
    assert output["table"].count('<th scope="row">') == 32
    assert "not model accuracy" in output["html"]
    assert "mathematical elimination or clinching" in output["table"]
    assert "snapshot" in output["table"]


def test_playoff_assets_and_endpoint_allowlist():
    viewer = importlib.import_module("week1_viewer")
    for path in ("/playoffs", "/playoffs.js", "/playoffs.css"):
        assert viewer.static_response(path)[2] == 200
    assert b'aria-current="page"' in viewer.static_response("/playoffs")[0]
    assert b'href="/playoffs"' in viewer.static_response("/")[0]
    for path in ("/api/playoffs", "/simulations", "/simulations/../view.json", "/playoffs.js?file=private"):
        assert viewer.static_response(path) is None
