# Task 12B Production Runtime and Task 13 Acceptance Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace Task 12's test-only forecast/outcome collaborators with fail-closed production adapters and a real CLI composition root, then close all eleven Task 13 acceptance findings without enabling deployment or consuming provider credits.

**Architecture:** A new `nfl_predictor.runtime` package composes the existing immutable workflow, storage, feature, model, odds, settlement, and report primitives. It reads only an explicit private data root, verifies active-schedule and artifact-registry bytes before use, persists normalized lineage through `LedgerStore`, and keeps network-capable adapters behind existing policy and authorization gates. Task 13 templates then invoke this composition root with the private checkout as the data root and enforce identity, nonce, cost, schedule, retry, and immutable-change controls before any secret-bearing step.

**Tech Stack:** Python 3.11, Pydantic 2, Polars/Arrow IPC, DuckDB, NumPy, joblib, httpx, pytest, GitHub Actions YAML, TOML, immutable content-addressed ledgers.

**Spec:** `/Users/supashramesha/nfl-predictor/docs/superpowers/specs/2026-08-28-nfl-predictor-v2-design.md`; acceptance evidence: `/Users/supashramesha/nfl-predictor/.superpowers/sdd/2026-08-28-nfl-predictor-v2/task-13-review.md`

## Global Constraints

- Preserve the legacy checkout and all existing user changes; execute only in `/Users/supashramesha/.codex/worktrees/nfl-predictor-v2/nfl-predictor`.
- Do not stage, commit, push, create repositories, configure secrets, enable Actions, deploy, spend money, call live providers, place wagers, or mutate external systems without separate explicit authorization.
- All tests use synthetic/recorded payloads and disable network access.
- Production operation requires an absolute private data root outside `code_root`; relative or code-contained data roots fail closed.
- Raw captures are durable before normalization; typed normalized records retain exact capture IDs and raw SHA-256 lineage.
- Grade A facts must have `available_at_utc <= decision_at_utc` and `captured_at_utc <= decision_at_utc`.
- Each origin requires exactly one frozen, verified champion binding; no artifact payload loads before marker, metadata, payload, registry, code, and dependency-lock checks pass.
- Odds capture stays disabled unless the reviewed private policy enables it, the 400-credit projection passes, a frozen book/settlement allowlist exists, and the credential is present.
- Missing or rejected odds degrade to `FOOTBALL_ONLY`; missing football lineage, artifacts, or active schedule fail the required lane.
- No automatic wagering or paid usage is introduced.
- The public/private workflows remain disabled and zero-dollar by default.
- Because commits were not authorized, every task ends at an unstaged review checkpoint rather than a commit.

---

## Root cause and acceptance behavior

`ForecastWorkflow` requires seven injected collaborators, but the repository supplies only test fakes for required capture, lineage resolution, snapshot construction, artifact selection, prediction, and market evaluation. `OutcomeWorkflow` receives a JSON-only adapter while the real nflverse source returns Arrow IPC. `cli.main()` therefore accepts injected services in tests but deliberately exits for production routes. Task 13 templates cannot safely run a workflow that has no real composition root.

The implementation is complete only when an offline fixture exercises this concrete chain with no fake workflow collaborators:

```text
Arrow/JSON source bytes -> raw CaptureService evidence -> durable normalized lineage
-> FeatureBuilder -> verified frozen artifact -> calibrated three-way Prediction
-> optional normalized market evaluation -> DurableForecastRepository
-> Arrow outcome capture -> DurableOutcomeReportRepository -> ReportWorkflow
```

The same test must prove that a missing/tampered schedule, lineage record, artifact, registry, or policy fails closed and that disabled odds still produces a football-only forecast.

## Locked file map

### Task 12B files

- Create `src/nfl_predictor/runtime/__init__.py` — public runtime exports only.
- Create `src/nfl_predictor/runtime/lineage.py` — typed durable lineage lookup, active-event loading, and point-in-time fact access.
- Create `src/nfl_predictor/runtime/capture.py` — Arrow football/outcome normalization plus required football and optional odds capture adapters.
- Create `src/nfl_predictor/runtime/artifacts.py` — frozen registry verification and calibrated three-way predictor.
- Create `src/nfl_predictor/runtime/markets.py` — comparator/candidate composition over Task 11 policies.
- Create `src/nfl_predictor/runtime/services.py` — production service registry and CLI composition root.
- Modify `src/nfl_predictor/config.py` — absolute private-root and containment validation.
- Modify `src/nfl_predictor/cli.py` — construct production services only from explicit reviewed runtime configuration.
- Create `tests/runtime/test_lineage.py`.
- Create `tests/runtime/test_capture.py`.
- Create `tests/runtime/test_artifacts.py`.
- Create `tests/runtime/test_markets.py`.
- Create `tests/runtime/test_services.py`.
- Modify `tests/test_config.py`.
- Modify `tests/test_cli.py`.
- Modify `tests/integration/test_fixture_pipeline.py` — replace test-only forecast collaborators with the production runtime adapters.

### Task 13 acceptance files

- Modify `src/nfl_predictor/workflows/dispatch.py`.
- Modify `src/nfl_predictor/workflows/schedule_windows.py`.
- Modify `src/nfl_predictor/cli.py`.
- Modify `.github/workflows/nfl-v2-dispatch.yml`.
- Modify `deploy/schedules/dispatch-windows-2026.json`.
- Modify `deploy/private-data-repo/README.md`.
- Modify `deploy/private-data-repo/config/data_repo.toml`.
- Modify `deploy/private-data-repo/config/dispatch-windows-2026.json`.
- Modify `deploy/private-data-repo/.github/workflows/capture-and-forecast.yml`.
- Modify `deploy/private-data-repo/.github/workflows/private-due-check.yml`.
- Modify `deploy/private-data-repo/.github/workflows/settle-and-report.yml`.
- Modify `docs/runbooks/nfl-v2-operations.md`.
- Modify `tests/workflows/test_schedule_windows.py`.
- Modify `tests/workflows/test_dispatch.py`.
- Modify `tests/deploy/test_workflows.py`.

No other product, configuration, test, workflow, or documentation file is in scope. If an interface below is disproven by the current code or official nflreadpy schema, stop and report found-versus-claimed before editing.

---

### Task 1: Private runtime paths, active events, and durable lineage

**Files:**

- Create: `src/nfl_predictor/runtime/__init__.py`
- Create: `src/nfl_predictor/runtime/lineage.py`
- Modify: `src/nfl_predictor/config.py`
- Create: `tests/runtime/test_lineage.py`
- Modify: `tests/test_config.py`

**Interfaces:**

- Consumes: `AppConfig`, `LedgerStore`, `CaptureManifest`, `NormalizedFact`, `EventVersion`, `DuckDbPointInTimeStore`.
- Produces: `RuntimePaths.from_config(config)`, `DurableLineageRepository.append_manifests(...)`, `append_facts(...)`, `append_events(...)`, `append_capture_batch(...)`, `replay_capture_batch(...)`, `resolve_manifests(...)`, `resolve_facts(...)`, `for_event_features(...)`, and `load_active_events(manifest_path, expected_sha256)`.

- [x] **Step 1: Write failing private-root tests**

```python
def test_config_rejects_relative_environment_data_root(tmp_path, monkeypatch):
    monkeypatch.setenv("NFL_PREDICTOR_DATA_DIR", "data")
    with pytest.raises(ValueError, match="absolute"):
        load_app_config(tmp_path / "base.toml")


def test_config_rejects_data_root_inside_code_checkout(config_file, monkeypatch):
    monkeypatch.setenv("NFL_PREDICTOR_DATA_DIR", str(PROJECT_ROOT / "data"))
    with pytest.raises(ValueError, match="outside code_root"):
        load_app_config(config_file)
```

- [x] **Step 2: Write failing lineage-integrity tests**

```python
def test_active_schedule_hashes_actual_manifest_and_event_bytes(runtime_fixture):
    manifest = runtime_fixture.install_active_events()
    events = runtime_fixture.lineage.load_active_events(manifest, sha256(manifest.read_bytes()).hexdigest())
    assert [event.canonical_event_id for event in events] == ["event-1"]
    runtime_fixture.event_file.write_bytes(b"tampered")
    with pytest.raises(DataIntegrityError, match="active event bytes"):
        runtime_fixture.lineage.load_active_events(manifest, sha256(manifest.read_bytes()).hexdigest())


def test_lineage_resolution_rejects_substituted_logical_ids(runtime_fixture):
    fact = runtime_fixture.fact()
    runtime_fixture.lineage.append_facts((fact,))
    runtime_fixture.rewrite_fact_object_id(fact.fact_id, "different-id")
    with pytest.raises(DataIntegrityError, match="logical ID"):
        runtime_fixture.lineage.resolve_facts([fact.fact_id])
```

- [x] **Step 3: Run the focused tests and verify RED**

Run: `uv run pytest tests/test_config.py tests/runtime/test_lineage.py -q`

Expected: failures because the runtime repository and strict root checks do not exist.

- [x] **Step 4: Implement strict path construction**

```python
@dataclass(frozen=True)
class RuntimePaths:
    data_root: Path
    lineage_root: Path
    prospective_forecast_root: Path
    replay_forecast_root: Path
    outcome_report_root: Path
    artifact_root: Path

    @classmethod
    def from_config(cls, config: AppConfig) -> "RuntimePaths":
        data_root = config.data_root.resolve(strict=True)
        code_root = config.code_root.resolve(strict=True)
        if data_root == code_root or code_root in data_root.parents:
            raise ValueError("private data_root must be outside code_root")
        return cls(
            data_root=data_root,
            lineage_root=data_root / "lineage",
            prospective_forecast_root=data_root / "forecast" / "prospective",
            replay_forecast_root=data_root / "forecast" / "replay",
            outcome_report_root=data_root / "outcomes-and-reports",
            artifact_root=data_root / "artifacts",
        )
```

`load_app_config` must reject a relative `NFL_PREDICTOR_DATA_DIR`, resolve the configured TOML path relative to the TOML file rather than the current working directory, and reject any resolved data root equal to or nested under `code_root`.

- [x] **Step 5: Implement typed immutable lineage resolution**

Use one `LedgerStore` namespace per type: `capture-manifest-v1`, `normalized-fact-v1`, and `event-version-v1`. Every append key is the contract's logical ID; every read reparses the exact Pydantic type and checks the parsed ID equals the requested key. `for_event_features` enumerates verified normalized-fact markers, reparses each object, then delegates temporal filtering/deduplication to `DuckDbPointInTimeStore`.

```python
class DurableLineageRepository:
    def resolve_facts(self, ids: list[str]) -> list[NormalizedFact]:
        return [self._fact(identifier) for identifier in ids]

    def resolve_manifests(self, ids: list[str]) -> list[CaptureManifest]:
        return [self._manifest(identifier) for identifier in ids]

    def for_event_features(self, event: EventVersion, cutoff: datetime) -> list[NormalizedFact]:
        return DuckDbPointInTimeStore(list(self.iter_facts())).for_event_features(event, cutoff)
```

The active-event manifest contains exactly `schema_version`, `season`, and ordered `files`; each file has `path`, `sha256`, and ordered event keys. Paths must be safe relative paths beneath `data_root`. Verify the manifest SHA, every file SHA, exact Parquet schema, unique `(canonical_event_id, event_version)`, one active version per event, and season agreement before returning events.

- [x] **Step 6: Run Task 1 tests and static checks**

Run:

```bash
uv run pytest tests/test_config.py tests/runtime/test_lineage.py -q
uv run ruff check src/nfl_predictor/config.py src/nfl_predictor/runtime/lineage.py tests/test_config.py tests/runtime/test_lineage.py
uv run mypy src/nfl_predictor/config.py src/nfl_predictor/runtime/lineage.py
```

Expected: PASS, with the tamper and containment cases rejected.

- [x] **Step 7: Review checkpoint**

Run: `git diff -- src/nfl_predictor/config.py src/nfl_predictor/runtime tests/test_config.py tests/runtime/test_lineage.py`

Expected: only the five Task 1 files; leave them unstaged.

---

### Task 2: Production football, odds, replay, and outcome capture adapters

**Files:**

- Create: `src/nfl_predictor/runtime/capture.py`
- Create: `tests/runtime/test_capture.py`

**Interfaces:**

- Consumes: `CaptureService`, `NflverseAdapter`, `OddsApiAdapter`, `OutcomeAdapter`, `normalize_h2h`, `OddsBudget`, `DurableLineageRepository`, active `EventVersion` records.
- Produces: `NflverseFootballNormalizer.normalize_events(payload, manifest)`, `normalize_facts(dataset, payload, manifest, target_event)`, `RequiredFootballCapture.capture_live(...)`, `capture_replay(...)`, `OptionalOddsCapture.enabled`, `capture(...)`, and `ArrowOutcomeAdapter.capture(...)`.

- [x] **Step 1: Write failing Arrow normalization tests**

Build tiny Polars frames with the frozen V1 columns below and serialize them with `write_ipc`:

- schedules: `game_id`, `season`, `game_type`, `week`, `gameday`, `gametime`, `away_team`, `home_team`, `away_score`, `home_score`, `location`, `div_game`, `stadium_id`;
- play by play: `game_id`, `play_id`, `posteam`, `defteam`, `passer_player_id`, `pass_attempt`, `rush_attempt`, `epa`, `qb_epa`, `cpoe`.

The normalizer rejects an IPC file missing any of these fields. The fixture includes a finalized prior game, both offenses, quarterback pass attempts, the target schedule row, prior-season rows for priors, and a division flag, then asserts exact facts:

```python
def test_nflverse_ipc_normalizes_complete_feature_fact_families(capture_fixture):
    bundle = capture_fixture.required.capture_live(capture_fixture.obligation, "attempt-1")
    facts = [row for row in bundle.records if isinstance(row, NormalizedFact)]
    assert {fact.fact_type for fact in facts} == {
        "completed_game", "division_alignment", "league_strength_prior",
        "qb_trailing", "team_game_epa", "team_passing_prior", "team_strength_prior",
    }
    assert {fact.capture_id for fact in facts} <= {
        row.capture_id for row in bundle.records if isinstance(row, CaptureManifest)
    }
    assert all(fact.provenance_grade is ProvenanceGrade.A for fact in facts)
```

The test fixtures must include a finalized prior game, both team-stat rows, both quarterback rows, the target schedule row, prior-season priors, and a division flag. Add separate tests for missing required columns, non-final current-game leakage, duplicate provider IDs, and a row timestamp after the capture receipt.

- [x] **Step 2: Write failing replay and odds-budget tests**

```python
def test_replay_uses_only_pre_cutoff_archived_capture_batch(capture_fixture):
    bundle = capture_fixture.required.capture_replay(
        capture_fixture.obligation, "replay-1", capture_fixture.cutoff
    )
    assert all(record.captured_at_utc <= capture_fixture.cutoff for record in bundle.records if isinstance(record, NormalizedFact))
    assert capture_fixture.network_attempts == 0


def test_odds_reservation_precedes_adapter_and_uncertain_request_is_consumed(odds_fixture):
    odds_fixture.adapter.raise_after_send = True
    with pytest.raises(TimeoutError):
        odds_fixture.capture.capture(odds_fixture.obligation, "attempt-1")
    assert odds_fixture.budget.state().consumed == 1
    assert odds_fixture.adapter.calls == 1
```

- [x] **Step 3: Write failing Arrow outcome test**

```python
def test_arrow_outcome_adapter_selects_exact_source_event(outcome_fixture):
    captured = outcome_fixture.adapter.capture(
        {"dataset": "schedules", "seasons": [2026], "source_event_id": "2026_01_GB_CHI"},
        "outcome-run-1",
    )
    assert captured.observation.game_status == "final"
    assert captured.observation.home_score == 20
    assert captured.observation.away_score == 17
    assert captured.observation.raw_payload_sha256 == captured.manifest.raw_payload_sha256
```

- [x] **Step 4: Run focused capture tests and verify RED**

Run: `uv run pytest tests/runtime/test_capture.py -q`

Expected: failures because no production runtime adapters exist.

- [x] **Step 5: Implement schema-closed nflverse normalization**

Decode IPC with `polars.read_ipc(BytesIO(payload))`. Each dataset declares a frozen required-column set and rejects missing columns, duplicate provider row identities, non-finite metrics, invalid teams, impossible scores, and timestamps after `manifest.response_received_at_utc`. Generate `fact_id` and `fact_content_sha256` from canonical JSON containing fact type, entity keys, payload, source provider ID, observation time, availability time, capture ID, and schema version.

`normalize_events` emits canonical `EventVersion` candidates from schedule rows and binds `stadium_id` to `venue_id`; schedule activation remains a separately reviewed manifest operation. Finalized schedules emit one equal `completed_game` fact per participating team, with both `canonical_event_id` and `team` entity keys. Play-by-play rows emit two directional `team_game_epa` facts per completed game with `team=offense_team`, plus team/game `qb_trailing`. Prior-season schedule/PBP aggregates emit frozen per-team `team_passing_prior` and `team_strength_prior`; the target capture emits one `league_strength_prior` and one `division_alignment`, each carrying the target `canonical_event_id` so the existing event-scoped fact query selects it. Team/league strength priors use the existing Elo, Colley, Massey, and opponent-adjusted EPA functions on prior-season completed games and PBP aggregates. Do not normalize the target game's score or EPA.

- [x] **Step 6: Implement raw-first live and replay football capture**

```python
class RequiredFootballCapture:
    DATASETS = ("schedules", "pbp")

    def capture_live(self, obligation: OriginObligation, attempt_id: str) -> CaptureBundle:
        manifests, facts = self._capture_datasets(
            seasons=(obligation.event.season - 1, obligation.event.season),
            event=obligation.event,
            run_id=attempt_id,
        )
        self.lineage.append_manifests(manifests)
        self.lineage.append_facts(facts)
        self.lineage.append_capture_batch(obligation, attempt_id, manifests, facts)
        return CaptureBundle(tuple((*manifests, *facts)), {"fact_ids": [f.fact_id for f in facts]})

    def capture_replay(self, obligation, attempt_id, cutoff):
        return self.lineage.replay_capture_batch(obligation, attempt_id, cutoff)
```

The live method must verify every `CaptureService` raw file and manifest before publishing facts. Replay never calls a source adapter: it verifies an archived batch captured no later than the cutoff, appends reconstruction manifests with the current attempt ID and the original raw path/hash/timestamps, and appends new Grade C facts whose IDs bind the reconstruction reason and archived fact ID. This makes the workflow's current-attempt manifest rule and Grade C snapshot rule both true without rewriting historical evidence.

- [x] **Step 7: Implement optional odds capture with durable budget ordering**

`OptionalOddsCapture.enabled` is true only when the loaded `OddsPolicy.capture_enabled` is true and the runtime has a nonblank key. `capture` reserves one credit transactionally before calling `OddsApiAdapter`, consumes uncertain requests, finalizes from allowlisted quota headers, normalizes with `normalize_h2h`, persists the manifest and accepted quotes, and returns rejections only as non-secret reason codes. A disabled policy raises `MarketCaptureDisabled` before constructing an HTTP client.

- [x] **Step 8: Implement Arrow outcome adaptation**

Subclass `OutcomeAdapter` and override normalization to select exactly one schedule row matching request `source_event_id`. Map official terminal/nonterminal states to the existing five-state outcome contract; use retrieval time when nflverse supplies no documented earlier source snapshot; retain the raw capture hash and build identity exactly.

- [x] **Step 9: Run Task 2 tests and static checks**

Run:

```bash
uv run pytest tests/runtime/test_capture.py tests/capture tests/sources tests/markets/test_budget.py tests/markets/test_normalizer.py -q
uv run ruff check src/nfl_predictor/runtime/capture.py tests/runtime/test_capture.py
uv run mypy src/nfl_predictor/runtime/capture.py
```

Expected: PASS with zero socket/DNS attempts.

- [x] **Step 10: Review checkpoint**

Run: `git diff -- src/nfl_predictor/runtime/capture.py tests/runtime/test_capture.py`

Expected: only the two Task 2 files; leave them unstaged.

**Acceptance evidence (2026-09-03):** The approved Task 2 amendment closed batch-gated
lineage, relationship point-in-time selection, football capture, odds finalization, frozen
outcome schema, and durable replay isolation across commits `436b457..589e8f3`. Independent
acceptance re-review found all three final Important findings addressed with no new Critical,
Important, or Minor breakage. Fresh integration verification at `589e8f3` reported 941 tests
passing, scoped V2 Ruff clean, and mypy clean across 78 source files. The whole-repository Ruff
probe still reports 31 pre-existing findings in four legacy root scripts, outside this task's
locked files.

---

### Task 3: Frozen artifact registry, calibrated predictor, and market layer

**Files:**

- Create: `src/nfl_predictor/runtime/artifacts.py`
- Create: `src/nfl_predictor/runtime/markets.py`
- Create: `tests/runtime/test_artifacts.py`
- Create: `tests/runtime/test_markets.py`

**Interfaces:**

- Consumes: `ArtifactStore`, `ArtifactMetadata`, `ProbabilityModel`, calibrator `transform`, `TieLayer`, `FEATURE_SCHEMA_V1`, `ArtifactBinding`, `OddsPolicy`, `CandidatePolicy`, and `build_market_comparator`.
- Produces: `FrozenForecastArtifact`, `VerifiedArtifactRegistry.from_private_config(private_root, artifact_root, registry_path, expected_registry_sha256)`, `for_origin(origin)`, `VerifiedForecastPredictor.predict(...)`, bounded `DurableCalibrationBins.lookup(...)`, and `ProductionMarketLayer.evaluate(...)`.

- [x] **Step 1: Write failing registry and tamper tests**

```python
def test_registry_returns_one_verified_frozen_champion_per_origin(artifact_fixture):
    bindings = artifact_fixture.registry.for_origin(Origin.T60)
    assert [item.model_role for item in bindings].count("champion") == 1
    assert all(item.frozen and item.verified for item in bindings)


def test_registry_rejects_registry_or_artifact_tamper(artifact_fixture):
    artifact_fixture.registry_path.write_bytes(b"{}")
    with pytest.raises(ArtifactIntegrityError):
        artifact_fixture.registry.for_origin(Origin.T60)
```

Cover duplicate champions, origin mismatch, uncommitted artifact, wrong code SHA, wrong dependency-lock SHA, wrong feature policy/schema, unknown role, and a registry SHA that does not match the reviewed private configuration.

- [x] **Step 2: Write failing probability and market tests**

```python
def test_predictor_uses_schema_order_calibrator_and_tie_layer(runtime_prediction_fixture):
    prediction = runtime_prediction_fixture.predict()
    assert prediction.p_home == Decimal("0.588")
    assert prediction.p_away == Decimal("0.392")
    assert prediction.p_tie == Decimal("0.020")
    assert prediction.p_home + prediction.p_away + prediction.p_tie == Decimal(1)


def test_market_layer_returns_comparator_and_both_audited_decisions(market_fixture):
    evaluation = market_fixture.layer.evaluate(
        market_fixture.prediction, market_fixture.quotes, market_fixture.decision_at
    )
    assert evaluation.comparator is not None
    assert {decision.side for decision in evaluation.decisions} == {"home", "away"}
    assert all(candidate.fixed_stake_units == Decimal(1) for candidate in evaluation.candidates)
```

- [x] **Step 3: Run focused tests and verify RED**

Run: `uv run pytest tests/runtime/test_artifacts.py tests/runtime/test_markets.py -q`

Expected: failures because the production registry/predictor/market adapters do not exist.

- [x] **Step 4: Implement a verified frozen artifact bundle and registry**

```python
@dataclass(frozen=True)
class FrozenForecastArtifact:
    model: ProbabilityModel
    calibrator: object
    calibrator_artifact_id: str
    tie_layer: TieLayer


class VerifiedArtifactRegistry:
    def for_origin(self, origin: Origin) -> tuple[ArtifactBinding, ...]:
        document = self._load_and_hash_registry()
        entries = tuple(entry for entry in document.entries if entry.origin is origin)
        bindings = tuple(self._verify_entry(entry) for entry in entries)
        if sum(binding.model_role == "champion" for binding in bindings) != 1:
            raise ArtifactIntegrityError("origin requires exactly one champion")
        return bindings
```

The registry document has an exact Pydantic schema and is hash-pinned by runtime configuration. Each entry independently anchors marker, metadata, and payload bytes. `_verify_entry` verifies those exact bytes plus metadata origin/lane/policy/code/lock values before deserializing the already-verified in-memory payload, requires `FrozenForecastArtifact`, and creates `ArtifactBinding(frozen=True, verified=True, ...)`. Artifact and registry paths must resolve under the explicit trusted private root, including after symlink resolution. Never load an artifact solely because a registry entry names it.

- [x] **Step 5: Implement deterministic prediction**

Create a `float64` row in exact `FEATURE_SCHEMA_V1` order, call `model.predict_r_home`, call the frozen calibrator's `transform`, then `to_three_way(calibrated_r_home, tie_layer.p_tie)`. Convert probabilities through `Decimal(str(value))`, quantize only by the existing contract tolerance, and generate the prediction ID from canonical origin-run/artifact/snapshot/policy lineage. Populate every field checked by `ForecastWorkflow._validate_prediction`; replay carries the context reconstruction reason.

- [x] **Step 6: Implement the market composition**

`ProductionMarketLayer` resolves the exact active event, constructs `OfficialEventState(status="pregame")`, derives one trusted provider source match from the normalized quotes, builds the comparator from the selected capture, and delegates both sides to `CandidatePolicy`. Persist calibration evidence and local frozen probability-bin bounds behind `DurableCalibrationBins`; only the unique containing `[lower, upper)` bin (with `1.0` admitted by the final upper-1 bin) that is frozen, out-of-sample, same-origin, and strictly before the decision can be returned. The evidence path must remain under the explicit trusted private root. Return all selected quotes, rejected/candidate decisions, and only non-null displayed candidates in `MarketEvaluation`.

- [x] **Step 7: Run Task 3 tests and static checks**

Run:

```bash
uv run pytest tests/runtime/test_artifacts.py tests/runtime/test_markets.py tests/models tests/markets tests/betting -q
uv run ruff check src/nfl_predictor/runtime/artifacts.py src/nfl_predictor/runtime/markets.py tests/runtime/test_artifacts.py tests/runtime/test_markets.py
uv run mypy src/nfl_predictor/runtime/artifacts.py src/nfl_predictor/runtime/markets.py
```

Expected: PASS, including tamper and exact-vector tests.

- [x] **Step 8: Review checkpoint**

Run: `git diff -- src/nfl_predictor/runtime/artifacts.py src/nfl_predictor/runtime/markets.py tests/runtime/test_artifacts.py tests/runtime/test_markets.py`

Expected: only the four Task 3 files; leave them unstaged.

**Acceptance evidence (2026-09-04):** Commits `38ee636..f9dec01` add the exact four-file
runtime surface. Independent review's 2 Critical and 2 Important findings were resolved in
one fix round: no serialized payload is used before independently anchored registry/content,
code, and dependency-lock verification; every runtime path is private-root-contained; and
calibration evidence is selected by probability-bin membership. Re-review approved all five
finding/test items with no new breakage. Fresh controller verification passed 309 focused and
962 full offline tests, scoped Ruff, mypy, and diff checks. One nonblocking coverage note remains
for the directly enforced off-root `artifact_root` branch.

---

### Task 4: Production service registry and concrete CLI composition

**Files:**

- Create: `src/nfl_predictor/runtime/services.py`
- Modify: `src/nfl_predictor/runtime/__init__.py`
- Modify: `src/nfl_predictor/cli.py`
- Create: `tests/runtime/test_services.py`
- Modify: `tests/test_cli.py`
- Modify: `tests/integration/test_fixture_pipeline.py`

**Interfaces:**

- Consumes: Tasks 1–3, all Task 12 workflows/repositories, schedule-window functions, dispatch functions, policy loaders, and injected clocks/HTTP clients.
- Produces: `ProductionRuntime`, `build_production_runtime(config_path, environment, clock, clients=None)`, a populated `ServiceRegistry`, and a `SchedulerFreshnessPolicy`.

- [ ] **Step 1: Write failing composition and fail-closed tests**

```python
def test_build_production_runtime_populates_every_service(runtime_tree):
    runtime = build_production_runtime(runtime_tree.base_config, runtime_tree.environment, runtime_tree.clock)
    assert runtime.services.schedule_render_dispatch is not None
    assert runtime.services.dispatch_due is not None
    assert callable(runtime.services.forecast_run)
    assert callable(runtime.services.outcomes_sync)
    assert callable(runtime.services.report_weekly)


@pytest.mark.parametrize("missing", ["active_schedule", "artifact_registry", "dependency_lock"])
def test_readiness_fails_closed_on_missing_or_tampered_binding(runtime_tree, missing):
    runtime_tree.break_binding(missing)
    result = runtime_tree.runtime().services.readiness_check(context=runtime_tree.context)
    assert result["ready"] is False
    assert result["blockers"]
```

- [ ] **Step 2: Write failing CLI auto-composition tests**

```python
def test_cli_builds_production_services_only_with_explicit_runtime_config(runtime_tree, capsys):
    code = main(
        ["readiness", "check"],
        environment={"NFL_V2_RUNTIME_CONFIG": str(runtime_tree.base_config)},
        clock=runtime_tree.clock,
    )
    assert code == 0
    assert json.loads(capsys.readouterr().out)["route"] == "readiness.check"


def test_cli_does_not_fall_back_to_preview_for_live_forecast(monkeypatch):
    with pytest.raises(SystemExit) as error:
        main(["forecast", "due", "--trigger", "manual"])
    assert error.value.code == 2
```

- [ ] **Step 3: Replace fixture-pipeline fakes with runtime adapters**

The integration test may keep deterministic source clients and a temporary frozen artifact, but must instantiate `DurableLineageRepository`, `RequiredFootballCapture`, `OptionalOddsCapture`, `FeatureBuilder`, `VerifiedArtifactRegistry`, `VerifiedForecastPredictor`, `ProductionMarketLayer`, `DurableForecastRepository`, `ArrowOutcomeAdapter`, `DurableOutcomeReportRepository`, and `ReportWorkflow` from production modules. Delete `FixtureLineageRepository`, `RequiredCapture`, `FixtureMarketCapture`, `Builder`, `Registry`, `Predictor`, and `FixtureMarketLayer` from the test.

- [ ] **Step 4: Run service/CLI/integration tests and verify RED**

Run: `uv run pytest tests/runtime/test_services.py tests/test_cli.py tests/integration/test_fixture_pipeline.py -q`

Expected: failures because no production composition root exists.

- [ ] **Step 5: Implement the composition root**

```python
@dataclass(frozen=True)
class ProductionRuntime:
    services: ServiceRegistry
    scheduler_policy: SchedulerFreshnessPolicy


def build_production_runtime(
    config_path: Path,
    environment: Mapping[str, str],
    clock: Callable[[], datetime],
    clients: RuntimeClients | None = None,
) -> ProductionRuntime:
    app = load_app_config(config_path)
    paths = RuntimePaths.from_config(app)
    lineage = DurableLineageRepository(paths.lineage_root, paths.data_root)
    artifact_registry = VerifiedArtifactRegistry.from_private_config(
        paths.artifact_root, paths.data_root / "config" / "artifact-registry.json"
    )
    feature_builder = FeatureBuilder(
        lineage,
        load_feature_policy(app.code_root / "configs" / "feature_policy_v1.toml"),
        VenueStore.from_csv(app.code_root / "configs" / "venues_v1.csv"),
        PointInTimeRatingService(
            policy=load_feature_policy(app.code_root / "configs" / "feature_policy_v1.toml")
        ),
    )
    components = RuntimeComponents.build(
        app=app,
        paths=paths,
        lineage=lineage,
        artifact_registry=artifact_registry,
        feature_builder=feature_builder,
        environment=environment,
        clock=clock,
        clients=clients,
    )
    return ProductionRuntime(
        services=components.service_registry(),
        scheduler_policy=components.scheduler_freshness_policy,
    )
```

`RuntimeComponents.build` is defined in the same file as a frozen dataclass whose fields are the concrete `RequiredFootballCapture`, `OptionalOddsCapture`, `VerifiedForecastPredictor`, `ProductionMarketLayer`, `ForecastWorkflow`, `ArrowOutcomeAdapter`, `OutcomeWorkflow`, `ReportWorkflow`, and durable repositories. Its `service_registry()` binds its concrete methods directly to all ten `ServiceRegistry` fields. It cannot accept collaborator callbacks or `_unavailable_service`. `RuntimeClients` is an optional test injection for nflverse loaders, Odds API HTTP client, and repository-dispatch HTTP client; production defaults use the real adapters only after route-specific gates pass.

- [ ] **Step 6: Implement every service over concrete workflow objects**

- `schedule_sync`: raw-captures nflverse schedules, reconciles candidate event versions, writes an immutable candidate active-event manifest, and returns its hashes without activating it.
- `forecast_due`: loads the verified active schedule, computes due clusters, creates obligations, and calls `ForecastWorkflow.run` idempotently.
- `forecast_run`: resolves one active event/origin and calls the same workflow.
- `outcomes_sync`: captures exact active events through the requested week with `ArrowOutcomeAdapter` and `OutcomeWorkflow`.
- `settle`: imports any missing committed forecast graphs and returns the durable settlement reconciliation summary; it does not recapture odds.
- `report_weekly`: calls `ReportWorkflow` against `DurableOutcomeReportRepository`.
- `odds_budget_plan`: loads the deployed private policy/config, recomputes request and GitHub/storage projections, and returns `deployment_allowed=False` with blockers when any cap fails.
- `readiness_check`: hashes active schedule bytes, registry bytes, artifacts, dependency lock, policies, data-root containment, budget, and deployment gates.
- `schedule_render_dispatch`: returns deterministic public/private cron documents derived from verified active events.
- `dispatch_due`: dry-run returns exact due envelopes; a live call requires `DispatchAuthorization`, validates caps again, and sends only `repository_dispatch_request(...)`.

- [ ] **Step 7: Wire CLI auto-composition without weakening injection tests**

When `services is None`, check only the passed `environment` mapping for `NFL_V2_RUNTIME_CONFIG`. If present, build the production runtime and use its scheduler policy. If absent, retain the current three offline previews and reject every mutating/live production route. Never read an implicit local config for a live route.

- [ ] **Step 8: Run Task 4 tests and full Task 12 matrix**

Run:

```bash
uv run pytest tests/runtime/test_services.py tests/test_cli.py tests/integration/test_fixture_pipeline.py -q
uv run pytest tests/workflows/test_forecast.py tests/workflows/test_outcomes.py tests/workflows/test_report.py -q
uv run ruff check src/nfl_predictor/runtime src/nfl_predictor/cli.py tests/runtime tests/test_cli.py tests/integration/test_fixture_pipeline.py
uv run mypy src/nfl_predictor/runtime src/nfl_predictor/cli.py
```

Expected: PASS with zero network attempts and a concrete fixture source-to-scorecard chain.

- [ ] **Step 9: Review checkpoint**

Run: `git diff -- src/nfl_predictor/runtime/services.py src/nfl_predictor/runtime/__init__.py src/nfl_predictor/cli.py tests/runtime/test_services.py tests/test_cli.py tests/integration/test_fixture_pipeline.py`

Expected: only the six Task 4 files; leave them unstaged.

---

### Task 5: Task 13 dispatch, schedule, cost, and workflow acceptance remediation

**Files:**

- Modify: `src/nfl_predictor/workflows/dispatch.py`
- Modify: `src/nfl_predictor/workflows/schedule_windows.py`
- Modify: `src/nfl_predictor/cli.py`
- Modify: `.github/workflows/nfl-v2-dispatch.yml`
- Modify: `deploy/schedules/dispatch-windows-2026.json`
- Modify: `deploy/private-data-repo/README.md`
- Modify: `deploy/private-data-repo/config/data_repo.toml`
- Modify: `deploy/private-data-repo/config/dispatch-windows-2026.json`
- Modify: `deploy/private-data-repo/.github/workflows/capture-and-forecast.yml`
- Modify: `deploy/private-data-repo/.github/workflows/private-due-check.yml`
- Modify: `deploy/private-data-repo/.github/workflows/settle-and-report.yml`
- Modify: `docs/runbooks/nfl-v2-operations.md`
- Modify: `tests/workflows/test_schedule_windows.py`
- Modify: `tests/workflows/test_dispatch.py`
- Modify: `tests/deploy/test_workflows.py`

**Interfaces:**

- Consumes: Task 4 production CLI, existing `DispatchPolicy`, `FileNonceStore`, `plan_private_usage`, active-event manifest loader, and fixed public/private offset policy.
- Produces: actor/sender-bound dispatch validation, durable pre-secret nonce admission, exact zero-dollar planning, byte-bound schedule validation, safe manual inputs, bounded recovery, heartbeat/alert behavior, and 2026-only cron execution.

- [ ] **Step 1: Add failing regressions for both Critical findings**

```python
def test_private_workflows_use_sibling_private_checkout_as_absolute_data_root(workflows):
    for workflow in workflows.private:
        assert workflow.env["NFL_PREDICTOR_DATA_DIR"] == "${{ github.workspace }}/data"
        assert "code/data" not in workflow.raw_text


@pytest.mark.parametrize("status", ["R100", "R090", "D", "C100"])
def test_immutable_guard_rejects_staged_source_and_destination_paths(workflow_probe, status):
    result = workflow_probe.run_name_status(status, "ledger/immutable.json", "config/moved.json")
    assert result.returncode != 0
```

- [ ] **Step 2: Add failing regressions for the eight Important findings**

Tests must prove:

1. every production CLI route composes when `NFL_V2_RUNTIME_CONFIG` is supplied;
2. actor and event sender must equal the reviewed identity;
3. the nonce is durably committed/pushed before checkout or any step containing provider secrets;
4. all eight dispatched full-worker jobs and per-job minute rounding are counted;
5. request cap and deployed private config are enforced with a failing exit on blockers;
6. actual public manifest, private manifest, and active-event bytes are independently hashed and cross-compared;
7. multiline/manual `at` input cannot reach `$GITHUB_ENV` and only strict UTC ISO-8601 is accepted;
8. provider attempts are bounded, target+5 heartbeat is recorded, and safe alert behavior contains no secrets.

- [ ] **Step 3: Add the year-lock regression**

```python
def test_public_cron_exits_before_dispatch_outside_manifest_year(public_workflow):
    assert public_workflow.has_guard('test "$(date -u +%Y)" = "2026"')
    assert public_workflow.guard_precedes_dispatch
```

- [ ] **Step 4: Run Task 13 tests and verify RED**

Run: `uv run pytest tests/workflows/test_schedule_windows.py tests/workflows/test_dispatch.py tests/deploy/test_workflows.py -q`

Expected: the new regressions reproduce 2 Critical, 8 Important, and 1 Minor findings.

- [ ] **Step 5: Bind dispatch identity before nonce consumption**

Extend `DispatchPolicy` with `approved_actors` and validate exact `github.actor` and `github.event.sender.login` inputs before calling `nonce_store.consume`. The private workflow exports these trusted GitHub context values directly, never from `client_payload`.

```python
def validate_dispatch(payload, *, actor, sender, policy, nonce_store):
    envelope = _envelope_from_payload(payload)
    if actor not in policy.approved_actors or sender not in policy.approved_actors:
        raise DispatchRejected("dispatch actor or sender is not approved")
    # Existing repository/ref/SHA/manifest/cluster checks follow.
    if not nonce_store.consume(envelope.nonce):
        raise DispatchRejected("dispatch nonce replay rejected")
    return envelope
```

- [ ] **Step 6: Make nonce admission durable before secret access**

The validation job checks out only the private data repository, atomically creates `ledger/dispatch-nonces/<sha>.nonce`, runs the immutable guard, commits/pushes that single allowed append with optimistic retry, and exposes a boolean job output. The forecast job has `needs: validate-dispatch`, checks the output, then checks out public code and receives `ODDS_API_KEY`. A failed nonce push prevents the forecast job.

- [ ] **Step 7: Fix private-root and immutable-change enforcement**

Set `NFL_PREDICTOR_DATA_DIR: ${{ github.workspace }}/data` for every private CLI step and pass `NFL_V2_RUNTIME_CONFIG` as an absolute public checkout config path. Parse `git diff --cached --name-status -z --find-renames --find-copies`; for rename/copy records validate both source and destination, reject every deletion, and allow additions only beneath configured append roots. Repeat the same check for unstaged changes and untracked files.

- [ ] **Step 8: Enforce exact cost and odds budgets from deployed private config**

Set `full_forecast_workers = 8` for the current reference schedule, recompute the rounded minimum as at least 29 minutes, and make the private config authoritative. The CLI receives the private config path explicitly, compares `projected_odds_requests <= monthly_hard_stop <= 400`, recomputes every job/storage number, and returns a nonzero exit when `deployment_enabled=true` but any blocker remains. The checked-in template stays `deployment_enabled=false`, `zero_dollar_mode=true`.

- [ ] **Step 9: Bind actual schedule bytes and sanitize manual time**

Hash the checked-out public dispatch manifest, the private copied manifest, the active-event manifest, and every active-event data file. Require equality with reviewed configuration and with dispatch payload SHA before due computation. Validate manual `at` against `YYYY-MM-DDTHH:MM:SSZ` in a no-secret job and pass it through a step output using a randomized delimiter; never append raw input to `$GITHUB_ENV`.

- [ ] **Step 10: Add bounded retry, heartbeat, alert, and year lock**

Provider work gets `timeout-minutes`, at most two attempts with fixed short backoff inside the origin window, and no retry after an uncertain odds request has consumed its reservation. At target+5, append a heartbeat record containing only event/origin/status/code SHA and, when missing or failed, open/update one deduplicated safe issue containing no payloads, paths, headers, or secrets. All alerting remains unreachable while deployment is disabled. Public and private scheduled jobs compare UTC year and manifest season to `2026` before dispatch/work.

- [ ] **Step 11: Update both manifests, private template documentation, and runbook**

Document the absolute sibling-checkout topology, actor allowlist, durable nonce sequence, exact minute/request calculation, byte-hash chain, strict manual input, retry ceiling, heartbeat/alert fields, 2026 lock, and recovery commands. Keep the active schedule unavailable and all enablement blockers explicit until real reviewed 2026 event bytes exist.

- [ ] **Step 12: Run Task 13 tests and static probes**

Run:

```bash
uv run pytest tests/workflows/test_schedule_windows.py tests/workflows/test_dispatch.py tests/deploy/test_workflows.py -q
uv run python -m nfl_predictor.cli odds budget-plan --season 2026
uv run python -m nfl_predictor.cli forecast due --at 2026-09-07T00:20:00+00:00 --trigger fixture --dry-run
uv run ruff check src/nfl_predictor/workflows/dispatch.py src/nfl_predictor/workflows/schedule_windows.py src/nfl_predictor/cli.py tests/workflows/test_dispatch.py tests/workflows/test_schedule_windows.py tests/deploy/test_workflows.py
uv run mypy src/nfl_predictor/workflows/dispatch.py src/nfl_predictor/workflows/schedule_windows.py src/nfl_predictor/cli.py
```

Expected: all tests pass; both CLI probes remain blocked/offline; no socket, Git, provider, or external mutation occurs.

- [ ] **Step 13: Review checkpoint**

Run: `git diff --` followed by the exact fifteen Task 13 paths listed above.

Expected: each of the eleven findings maps to a regression and code/template change; leave all files unstaged.

---

### Task 6: Fresh acceptance review and whole-project verification

**Files:**

- No product-file changes. Any discovered issue returns to the owning task and its locked files before rerunning this gate.

**Interfaces:**

- Consumes: Tasks 1–5.
- Produces: evidence-backed Task 12B acceptance and a fresh Task 13 verdict.

- [ ] **Step 1: Run the full offline suite with network disabled**

Run: `uv run pytest -q`

Expected: all tests pass and the network-attempt sentinel remains zero.

- [ ] **Step 2: Run full static verification**

Run:

```bash
uv run ruff check .
uv run mypy src/nfl_predictor
uv run python -m compileall -q src tests
```

Expected: all commands exit zero.

- [ ] **Step 3: Run behavior acceptance probes**

Exercise, with temporary private roots and injected clients: complete forecast, duplicate trigger, football-only fallback, closed window, replay cutoff, tampered raw capture, tampered active schedule, tampered registry/artifact, outcome correction, settlement, report generation, dispatch replay, wrong actor, staged rename, budget overrun, multiline manual input, provider timeout, and 2027 cron invocation.

Expected: valid offline paths complete; every invalid case fails closed with a safe category and no external attempt.

- [ ] **Step 4: Verify exact scope and authorization gates**

Run:

```bash
git status --short
git diff --name-only
git diff --cached --name-only
```

Expected: only the locked Task 12B and Task 13 files plus this approved plan are changed; the index is empty; no secret, `.env`, database, provider payload, or generated private ledger is tracked.

- [ ] **Step 5: Obtain independent acceptance review**

The reviewer must read the locked files completely, verify the eleven Task 13 regressions, reproduce the full offline/static matrix, and return `APPROVE` only with zero Critical/Important findings. Any finding is remediated only inside its owning task's locked files and rerun from RED to GREEN.

- [ ] **Step 6: Stop at the external authorization gate**

Report the local result and exact manual rehearsal steps. Do not stage, commit, push, configure GitHub, call a live source, or enable deployment.

---

## Self-review record

- Spec coverage: raw-first capture, typed lineage, point-in-time selection, frozen origin artifacts, calibrated three-way prediction, optional odds degradation, immutable forecast/outcome/report repositories, concrete CLI services, schedule redundancy, security, cost, and launch fail-closed behavior each map to Tasks 1–5.
- Acceptance coverage: 2 Critical findings map to Task 5 Steps 1, 6, and 7; 8 Important findings map to Steps 2 and 5–10; the Minor finding maps to Steps 3 and 10.
- Type consistency: runtime collaborators implement the exact protocols declared by `ForecastWorkflow`, `OutcomeWorkflow`, and `ServiceRegistry`; later tasks consume the names defined in earlier tasks.
- Scope check: Task 12B runtime and Task 13 deployment remediation remain separate reviewer gates inside one plan because Task 13 cannot pass without Task 12B, while each task still produces independently testable software.
- Placeholder scan: every implementation step names concrete contracts, invariants, tests, commands, and expected outcomes.
