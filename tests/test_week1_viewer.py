"""The private viewer serves only explicit application routes and assets."""
import importlib.util
import json
import shutil
import subprocess
from pathlib import Path

spec = importlib.util.spec_from_file_location("week1_viewer", Path(__file__).parents[1] / "ops/week1_viewer.py")
viewer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(viewer)
static_response = viewer.static_response


def test_application_routes_and_assets():
    for path in ("/", "/performance", "/games/2026_01_SF_LA"):
        body, kind, status = static_response(path)
        assert status == 200 and kind.startswith("text/html")
        assert b'<main id="app"' in body
    assert static_response("/app.js")[1].startswith("text/javascript")


def test_asset_allowlist_prevents_private_file_access():
    for path in ("/../view.json", "/%2e%2e/view.json", "/styles.css/../view.json",
                 "/games/../view.json", "/games/%2fetc%2fpasswd", "/view.json",
                 "/api/games/2026_01_SF_LA", "/app.js?file=/etc/passwd"):
        assert static_response(path) is None


def test_input_status_preserves_availability_and_check_time_distinctions():
    node = shutil.which("node")
    assert node is not None, "Node is required to verify the browser input classifier"
    script = Path(__file__).parents[1] / "ops/viewer/app.js"
    harness = r"""
const fs=require('fs'),vm=require('vm'),source=fs.readFileSync(process.argv[1],'utf8');
const ctx={document:{getElementById:()=>({})},Intl,Date};vm.createContext(ctx);
vm.runInContext(source.slice(0,source.lastIndexOf("document.getElementById('refresh').onclick")),ctx);
const input={status:'MISSING',reason:'NO_VERIFIED_PREGAME_OFFICIAL_INACTIVES_FOR_GAME',captured_at:'2026-09-13T02:00:00Z',used_by_model:false};
const failed={status:'MISSING',reason:'CalledProcessError: source failed',raw_sha256:null,captured_at:'2026-09-13T02:00:00Z',last_successful_check:'2026-09-13T00:00:00Z'};
ctx.input=input;ctx.failed=failed;
const output=vm.runInContext(`[
 inputState(input),
 inputState(input,failed),
 inputState(input,{...failed,captured_at:'2026-09-12T22:00:00Z'}),
 inputState({status:'STALE',used_by_model:false}),
 inputState({status:'AVAILABLE',used_by_model:false}),
 inputState({status:'PROVENANCE_LIMITED',used_by_model:false}),
 inputState({status:'AVAILABLE',used_by_model:true}),
 inputState({status:'MISSING',reason:'PARTICIPATING_TEAM_EXPECTED_QB_MISSING'}),
 inputState({...input,fetch_failed:true})
]`,ctx);
console.log(JSON.stringify(output));
"""
    result = subprocess.run([node, "-e", harness, str(script)], check=True, capture_output=True, text=True)
    states = json.loads(result.stdout)
    assert [state["label"] for state in states] == [
        "No verified report yet", "Fetch failed", "No verified report yet", "Stale",
        "Available", "Publication time unverified", "Available", "Not verified", "Fetch failed",
    ]
    assert states[1]["lastSuccessfulCheck"] == "2026-09-13T00:00:00Z"
    assert states[0]["lastSuccessfulCheck"] is None
    assert states[4]["usage"] == "Not used by model"
    assert states[6]["usage"] == "Used by model"


def test_conditional_shadow_scenarios_remain_unweighted_and_unscored():
    node = shutil.which("node")
    assert node is not None
    script = Path(__file__).parents[1] / "ops/viewer/app.js"
    harness = r"""
const fs=require('fs'),vm=require('vm'),source=fs.readFileSync(process.argv[1],'utf8');
const ctx={document:{getElementById:()=>({})},Intl,Date};vm.createContext(ctx);
vm.runInContext(source.slice(0,source.lastIndexOf("document.getElementById('refresh').onclick")),ctx);
ctx.states={qb_shadow:{status:'FALLBACK',conditional_scenarios:[{assumption:'If Player A starts <script>',status:'CONDITIONAL',p_home:.6,p_away:.39,p_tie:.01,evidence:{captured_at:'2026-09-13T02:00:00Z'},limitations:['Starter not confirmed']}]}};
const before=JSON.stringify(ctx.states);
const html=vm.runInContext('conditionalScenarios(states)',ctx);
const empty=vm.runInContext('conditionalScenarios({qb_shadow:{status:"FALLBACK"}})',ctx);
console.log(JSON.stringify({html,empty,unchanged:before===JSON.stringify(ctx.states)}));
"""
    result = subprocess.run([node, "-e", harness, str(script)], check=True, capture_output=True, text=True)
    output = json.loads(result.stdout)
    assert output["unchanged"] and output["empty"] == ""
    assert "Unweighted" in output["html"] and "excluded from scorecards" in output["html"]
    assert "60.0%" in output["html"] and "39.0%" in output["html"] and "1.0%" in output["html"]
    assert "Starter not confirmed" in output["html"]
    assert "&lt;script&gt;" in output["html"] and "<script>" not in output["html"]


def test_live_status_does_not_hide_failed_or_stale_source_checks():
    from datetime import UTC, datetime
    now = datetime(2026, 9, 16, 5, tzinfo=UTC)
    view = {"worker_status": "HEALTHY", "last_successful_source_check": "2026-09-15T20:00:00Z", "next_scheduled_run": "2026-09-15T22:00:00Z"}
    worker = {"status": "FAILED", "checked_at": "2026-09-16T04:59:30Z", "error": "HTTP 400"}
    cfg = {"poll_seconds": 60, "maximum_capture_age_seconds": 7200}
    status = viewer.operational_status(view, worker, cfg, now)
    assert status["worker_status"] == "FAILED"
    assert status["source_status"] == "STALE"
    assert status["scheduled_run_overdue"] is True
    assert status["worker"]["error"] == "HTTP 400"
    worker = {**worker, "status": "HEALTHY", "checked_at": "2026-09-16T04:00:00Z"}
    assert viewer.operational_status(view, worker, cfg, now)["worker_status"] == "STALE / OFFLINE"
    assert view["worker_status"] == "HEALTHY"  # Read-only derived status.


def test_worker_alert_is_visible_and_escapes_failure_text():
    node = shutil.which("node")
    script = Path(__file__).parents[1] / "ops/viewer/app.js"
    harness = r"""
const fs=require('fs'),vm=require('vm'),source=fs.readFileSync(process.argv[1],'utf8');
const ctx={document:{getElementById:()=>({})},Intl,Date};vm.createContext(ctx);
vm.runInContext(source.slice(0,source.lastIndexOf("document.getElementById('refresh').onclick")),ctx);
console.log(vm.runInContext(`data={worker_status:'FAILED',source_status:'STALE',worker:{error:'HTTP400 <script>'},games:[{week:1,origins:{T60:'MISSED'}}]};operationsAlert()`,ctx));
"""
    result = subprocess.run([node, "-e", harness, str(script)], check=True, capture_output=True, text=True)
    assert 'role="alert"' in result.stdout
    assert "FAILED" in result.stdout and "STALE" in result.stdout
    assert "&lt;script&gt;" in result.stdout and "<script>" not in result.stdout
    assert "1 missed" in result.stdout and "T60" in result.stdout
