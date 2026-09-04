from __future__ import annotations

import hashlib
import re
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[2]
PUBLIC_WORKFLOW = ROOT / ".github/workflows/nfl-v2-dispatch.yml"
PRIVATE_ROOT = ROOT / "deploy/private-data-repo"
CAPTURE_WORKFLOW = PRIVATE_ROOT / ".github/workflows/capture-and-forecast.yml"
PRIVATE_DUE_WORKFLOW = PRIVATE_ROOT / ".github/workflows/private-due-check.yml"
SETTLE_WORKFLOW = PRIVATE_ROOT / ".github/workflows/settle-and-report.yml"
PUBLIC_MANIFEST = ROOT / "deploy/schedules/dispatch-windows-2026.json"
PRIVATE_MANIFEST = PRIVATE_ROOT / "config/dispatch-windows-2026.json"
DATA_REPO_CONFIG = PRIVATE_ROOT / "config/data_repo.toml"
PINNED_ACTION = re.compile(r"^[^@]+@[0-9a-f]{40}$")

PUBLIC_CRONS = {
    "12 0 7 9 *",
    "17 0 7 9 *",
    "22 0 7 9 *",
    "27 0 7 9 *",
    "12 23 9 9 *",
    "17 23 9 9 *",
    "22 23 9 9 *",
    "27 23 9 9 *",
}
PRIVATE_CRONS = {
    "14 0 7 9 *",
    "19 0 7 9 *",
    "24 0 7 9 *",
    "29 0 7 9 *",
    "14 23 9 9 *",
    "19 23 9 9 *",
    "24 23 9 9 *",
    "29 23 9 9 *",
}


@dataclass(frozen=True)
class ParsedWorkflow:
    path: Path
    raw_text: str
    data: dict[str, Any]

    @property
    def permissions(self) -> dict[str, str]:
        return self.data["permissions"]

    @property
    def steps(self) -> list[dict[str, Any]]:
        jobs = self.data["jobs"]
        return [step for job in jobs.values() for step in job.get("steps", [])]

    @property
    def crons(self) -> set[str]:
        schedules = self.data["on"].get("schedule", [])
        return {item["cron"] for item in schedules}


def parse_workflow(path: Path) -> ParsedWorkflow:
    raw = path.read_text(encoding="utf-8")
    parsed = yaml.load(raw, Loader=yaml.BaseLoader)
    assert isinstance(parsed, dict)
    assert set(parsed).issuperset({"name", "on", "permissions", "jobs"})
    assert isinstance(parsed["on"], dict)
    assert isinstance(parsed["permissions"], dict)
    assert isinstance(parsed["jobs"], dict)
    return ParsedWorkflow(path, raw, parsed)


def step_index(workflow: ParsedWorkflow, name: str) -> int:
    return next(index for index, step in enumerate(workflow.steps) if step.get("name") == name)


def test_public_workflow_has_no_provider_secret_or_private_payload() -> None:
    public = parse_workflow(PUBLIC_WORKFLOW)

    assert "ODDS_API_KEY" not in public.raw_text
    assert "NFL_PREDICTOR_DATA_DIR" not in public.raw_text
    assert public.permissions == {"contents": "read"}
    assert public.crons == PUBLIC_CRONS
    assert all(not entry.startswith("0 ") for entry in public.crons)

    token_steps = [
        step for step in public.steps if "NFL_DATA_REPO_DISPATCH_TOKEN" in str(step.get("env", {}))
    ]
    assert len(token_steps) == 1
    assert "NFL_DATA_REPO_DISPATCH_TOKEN" not in token_steps[0].get("run", "")


def test_private_worker_pins_public_sha_serializes_budget_and_orders_secret_use() -> None:
    private = parse_workflow(CAPTURE_WORKFLOW)
    checkouts = [
        step for step in private.steps if str(step.get("uses", "")).startswith("actions/checkout@")
    ]
    public_checkout = next(step for step in checkouts if step.get("with", {}).get("path") == "code")

    assert public_checkout["with"]["ref"] == "${{ github.event.client_payload.code_sha }}"
    assert private.data["concurrency"]["cancel-in-progress"] == "false"
    assert private.data["concurrency"]["group"] == "nfl-odds-budget-2026-09"
    assert private.data["on"]["repository_dispatch"]["types"] == ["nfl_forecast_due"]
    assert "workflow_dispatch" in private.data["on"]

    validation = step_index(private, "Validate fixed dispatch envelope")
    budget = step_index(private, "Reserve monthly odds budget")
    capture = step_index(private, "Capture immutable forecast")
    leak_scan = step_index(private, "Scan ledger for secret leakage")
    immutable = step_index(private, "Validate immutable ledger")
    push = step_index(private, "Push immutable ledger append")
    assert validation < budget < capture < leak_scan < immutable < push

    capture_step = private.steps[capture]
    assert set(capture_step["env"]) == {"ODDS_API_KEY"}
    assert capture_step["env"]["ODDS_API_KEY"] == "${{ secrets.ODDS_API_KEY }}"
    assert "--api-key" not in capture_step["run"]


def test_private_backup_is_schedule_specific_month_locked_and_validates_before_odds() -> None:
    private_due = parse_workflow(PRIVATE_DUE_WORKFLOW)

    assert private_due.crons == PRIVATE_CRONS
    assert private_due.crons != PUBLIC_CRONS
    assert all(not entry.startswith("0 ") for entry in private_due.crons)
    assert private_due.data["concurrency"]["cancel-in-progress"] == "false"
    assert private_due.data["concurrency"]["group"] == "nfl-odds-budget-2026-09"
    assert "forecast due" in private_due.raw_text
    assert "--trigger private_schedule" in private_due.raw_text

    validation = step_index(private_due, "Validate private due configuration")
    budget = step_index(private_due, "Reserve monthly odds budget")
    capture = step_index(private_due, "Run idempotent private due forecast")
    assert validation < budget < capture
    assert set(private_due.steps[capture]["env"]) == {"ODDS_API_KEY"}


def test_settlement_has_postgame_and_correction_paths_without_odds_secret() -> None:
    settlement = parse_workflow(SETTLE_WORKFLOW)

    assert len(settlement.crons) == 2
    assert all(not entry.startswith("0 ") for entry in settlement.crons)
    assert "postgame" in settlement.raw_text
    assert "official_correction" in settlement.raw_text
    assert "ODDS_API_KEY" not in settlement.raw_text


def test_all_workflows_use_explicit_safe_schema_and_sha_pinned_actions() -> None:
    workflows = [
        parse_workflow(PUBLIC_WORKFLOW),
        parse_workflow(CAPTURE_WORKFLOW),
        parse_workflow(PRIVATE_DUE_WORKFLOW),
        parse_workflow(SETTLE_WORKFLOW),
    ]

    for workflow in workflows:
        assert "pull_request_target" not in workflow.data["on"]
        assert workflow.permissions != "write-all"
        assert "github.run_started_at" not in workflow.raw_text
        assert any(step.get("name") == "Freeze UTC scheduler clock" for step in workflow.steps)
        for step in workflow.steps:
            uses = step.get("uses")
            if uses is not None:
                assert PINNED_ACTION.fullmatch(uses), (workflow.path, uses)
            run = step.get("run", "")
            assert "${{ github.event.client_payload" not in run
            assert "${{ inputs." not in run
            assert "--api-key" not in run


def test_static_manifests_are_identical_provenanced_incomplete_and_fail_closed() -> None:
    public_bytes = PUBLIC_MANIFEST.read_bytes()
    private_bytes = PRIVATE_MANIFEST.read_bytes()
    manifest = __import__("json").loads(public_bytes)
    config = tomllib.loads(DATA_REPO_CONFIG.read_text(encoding="utf-8"))

    assert private_bytes == public_bytes
    assert manifest["season"] == 2026
    assert manifest["generated_from_active_event_versions"] is False
    assert manifest["deployment_enabled"] is False
    assert manifest["official_schedule_review"]["source_kind"] == "official_schedule"
    assert manifest["reviewed_kickoff_facts"] == [
        {
            "away_team": "NE",
            "home_team": "SEA",
            "kickoff_at_utc": "2026-09-10T00:20:00Z",
            "status": "reviewed_reference_only_not_active_event_version",
        }
    ]
    limitations = " ".join(manifest["limitations"]).lower()
    assert "flex" in limitations
    assert "tbd" in limitations
    assert "active event version" in limitations
    assert set(manifest["public_crons"]) == PUBLIC_CRONS
    assert set(manifest["private_crons"]) == PRIVATE_CRONS

    digest = hashlib.sha256(public_bytes).hexdigest()
    assert config["schedule"]["manifest_sha256"] == digest
    assert config["deployment_enabled"] is False
    assert config["zero_dollar_mode"] is True
    assert config["authorization"]["approved_public_code_sha"] == ""
    assert config["authorization"]["target_private_repository"] == ""
    assert config["cost_projection"]["verified_included_private_actions_minutes"] == 0
    assert config["cost_projection"]["verified_included_storage_bytes"] == 0
