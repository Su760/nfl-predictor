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
