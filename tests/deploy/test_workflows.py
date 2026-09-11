from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import textwrap
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
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
class WorkflowStep:
    name: str
    identifier: str | None
    run: str | None
    uses: str | None
    environment: Mapping[str, str]
    inputs: Mapping[str, str]
    condition: str | None
    working_directory: str | None


@dataclass(frozen=True)
class WorkflowJob:
    identifier: str
    condition: str | None
    needs: tuple[str, ...]
    outputs: Mapping[str, str]
    environment: Mapping[str, str]
    timeout_minutes: int | None
    steps: tuple[WorkflowStep, ...]


@dataclass(frozen=True)
class ParsedWorkflow:
    path: Path
    raw_text: str
    data: dict[str, Any]
    typed_jobs: tuple[WorkflowJob, ...]

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

    def job(self, identifier: str) -> WorkflowJob:
        return next(job for job in self.typed_jobs if job.identifier == identifier)

    def step(self, name: str) -> WorkflowStep:
        return next(
            step
            for job in self.typed_jobs
            for step in job.steps
            if step.name == name
        )


def _string_mapping(value: object) -> dict[str, str]:
    if value is None:
        return {}
    assert isinstance(value, dict)
    assert all(isinstance(key, str) and isinstance(item, str) for key, item in value.items())
    return dict(value)


def _typed_step(value: object) -> WorkflowStep:
    assert isinstance(value, dict)
    assert isinstance(value.get("name"), str)
    identifier = value.get("id")
    run = value.get("run")
    uses = value.get("uses")
    condition = value.get("if")
    working_directory = value.get("working-directory")
    assert identifier is None or isinstance(identifier, str)
    assert run is None or isinstance(run, str)
    assert uses is None or isinstance(uses, str)
    assert condition is None or isinstance(condition, str)
    assert working_directory is None or isinstance(working_directory, str)
    return WorkflowStep(
        name=value["name"],
        identifier=identifier,
        run=run,
        uses=uses,
        environment=_string_mapping(value.get("env")),
        inputs=_string_mapping(value.get("with")),
        condition=condition,
        working_directory=working_directory,
    )


def _typed_job(identifier: str, value: object) -> WorkflowJob:
    assert isinstance(value, dict)
    raw_needs = value.get("needs", [])
    if isinstance(raw_needs, str):
        needs = (raw_needs,)
    else:
        assert isinstance(raw_needs, list) and all(isinstance(item, str) for item in raw_needs)
        needs = tuple(raw_needs)
    raw_timeout = value.get("timeout-minutes")
    timeout = None if raw_timeout is None else int(raw_timeout)
    condition = value.get("if")
    assert condition is None or isinstance(condition, str)
    raw_steps = value.get("steps", [])
    assert isinstance(raw_steps, list)
    return WorkflowJob(
        identifier=identifier,
        condition=condition,
        needs=needs,
        outputs=_string_mapping(value.get("outputs")),
        environment=_string_mapping(value.get("env")),
        timeout_minutes=timeout,
        steps=tuple(_typed_step(step) for step in raw_steps),
    )


def parse_workflow(path: Path) -> ParsedWorkflow:
    raw = path.read_text(encoding="utf-8")
    parsed = yaml.load(raw, Loader=yaml.BaseLoader)
    assert isinstance(parsed, dict)
    assert set(parsed).issuperset({"name", "on", "permissions", "jobs"})
    assert isinstance(parsed["on"], dict)
    assert isinstance(parsed["permissions"], dict)
    assert isinstance(parsed["jobs"], dict)
    return ParsedWorkflow(
        path,
        raw,
        parsed,
        tuple(_typed_job(identifier, job) for identifier, job in parsed["jobs"].items()),
    )


def run_step(
    step: WorkflowStep,
    cwd: Path,
    environment: Mapping[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    assert step.run is not None
    active_environment = os.environ.copy()
    active_environment["PYTHONPATH"] = str(ROOT / "src") + os.pathsep + active_environment.get("PYTHONPATH", "")
    active_environment.update(
        {
            name: value
            for name, value in step.environment.items()
            if not value.startswith("${{")
        }
    )
    if environment is not None:
        active_environment.update(environment)
    return subprocess.run(
        ["bash", "-euo", "pipefail", "-c", step.run],
        cwd=cwd,
        env=active_environment,
        check=False,
        capture_output=True,
        text=True,
    )


def git(cwd: Path, *arguments: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(cwd), *arguments],
        check=True,
        capture_output=True,
        text=True,
    )


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

    assert public_checkout["with"]["ref"] == (
        "${{ needs.validate-dispatch.outputs.code_sha }}"
    )
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

    assert private_due.crons == PRIVATE_CRONS | {"25 0 7 9 *", "25 23 9 9 *"}
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
    assert private_due.steps[capture]["env"]["ODDS_API_KEY"] == (
        "${{ secrets.ODDS_API_KEY }}"
    )


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


def _private_workflows() -> tuple[ParsedWorkflow, ...]:
    return tuple(
        parse_workflow(path)
        for path in (CAPTURE_WORKFLOW, PRIVATE_DUE_WORKFLOW, SETTLE_WORKFLOW)
    )


def _cli_steps(workflow: ParsedWorkflow) -> tuple[tuple[WorkflowJob, WorkflowStep], ...]:
    return tuple(
        (job, step)
        for job in workflow.typed_jobs
        for step in job.steps
        if step.run is not None and "nfl_predictor.cli" in step.run
    )


def test_private_cli_steps_use_absolute_sibling_private_checkout_as_data_root() -> None:
    for workflow in _private_workflows():
        cli_steps = _cli_steps(workflow)
        assert cli_steps, workflow.path
        for job, step in cli_steps:
            effective_environment = {**job.environment, **step.environment}
            assert effective_environment["NFL_PREDICTOR_DATA_DIR"] == (
                "${{ github.workspace }}/data"
            )


def test_every_private_production_cli_step_receives_explicit_runtime_config() -> None:
    for workflow in _private_workflows():
        for job, step in _cli_steps(workflow):
            effective_environment = {**job.environment, **step.environment}
            assert effective_environment["NFL_V2_RUNTIME_CONFIG"] == (
                "${{ github.workspace }}/code/configs/base.toml"
            )


def test_dispatch_validation_receives_trusted_actor_and_sender_contexts() -> None:
    workflow = parse_workflow(CAPTURE_WORKFLOW)
    validation = workflow.step("Validate fixed dispatch envelope")
    config = tomllib.loads(DATA_REPO_CONFIG.read_text(encoding="utf-8"))

    assert validation.environment["ACTOR"] == "${{ github.actor }}"
    assert validation.environment["EVENT_SENDER"] == "${{ github.event.sender.login }}"
    assert config["authorization"]["approved_actors"] == ["Su760"]


IMMUTABLE_GUARDS = {
    CAPTURE_WORKFLOW: "Validate immutable ledger",
    PRIVATE_DUE_WORKFLOW: "Validate immutable ledger",
    SETTLE_WORKFLOW: "Validate immutable settlement append",
}


def _initialize_immutable_repository(root: Path) -> Path:
    data = root / "data"
    (data / "config").mkdir(parents=True)
    (data / "ledger").mkdir()
    (data / "config" / "data_repo.toml").write_text(
        '[ledger]\nallowed_append_roots = ["ledger", "reports", "raw"]\n',
        encoding="utf-8",
    )
    immutable = "".join(f"{line:04d}: immutable payload line\n" for line in range(100))
    (data / "ledger" / "immutable.json").write_text(immutable, encoding="utf-8")
    git(data, "init", "-q", "-b", "main")
    git(data, "config", "user.name", "test")
    git(data, "config", "user.email", "test@example.com")
    git(data, "add", ".")
    git(data, "commit", "-qm", "baseline")
    return data


def _stage_immutable_attack(data: Path, status: str) -> str:
    source = data / "ledger" / "immutable.json"
    destination = data / "config" / "moved.json"
    if status == "R100":
        git(data, "mv", "ledger/immutable.json", "config/moved.json")
    elif status == "R090":
        git(data, "mv", "ledger/immutable.json", "config/moved.json")
        lines = destination.read_text(encoding="utf-8").splitlines()
        destination.write_text("\n".join(["changed", *lines[5:]]) + "\n", encoding="utf-8")
    elif status == "D":
        source.unlink()
    elif status == "C100":
        shutil.copyfile(source, destination)
        source.write_text(source.read_text(encoding="utf-8") + "changed\n", encoding="utf-8")
    else:  # pragma: no cover - closed test table
        raise AssertionError(status)
    git(data, "add", "-A")
    return git(
        data,
        "diff",
        "--cached",
        "--name-status",
        "--find-renames",
        "--find-copies",
    ).stdout


@pytest.mark.parametrize("workflow_path,step_name", IMMUTABLE_GUARDS.items())
@pytest.mark.parametrize("status", ["R100", "R090", "D", "C100"])
def test_immutable_guard_rejects_staged_source_and_destination_paths(
    tmp_path: Path, workflow_path: Path, step_name: str, status: str
) -> None:
    data = _initialize_immutable_repository(tmp_path)
    observed_status = _stage_immutable_attack(data, status)
    if status.startswith("R"):
        assert observed_status.startswith("R")
    elif status == "C100":
        assert observed_status.startswith("C100")
    else:
        assert observed_status.startswith("D")

    result = run_step(parse_workflow(workflow_path).step(step_name), tmp_path)

    assert result.returncode != 0, (workflow_path, status, result.stdout, result.stderr)


@pytest.mark.parametrize("workflow_path,step_name", IMMUTABLE_GUARDS.items())
def test_immutable_guard_is_nul_safe_for_untracked_newline_paths(
    tmp_path: Path, workflow_path: Path, step_name: str
) -> None:
    data = _initialize_immutable_repository(tmp_path)
    allowed = data / "ledger" / "append\nrecord.json"
    allowed.write_text("{}\n", encoding="utf-8")
    guard = parse_workflow(workflow_path).step(step_name)

    accepted = run_step(guard, tmp_path)
    assert accepted.returncode == 0, (accepted.stdout, accepted.stderr)

    disallowed = data / "config" / "escape\nrecord.json"
    disallowed.write_text("{}\n", encoding="utf-8")
    rejected = run_step(guard, tmp_path)
    assert rejected.returncode != 0


def test_nonce_is_pushed_with_optimistic_retry_before_public_checkout_or_secrets(
    tmp_path: Path,
) -> None:
    workflow = parse_workflow(CAPTURE_WORKFLOW)
    validation = workflow.job("validate-dispatch")
    forecast = workflow.job("forecast")
    persist = workflow.step("Persist admitted nonce")

    assert validation.outputs["admitted"] == "${{ steps.persist.outputs.admitted }}"
    assert forecast.needs == ("admit-budget",)
    assert workflow.job("admit-budget").needs == ("validate-dispatch",)
    assert forecast.condition is not None
    assert "needs.admit-budget.outputs.admitted == 'true'" in forecast.condition
    assert all(
        step.inputs.get("path") != "code"
        for step in validation.steps
        if step.uses is not None
    )
    assert any(step.inputs.get("path") == "code" for step in forecast.steps)
    assert all("ODDS_API_KEY" not in step.environment for step in validation.steps)
    assert any("ODDS_API_KEY" in step.environment for step in forecast.steps)

    remote = tmp_path / "remote.git"
    subprocess.run(
        ["git", "init", "--bare", "-q", str(remote)],
        check=True,
        capture_output=True,
        text=True,
    )
    data = _initialize_immutable_repository(tmp_path / "workspace")
    git(data, "remote", "add", "origin", str(remote))
    git(data, "push", "-qu", "origin", "main")
    nonce_relative = "ledger/dispatch-nonces/" + "f" * 64 + ".nonce"
    nonce_path = data / nonce_relative
    nonce_path.parent.mkdir(parents=True)
    nonce_path.write_text("f" * 64 + "\n", encoding="ascii")

    real_git = shutil.which("git")
    assert real_git is not None
    binary_root = tmp_path / "bin"
    binary_root.mkdir()
    wrapper = binary_root / "git"
    wrapper.write_text(
        textwrap.dedent(
            """\
            #!/bin/bash
            if [[ " $* " == *" push "* ]]; then
              printf 'push\\n' >> "$GIT_WRAPPER_LOG"
              if [ ! -e "$GIT_WRAPPER_MARKER" ]; then
                : > "$GIT_WRAPPER_MARKER"
                exit 1
              fi
            fi
            exec "$REAL_GIT" "$@"
            """
        ),
        encoding="utf-8",
    )
    wrapper.chmod(0o755)
    output = tmp_path / "github-output"
    output.write_text("", encoding="utf-8")
    log = tmp_path / "git-wrapper.log"

    result = run_step(
        persist,
        tmp_path / "workspace",
        {
            "GITHUB_OUTPUT": str(output),
            "GIT_WRAPPER_LOG": str(log),
            "GIT_WRAPPER_MARKER": str(tmp_path / "first-push-failed"),
            "NONCE_RELATIVE_PATH": nonce_relative,
            "PATH": str(binary_root) + os.pathsep + os.environ["PATH"],
            "PRIVATE_DEFAULT_BRANCH": "main",
            "REAL_GIT": real_git,
        },
    )

    assert result.returncode == 0, (result.stdout, result.stderr)
    assert log.read_text(encoding="utf-8").splitlines() == ["push", "push"]
    assert "admitted=true" in output.read_text(encoding="utf-8").splitlines()
    remote_contents = subprocess.run(
        ["git", "--git-dir", str(remote), "show", f"main:{nonce_relative}"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    assert remote_contents == "f" * 64 + "\n"


def _write_schedule_hash_fixture(root: Path, *, season: int = 2026) -> dict[str, str]:
    data = root / "data"
    code = root / "code"
    active_data = data / "active" / "week-1.parquet"
    active_data.parent.mkdir(parents=True)
    active_data.write_bytes(b"reviewed active event bytes")
    active_manifest = {
        "schema_version": "active-event-manifest-v1",
        "season": season,
        "files": [
            {
                "path": "active/week-1.parquet",
                "sha256": hashlib.sha256(active_data.read_bytes()).hexdigest(),
                "event_keys": ["event-1:1"],
            }
        ],
    }
    active_manifest_path = data / "active-events.json"
    active_manifest_path.write_text(
        json.dumps(active_manifest, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    active_sha = hashlib.sha256(active_manifest_path.read_bytes()).hexdigest()
    dispatch_manifest = {
        "schema_version": "nfl-v2-dispatch-windows-v1",
        "season": season,
        "deployment_enabled": True,
        "generated_from_active_event_versions": True,
        "active_event_version_manifest_sha256": active_sha,
        "origin_targets": [
            {
                "canonical_event_id": "event-1",
                "origin": "T72",
                "target_at_utc": "2026-09-07T00:20:00Z",
            }
        ],
    }
    dispatch_raw = (
        json.dumps(dispatch_manifest, sort_keys=True, separators=(",", ":")) + "\n"
    ).encode()
    schedule_sha = hashlib.sha256(dispatch_raw).hexdigest()
    public_manifest = code / "deploy" / "schedules" / "dispatch-windows-2026.json"
    private_manifest = data / "config" / "dispatch-windows-2026.json"
    public_manifest.parent.mkdir(parents=True)
    private_manifest.parent.mkdir(parents=True)
    public_manifest.write_bytes(dispatch_raw)
    private_manifest.write_bytes(dispatch_raw)
    private_config = data / "config" / "data_repo.toml"
    private_config.write_text(
        textwrap.dedent(
            f"""\
            deployment_enabled = true
            season = {season}

            [schedule]
            manifest_path = "config/dispatch-windows-2026.json"
            manifest_sha256 = "{schedule_sha}"
            active_event_version_manifest_path = "active-events.json"
            active_event_version_manifest_sha256 = "{active_sha}"
            generated_from_active_event_versions = true
            """
        ),
        encoding="utf-8",
    )
    return {
        "EXPECTED_SCHEDULE_SHA": schedule_sha,
        "NFL_PREDICTOR_DATA_DIR": str(data.resolve()),
        "NFL_PRIVATE_CONFIG": str(private_config.resolve()),
        "NFL_PUBLIC_DISPATCH_MANIFEST": str(public_manifest.resolve()),
    }


def test_public_private_and_active_schedule_bytes_are_cross_bound_before_work(
    tmp_path: Path,
) -> None:
    environment = _write_schedule_hash_fixture(tmp_path)
    for workflow in _private_workflows():
        result = run_step(
            workflow.step("Verify public/private/active schedule bytes"),
            tmp_path,
            environment,
        )
        assert result.returncode == 0, (workflow.path, result.stdout, result.stderr)

    active_data = tmp_path / "data" / "active" / "week-1.parquet"
    active_data.write_bytes(b"tampered active event bytes")
    rejected = run_step(
        parse_workflow(CAPTURE_WORKFLOW).step(
            "Verify public/private/active schedule bytes"
        ),
        tmp_path,
        environment,
    )
    assert rejected.returncode != 0


def test_private_schedule_verification_rejects_non_2026_manifest_season(
    tmp_path: Path,
) -> None:
    environment = _write_schedule_hash_fixture(tmp_path, season=2027)
    result = run_step(
        parse_workflow(PRIVATE_DUE_WORKFLOW).step(
            "Verify public/private/active schedule bytes"
        ),
        tmp_path,
        environment,
    )
    assert result.returncode != 0


def _run_private_clock_step(
    tmp_path: Path, requested_at: str, *, suffix: str
) -> tuple[subprocess.CompletedProcess[str], str, str]:
    output = tmp_path / f"github-output-{suffix}"
    environment_file = tmp_path / f"github-env-{suffix}"
    output.write_text("", encoding="utf-8")
    environment_file.write_text("", encoding="utf-8")
    result = run_step(
        parse_workflow(PRIVATE_DUE_WORKFLOW).step("Freeze UTC scheduler clock"),
        tmp_path,
        {
            "EVENT_NAME": "workflow_dispatch",
            "GITHUB_ENV": str(environment_file),
            "GITHUB_OUTPUT": str(output),
            "REQUESTED_AT": requested_at,
        },
    )
    return (
        result,
        output.read_text(encoding="utf-8"),
        environment_file.read_text(encoding="utf-8"),
    )


def test_multiline_manual_time_is_rejected_without_reaching_github_environment(
    tmp_path: Path,
) -> None:
    result, output, environment = _run_private_clock_step(
        tmp_path,
        "2026-09-07T00:20:00Z\nODDS_API_KEY=hostile",
        suffix="hostile",
    )

    assert result.returncode != 0
    assert output == ""
    assert environment == ""


def test_manual_time_accepts_only_strict_utc_z_and_uses_randomized_step_output(
    tmp_path: Path,
) -> None:
    first, first_output, first_environment = _run_private_clock_step(
        tmp_path, "2026-09-07T00:20:00Z", suffix="first"
    )
    second, second_output, second_environment = _run_private_clock_step(
        tmp_path, "2026-09-07T00:20:00Z", suffix="second"
    )
    offset, _, offset_environment = _run_private_clock_step(
        tmp_path, "2026-09-07T00:20:00+00:00", suffix="offset"
    )

    assert first.returncode == second.returncode == 0
    assert offset.returncode != 0
    assert first_environment == second_environment == offset_environment == ""
    first_delimiters = re.findall(r"^\w+<<([^\n]+)$", first_output, re.MULTILINE)
    second_delimiters = re.findall(r"^\w+<<([^\n]+)$", second_output, re.MULTILINE)
    assert len(first_delimiters) == len(second_delimiters) == 2
    assert set(first_delimiters).isdisjoint(second_delimiters)
    assert "2026-09-07T00:20:00Z" in first_output


def _run_provider_retry_probe(
    root: Path, *, uncertain_odds: bool, late_retry: bool = False, manual: bool = False, prior_claim: bool = False
) -> tuple[subprocess.CompletedProcess[str], list[str]]:
    from datetime import UTC, datetime
    (root / "data" / "ledger").mkdir(parents=True)
    if prior_claim:
        claim_root = root / "data/ledger/budget-reservations/2026-09/claims"
        claim_root.mkdir(parents=True)
        (claim_root / "old.json").write_text("{}")
    target = datetime.now(UTC).isoformat()
    binary_root = root / "bin"
    binary_root.mkdir()
    log = root / "uv.log"
    fake_uv = binary_root / "uv"
    fake_uv.write_text(
        textwrap.dedent(
            """\
            #!/bin/bash
            if [[ " $* " == *" --dry-run "* ]]; then
              if [ "$LATE_RETRY" = "true" ] && [ -e "$UV_LOG" ]; then
                printf '%s\\n' '{"clusters":[],"runs":[]}'
                exit 0
              fi
              printf '%s\\n' "$TEST_PLAN"
              exit 0
            fi
            printf 'live\\n' >> "$UV_LOG"
            printf '%s\\n' "$*" >> "$ROUTE_LOG"
            if [ "$UNCERTAIN_ODDS" = "true" ]; then
              mkdir -p "$NFL_PREDICTOR_DATA_DIR/ledger/budget-reservations/2026-09/claims"
              printf '%s\\n' '{"request_id":"admission:100:1:event:T72:policy"}' > "$NFL_PREDICTOR_DATA_DIR/ledger/budget-reservations/2026-09/claims/attempt.json"
            fi
            exit 17
            """
        ),
        encoding="utf-8",
    )
    fake_uv.chmod(0o755)
    step = parse_workflow(CAPTURE_WORKFLOW if manual else PRIVATE_DUE_WORKFLOW).step(
        "Capture immutable forecast" if manual else "Run idempotent private due forecast"
    )
    result = run_step(
        step,
        root,
        {
            "FORECAST_PLAN_PATH": str(root / "forecast-plan.json"),
            "GITHUB_OUTPUT": str(root / "provider-output"),
            "NFL_PREDICTOR_DATA_DIR": str((root / "data").resolve()),
            "ODDS_API_KEY": "masked-test-key",
            "ODDS_BUDGET_PATH": str(root / "data" / "ledger" / "odds-budget.json"),
            "PATH": str(binary_root) + os.pathsep + os.environ["PATH"],
            "RETRY_BACKOFF_SECONDS": "0",
            "RUN_AT_UTC": "2026-09-07T00:20:00Z",
            "UNCERTAIN_ODDS": "true" if uncertain_odds else "false",
            "LATE_RETRY": "true" if late_retry else "false",
            "UV_LOG": str(log),
            "ROUTE_LOG": str(root / "route.log"),
            "TRIGGER_KIND": "workflow_dispatch" if manual else "schedule",
            "RECOVERY_EVENT_ID": "event-selected" if manual else "",
            "RECOVERY_ORIGIN": "T72" if manual else "",
            "PROVIDER_MAX_ATTEMPTS": "2",
            "CURRENT_EXECUTION": "100:1",
            "TEST_PLAN": json.dumps({"clusters": [{"target_at_utc": target}], "runs": []}),
        },
    )
    attempts = [] if not log.exists() else log.read_text(encoding="utf-8").splitlines()
    return result, attempts


def test_provider_retry_is_bounded_and_never_retries_after_uncertain_odds(
    tmp_path: Path,
) -> None:
    safe_result, safe_attempts = _run_provider_retry_probe(
        tmp_path / "safe", uncertain_odds=False
    )
    uncertain_result, uncertain_attempts = _run_provider_retry_probe(
        tmp_path / "uncertain", uncertain_odds=True
    )

    assert safe_result.returncode != 0
    assert uncertain_result.returncode != 0
    assert safe_attempts == ["live", "live"]
    assert uncertain_attempts == ["live"]
    provider_job = parse_workflow(PRIVATE_DUE_WORKFLOW).job("private-due")
    assert provider_job.timeout_minutes is not None
    assert provider_job.timeout_minutes <= 15
    for workflow_path, job_name, step_name in (
        (CAPTURE_WORKFLOW, "forecast", "Capture immutable forecast"),
        (PRIVATE_DUE_WORKFLOW, "private-due", "Run idempotent private due forecast"),
        (SETTLE_WORKFLOW, "settle", "Capture outcomes and append settlements"),
    ):
        parsed = parse_workflow(workflow_path)
        job = parsed.job(job_name)
        step = parsed.step(step_name)
        effective_environment = {**job.environment, **step.environment}
        assert effective_environment["PROVIDER_MAX_ATTEMPTS"] == "2"
        assert effective_environment["RETRY_BACKOFF_SECONDS"] == "5"
        assert job.timeout_minutes is not None and job.timeout_minutes <= 15


def test_target_plus_five_heartbeat_and_alert_are_safe_and_deduplicated(
    tmp_path: Path,
) -> None:
    import importlib.util
    from datetime import UTC, datetime, timedelta
    specification = importlib.util.spec_from_file_location("runtime_fixture", ROOT / "tests/runtime/test_services.py")
    module = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(module)
    tree = module.RuntimeTree(tmp_path)
    tree.install_due_event(kickoff_at_utc=datetime.now(UTC) + timedelta(hours=72, minutes=-6))
    data_root = tree.data_root
    config = data_root / "config/data_repo.toml"
    config.write_text(config.read_text().replace("deployment_enabled = false", "deployment_enabled = true"))
    code = tmp_path / "code"
    code.mkdir()
    shutil.copytree(ROOT / "configs", code / "configs")
    binary = tmp_path / "bin-python"
    binary.mkdir()
    wrapper = binary / "uv"
    wrapper.write_text("#!/bin/bash\nshift\nshift\nexec " + str(ROOT / ".venv/bin/python") + ' "$@"\n')
    wrapper.chmod(0o755)
    heartbeat_output = tmp_path / "heartbeat-output"
    heartbeat_output.write_text("", encoding="utf-8")
    workflow = parse_workflow(PRIVATE_DUE_WORKFLOW)
    heartbeat = run_step(
        workflow.step("Record target plus five heartbeat"),
        tmp_path,
        {
            "CODE_SHA": "a" * 40,
            "PATH": str(binary) + os.pathsep + os.environ["PATH"],
            "PYTHONPATH": str(ROOT / "src"),
            "RUNNER_TEMP": str(tmp_path),
            "GITHUB_OUTPUT": str(heartbeat_output),
            "NFL_PREDICTOR_DATA_DIR": str(data_root.resolve()),
            "PROVIDER_OUTCOME": "failure",
            "RUN_AT_UTC": "2026-09-07T00:27:00Z",
        },
    )

    assert heartbeat.returncode == 0, (heartbeat.stdout, heartbeat.stderr)
    heartbeat_files = tuple((data_root / "ledger" / "heartbeats").glob("*.json"))
    assert len(heartbeat_files) == 1
    record = json.loads(heartbeat_files[0].read_text(encoding="utf-8"))
    assert record == {
        "code_sha": "1" * 40,
        "event": "event-due",
        "origin": "T72",
        "status": "missing",
    }
    output_values = dict(
        line.split("=", 1)
        for line in heartbeat_output.read_text(encoding="utf-8").splitlines()
        if "=" in line
    )
    assert output_values["alert"] == "true"

    binary_root = tmp_path / "bin"
    binary_root.mkdir()
    gh_log = tmp_path / "gh.log"
    fake_gh = binary_root / "gh"
    fake_gh.write_text(
        textwrap.dedent(
            """\
            #!/bin/bash
            printf '%s\\n' "$*" >> "$GH_LOG"
            if [ "$1" = "issue" ] && [ "$2" = "list" ]; then
              printf '%s\\n' '[{"number":7,"title":"[NFL V2 heartbeat] event-due T72"}]'
              exit 0
            fi
            prior=""
            for argument in "$@"; do
              if [ "$prior" = "--body-file" ]; then
                cat "$argument" >> "$GH_LOG"
              fi
              prior="$argument"
            done
            """
        ),
        encoding="utf-8",
    )
    fake_gh.chmod(0o755)
    alert = run_step(
        workflow.step("Open or update deduplicated safe alert"),
        tmp_path,
        {
            "ALERTS_PATH": output_values["alerts_path"],
            "CURRENT_PRIVATE_REPOSITORY": "Su760/nfl-predictor-data",
            "GH_LOG": str(gh_log),
            "GH_TOKEN": "github-token-value",
            "ODDS_API_KEY": "must-not-appear",
            "PATH": str(binary_root) + os.pathsep + os.environ["PATH"],
        },
    )

    assert alert.returncode == 0, (alert.stdout, alert.stderr)
    alert_log = gh_log.read_text(encoding="utf-8")
    assert "issue comment 7" in alert_log
    assert "issue create" not in alert_log
    assert "must-not-appear" not in alert_log
    assert "github-token-value" not in alert_log
    assert "payload" not in alert_log.lower()
    assert "authorization" not in alert_log.lower()


def test_public_cron_exits_before_dispatch_outside_2026_and_rejects_manifest_season(
    tmp_path: Path,
) -> None:
    public = parse_workflow(PUBLIC_WORKFLOW)
    job = public.job("due-dispatch")
    year_step = public.step("Enforce 2026 UTC year lock")
    assert job.steps.index(year_step) < job.steps.index(
        public.step("Send fixed repository dispatch")
    )

    real_date = shutil.which("date")
    assert real_date is not None
    binary_root = tmp_path / "bin"
    binary_root.mkdir()
    fake_date = binary_root / "date"
    fake_date.write_text(
        textwrap.dedent(
            """\
            #!/bin/bash
            if [ "$1" = "-u" ] && [ "$2" = "+%Y" ]; then
              printf '%s\\n' "$PROBE_YEAR"
              exit 0
            fi
            exec "$REAL_DATE" "$@"
            """
        ),
        encoding="utf-8",
    )
    fake_date.chmod(0o755)
    common = {
        "PATH": str(binary_root) + os.pathsep + os.environ["PATH"],
        "REAL_DATE": real_date,
    }
    rejected = run_step(year_step, tmp_path, {**common, "PROBE_YEAR": "2027"})
    accepted = run_step(year_step, tmp_path, {**common, "PROBE_YEAR": "2026"})
    assert rejected.returncode != 0
    assert accepted.returncode == 0

    manifest_path = tmp_path / "deploy" / "schedules" / "dispatch-windows-2026.json"
    manifest_path.parent.mkdir(parents=True)
    manifest_path.write_text(
        json.dumps(
            {
                "season": 2027,
                "deployment_enabled": True,
                "generated_from_active_event_versions": True,
                "active_event_version_manifest_sha256": "a" * 64,
            }
        ),
        encoding="utf-8",
    )
    season_result = run_step(
        public.step("Fail closed on incomplete schedule"),
        tmp_path,
        {
            "EXPECTED_MANIFEST_SHA": hashlib.sha256(
                manifest_path.read_bytes()
            ).hexdigest()
        },
    )
    assert season_result.returncode != 0


@pytest.mark.parametrize("workflow_path", [CAPTURE_WORKFLOW, PRIVATE_DUE_WORKFLOW])
def test_provider_worker_depends_on_durable_execution_bound_budget_admission(workflow_path, tmp_path):
    workflow = parse_workflow(workflow_path)
    worker = workflow.job("forecast" if workflow_path == CAPTURE_WORKFLOW else "private-due")
    assert "admit-budget" in worker.needs
    admission = workflow.job("admit-budget")
    assert all("ODDS_API_KEY" not in str(step.environment) for step in admission.steps)
    check = workflow.step("Verify current admission execution")
    output = run_step(check, tmp_path, {"ADMITTED_EXECUTION": "100:1",
        "CURRENT_EXECUTION": "100:2", "ADMITTED_SHA": "a" * 40})
    assert output.returncode != 0


@pytest.mark.parametrize("workflow_path,step_name", IMMUTABLE_GUARDS.items())
def test_real_runtime_roots_are_accepted_as_new_immutable_files(workflow_path, step_name, tmp_path):
    data = _initialize_immutable_repository(tmp_path)
    (data / "config/data_repo.toml").write_bytes(DATA_REPO_CONFIG.read_bytes())
    git(data, "add", "config/data_repo.toml")
    git(data, "commit", "-qm", "reviewed template config")
    for name in ("lineage", "forecast", "outcomes-and-reports"):
        (data / name).mkdir()
        (data / name / "record.json").write_text("{}")
    result = run_step(parse_workflow(workflow_path).step(step_name), tmp_path)
    assert result.returncode == 0, result.stderr


def test_final_projection_counts_nonce_admission_workers_and_heartbeat_jobs():
    from nfl_predictor.cli import _private_budget_plan
    plan = _private_budget_plan(DATA_REPO_CONFIG.resolve(), 2026)
    assert plan["github_billed_minutes"] == 143


def test_retry_stops_when_refreshed_due_window_has_closed(tmp_path):
    result, attempts = _run_provider_retry_probe(tmp_path, uncertain_odds=False, late_retry=True)
    assert result.returncode != 0
    assert attempts == ["live"]
    assert "origin window closed" in result.stderr


def test_budget_admission_failed_push_never_exports_worker_permission(tmp_path):
    from nfl_predictor.markets.budget import AppendOnlyBudgetRepository, OddsBudget
    workspace = tmp_path / "workspace"
    data = _initialize_immutable_repository(workspace)
    budget = OddsBudget(AppendOnlyBudgetRepository(data / "ledger/budget-reservations/2026-09"), 1)
    budget.admit("2026-09", "100:1", "event:T60:policy")
    binary = tmp_path / "bin"
    binary.mkdir()
    wrapper = binary / "git"
    real_git = shutil.which("git")
    wrapper.write_text('#!/bin/bash\nif [[ " $* " == *" push "* ]]; then exit 17; fi\nexec "' + real_git + '" "$@"\n')
    wrapper.chmod(0o755)
    output = tmp_path / "output"
    output.write_text("")
    workflow = parse_workflow(PRIVATE_DUE_WORKFLOW)
    environment = {"CURRENT_EXECUTION": "100:1", "PRIVATE_DEFAULT_BRANCH": "main",
                   "GITHUB_OUTPUT": str(output), "PATH": str(binary) + os.pathsep + os.environ["PATH"]}
    failed = run_step(workflow.step("Persist execution-bound odds admission"), workspace, environment)
    assert failed.returncode == 17
    assert output.read_text() == ""
    assert budget.state("2026-09").consumed_local == 1
    # A local bare remote exercises the successful exact-SHA CAS without network.
    remote = tmp_path / "remote.git"
    subprocess.run([real_git, "init", "--bare", "-q", str(remote)], check=True)
    git(data, "remote", "add", "origin", str(remote))
    environment["PATH"] = os.environ["PATH"]
    success = run_step(workflow.step("Persist execution-bound odds admission"), workspace, environment)
    assert success.returncode == 0, success.stderr
    values = dict(line.split("=", 1) for line in output.read_text().splitlines())
    assert values["admitted"] == "true" and values["execution"] == "100:1"
    assert values["data_sha"] == git(data, "rev-parse", "HEAD").stdout.strip()
    admitted = run_step(workflow.step("Verify current admission execution"), workspace,
        {"ADMITTED_EXECUTION": "100:1", "CURRENT_EXECUTION": "100:1", "ADMITTED_SHA": values["data_sha"]})
    assert admitted.returncode == 0


def test_manual_worker_only_runs_the_selected_event_and_origin(tmp_path):
    _run_provider_retry_probe(tmp_path, uncertain_odds=False, manual=True)
    commands = (tmp_path / "route.log").read_text().splitlines()
    assert len(commands) == 2
    assert all("forecast run --event event-selected --origin T72" in command for command in commands)


def test_prior_execution_claim_does_not_suppress_current_football_retry(tmp_path):
    result, attempts = _run_provider_retry_probe(tmp_path, uncertain_odds=False, prior_claim=True)
    assert result.returncode != 0
    assert attempts == ["live", "live"]


def _bare_remote_for_jobs(tmp_path):
    workspace = tmp_path / "initial"
    data = _initialize_immutable_repository(workspace)
    remote = tmp_path / "remote.git"
    subprocess.run(["git", "init", "--bare", "-q", str(remote)], check=True)
    git(data, "remote", "add", "origin", str(remote))
    git(data, "push", "-qu", "origin", "main")
    return data, remote


def _checkout_job(remote, workspace, revision):
    workspace.mkdir(parents=True)
    subprocess.run(["git", "clone", "-q", "--no-checkout", str(remote), str(workspace / "data")], check=True)
    git(workspace / "data", "checkout", "--detach", revision)
    return workspace / "data"


def test_separate_budget_job_starts_at_pushed_nonce_revision(tmp_path):
    data, remote = _bare_remote_for_jobs(tmp_path)
    triggering_sha = git(data, "rev-parse", "HEAD").stdout.strip()
    nonce = "ledger/dispatch-nonces/" + "a" * 64 + ".nonce"
    (data / nonce).parent.mkdir()
    (data / nonce).write_text("admitted")
    output = tmp_path / "nonce-output"
    output.write_text("")
    workflow = parse_workflow(CAPTURE_WORKFLOW)
    persisted = run_step(workflow.step("Persist admitted nonce"), data.parent,
        {"NONCE_RELATIVE_PATH": nonce, "PRIVATE_DEFAULT_BRANCH": "main", "GITHUB_OUTPUT": str(output)})
    assert persisted.returncode == 0, persisted.stderr
    values = dict(line.split("=", 1) for line in output.read_text().splitlines())
    checkout = next(step for step in workflow.job("admit-budget").steps if step.inputs.get("path") == "data")
    revision = values["data_sha"] if checkout.inputs.get("ref") == "${{ needs.validate-dispatch.outputs.data_sha }}" else triggering_sha
    admission = _checkout_job(remote, tmp_path / "budget-job", revision)
    (admission / "ledger/budget.json").write_text("{}")
    result = run_step(workflow.step("Persist execution-bound odds admission"), admission.parent,
        {"PRIVATE_DEFAULT_BRANCH": "main", "CURRENT_EXECUTION": "1:1", "GITHUB_OUTPUT": str(output)})
    assert result.returncode == 0, result.stderr
    assert (admission / nonce).exists()
    assert workflow.job("validate-dispatch").outputs["data_sha"] == "${{ steps.persist.outputs.data_sha }}"


@pytest.mark.parametrize("workflow_path", [CAPTURE_WORKFLOW, PRIVATE_DUE_WORKFLOW])
def test_detached_provider_job_publishes_to_explicit_private_branch(tmp_path, workflow_path):
    initial, remote = _bare_remote_for_jobs(tmp_path)
    sha = git(initial, "rev-parse", "HEAD").stdout.strip()
    worker = _checkout_job(remote, tmp_path / "worker", sha)
    (worker / "ledger/captured.json").write_text("{}")
    result = run_step(parse_workflow(workflow_path).step("Push immutable ledger append"), worker.parent,
        {"PRIVATE_DEFAULT_BRANCH": "main"})
    assert result.returncode == 0, result.stderr
    assert git(initial, "fetch", "origin", "main").returncode == 0
    assert git(initial, "show", "origin/main:ledger/captured.json").stdout == "{}"


def test_heartbeat_serializes_after_provider_and_checks_out_fresh_branch(tmp_path):
    workflow = parse_workflow(PRIVATE_DUE_WORKFLOW)
    heartbeat = workflow.job("heartbeat")
    assert set(heartbeat.needs) == {"admit-budget", "private-due"}
    assert "always()" in heartbeat.condition
    checkout = next(step for step in heartbeat.steps if step.inputs.get("path") == "data")
    assert checkout.inputs["ref"] == "refs/heads/${{ github.event.repository.default_branch }}"
    assert parse_workflow(SETTLE_WORKFLOW).data["concurrency"]["group"] == workflow.data["concurrency"]["group"]
    initial, remote = _bare_remote_for_jobs(tmp_path)
    sha = git(initial, "rev-parse", "HEAD").stdout.strip()
    worker = _checkout_job(remote, tmp_path / "worker", sha)
    (worker / "ledger/result.json").write_text("{}")
    published = run_step(workflow.step("Push immutable ledger append"), worker.parent,
        {"PRIVATE_DEFAULT_BRANCH": "main"})
    assert published.returncode == 0, published.stderr
    # The dependent heartbeat starts only after the provider writer, from the current branch.
    monitor = _checkout_job(remote, tmp_path / "monitor", "origin/main")
    assert (monitor / "ledger/result.json").exists()
    (monitor / "ledger/heartbeat.json").write_text("{}")
    publish_heartbeat = next(step for step in heartbeat.steps if step.name == "Push immutable ledger append")
    result = run_step(publish_heartbeat, monitor.parent, {"PRIVATE_DEFAULT_BRANCH": "main"})
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("failure", ["missing_manifest", "missing_event", "understated_requests"])
def test_explicit_private_planner_requires_actual_event_bytes_and_request_count(tmp_path, failure):
    import importlib.util

    from nfl_predictor.cli import _private_budget_plan, main
    specification = importlib.util.spec_from_file_location("planner_fixture", ROOT / "tests/runtime/test_services.py")
    module = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(module)
    tree = module.RuntimeTree(tmp_path)
    tree.install_due_event()
    active_sha = hashlib.sha256(tree.active_manifest.read_bytes()).hexdigest()
    manifest = json.loads(PUBLIC_MANIFEST.read_text())
    manifest.update(deployment_enabled=True, generated_from_active_event_versions=True,
                    active_event_version_manifest_sha256=active_sha)
    tree.dispatch_manifest.write_text(json.dumps(manifest))
    manifest_sha = hashlib.sha256(tree.dispatch_manifest.read_bytes()).hexdigest()
    config_path = tree.data_root / "config/data_repo.toml"
    text = DATA_REPO_CONFIG.read_text().replace("deployment_enabled = false", "deployment_enabled = true")
    text = text.replace('active_event_version_manifest_sha256 = ""', f'active_event_version_manifest_sha256 = "{active_sha}"')
    text = text.replace('generated_from_active_event_versions = false', 'generated_from_active_event_versions = true')
    text = text.replace('d84ef7a4522608997477c0a81ad2f5c8037841af9b87f3867d6a77bae400cccd', manifest_sha)
    text = text.replace('verified_included_private_actions_minutes = 0', 'verified_included_private_actions_minutes = 200')
    text = text.replace('verified_included_storage_bytes = 0', 'verified_included_storage_bytes = 104857600')
    text = text.replace('projected_paid_actions_minutes = 143', 'projected_paid_actions_minutes = 0')
    text = text.replace('projected_paid_storage_bytes = 104857600', 'projected_paid_storage_bytes = 0')
    if failure == "understated_requests":
        text = text.replace('projected_odds_requests = 6', 'projected_odds_requests = 0')
    elif failure == "missing_manifest":
        tree.active_manifest.unlink()
    else:
        tree.event_path.unlink()
    config_path.write_text(text)
    result = _private_budget_plan(config_path, 2026)
    assert result["deployment_allowed"] is False
    expected = "STORED_ODDS_REQUEST_PROJECTION_MISMATCH" if failure == "understated_requests" else "ACTIVE_EVENT_VERSION_SCHEDULE_UNAVAILABLE"
    assert expected in result["blockers"]
    assert main(["odds", "budget-plan", "--season", "2026", "--private-config", str(config_path)],
                environment={}) == 1
