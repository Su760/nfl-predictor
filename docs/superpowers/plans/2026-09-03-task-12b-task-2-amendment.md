# Task 12B Task 2 Integrity Amendment Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Repair the rejected production capture adapters with batch-gated lineage visibility, complete multi-source provenance, contract-compatible football facts, authoritative odds accounting, and documented nflverse outcome behavior.

**Architecture:** A committed `CaptureBatch` marker becomes the visibility boundary for football facts: manifests and fact objects may be written first, but feature selection sees them only after the coherent batch marker exists. `NormalizedFact.input_capture_ids` records every raw capture used by a derived fact, while the point-in-time store recognizes both offensive and defensive team relationships. The nflverse adapter remains stateless and converts documented Eastern kickoffs to UTC; its outcome adapter intentionally emits only `final` or `unresolved` because the official schedule dictionary has no documented postponed/cancelled/suspended status field.

**Tech Stack:** Python 3.11, Pydantic v2, Polars Arrow IPC, PyArrow, DuckDB, `zoneinfo`, pytest, Ruff, mypy.

**Spec:** `docs/superpowers/plans/2026-09-02-task-12b-production-runtime.md` Task 2, amended by `.superpowers/sdd/2026-09-02-task-12b-production-runtime/task-2-review-1.md` and the user's 2026-09-03 approval of the recommended minimal amendment.

## Global Constraints

- Work only in `/Users/supashramesha/.codex/worktrees/nfl-predictor-v2/nfl-predictor` on `codex/nfl-predictor-v2`; preserve the dirty legacy checkout.
- No provider/network calls, secret reads or writes, repository creation, Actions enablement, deployment, paid usage, or wagers.
- The user authorizes commits and pushing `codex/nfl-predictor-v2`; never push to `main`.
- Raw provider evidence remains outside `code_root`; no `.env`, secret, database, raw provider payload, or private ledger may be tracked.
- Use schedules plus play-by-play only for required football capture; do not add the rejected team-stats dataset.
- Keep the existing five-state `OutcomeStatus` contract, but the nflverse Arrow adapter may emit only `final` or `unresolved`; other states require a future documented source and must fail closed as unresolved here.
- `gametime` from the official nflverse schedule dictionary is Eastern time regardless of venue and must be converted with `ZoneInfo("America/New_York")` before producing UTC.
- All bug fixes require regression tests that fail for the observed mechanism before implementation.

---

### Task 1: Batch-Gated Visibility and Multi-Source Fact Lineage

**Files:**

- Modify: `src/nfl_predictor/contracts/lineage.py`
- Modify: `src/nfl_predictor/storage/parquet.py`
- Modify: `src/nfl_predictor/runtime/lineage.py`
- Modify: `src/nfl_predictor/workflows/forecast.py`
- Modify: `tests/storage/test_ledger.py`
- Modify: `tests/runtime/test_lineage.py`
- Modify: `tests/workflows/test_forecast.py`

**Interfaces:**

- Produces: `NormalizedFact.input_capture_ids`, `NormalizedFact.lineage_capture_ids`, and `DurableLineageRepository.publish_capture_batch(obligation, attempt_id, manifests, facts) -> CaptureBatch`.
- Changes: `DurableLineageRepository.iter_facts()` and `for_event_features(...)` expose only facts named by verified committed capture batches.
- Consumes: existing `LedgerStore` object/marker atomicity; the ledger implementation itself remains unchanged.

- [ ] **Step 1: Write failing multi-source lineage tests**

Add contract/storage tests proving a derived fact can bind two unique nonblank capture IDs, `capture_id` must be one of them, empty `input_capture_ids` remains backward-compatible as `(capture_id,)`, and Arrow round-trip preserves the tuple.

```python
def test_normalized_fact_exposes_exact_multi_capture_lineage(fact):
    derived = fact.model_copy(
        update={"capture_id": "pbp-1", "input_capture_ids": ("schedule-1", "pbp-1")}
    )
    validated = NormalizedFact.model_validate(derived.model_dump())
    assert validated.lineage_capture_ids == ("schedule-1", "pbp-1")
```

- [ ] **Step 2: Write failing batch-visibility fault tests**

In `tests/runtime/test_lineage.py`, inject a `LedgerStore.append` failure for namespace `capture-batch-v1` after manifest/fact writes. Assert `iter_facts()` and `for_event_features()` expose nothing, then retry the same publication and assert the exact facts become visible once.

```python
def test_fact_is_invisible_until_capture_batch_marker_commits(runtime_fixture, monkeypatch):
    monkeypatch.setattr(runtime_fixture.lineage.ledger, "append", fail_batch_marker)
    with pytest.raises(OSError, match="batch marker"):
        runtime_fixture.lineage.publish_capture_batch(obligation, "attempt-1", manifests, facts)
    assert list(runtime_fixture.lineage.iter_facts()) == []
```

- [ ] **Step 3: Write failing forecast-lineage tests**

Add tests showing feature validation accepts a derived fact only when every `lineage_capture_ids` member appears in the exact snapshot manifest set, and rejects a missing secondary input manifest.

- [ ] **Step 4: Run focused tests and verify RED**

Run:

```bash
uv run pytest tests/storage/test_ledger.py tests/runtime/test_lineage.py tests/workflows/test_forecast.py -q
```

Expected: failures for the missing field/property, publication method, visibility gate, Arrow field, and workflow secondary-lineage validation.

- [ ] **Step 5: Implement the fact-lineage contract**

Add an immutable tuple field and effective-lineage property:

```python
class NormalizedFact(UtcModel):
    capture_id: str
    input_capture_ids: tuple[str, ...] = ()

    @property
    def lineage_capture_ids(self) -> tuple[str, ...]:
        return self.input_capture_ids or (self.capture_id,)
```

Validate that explicit IDs are nonblank, unique, and contain `capture_id`. Add `input_capture_ids` as `_STRINGS` to the exact PyArrow schema. Update workflow validation to require `set(fact.lineage_capture_ids) <= manifest_ids`; do not weaken the existing primary-capture equality checks.

- [ ] **Step 6: Implement batch publication and gated reads**

`publish_capture_batch` validates all IDs first, appends manifests, appends facts, and writes the batch marker last through existing `append_capture_batch`. `append_capture_batch` requires every fact lineage capture ID to belong to the supplied manifest set. `iter_facts` enumerates verified batches, resolves their exact manifests/facts, rechecks coherence, deduplicates fact IDs, and never enumerates the raw normalized-fact namespace directly.

- [ ] **Step 7: Run Task 1 verification**

Run:

```bash
uv run pytest tests/storage/test_ledger.py tests/runtime/test_lineage.py tests/workflows/test_forecast.py -q
uv run ruff check src/nfl_predictor/contracts/lineage.py src/nfl_predictor/storage/parquet.py src/nfl_predictor/runtime/lineage.py src/nfl_predictor/workflows/forecast.py tests/storage/test_ledger.py tests/runtime/test_lineage.py tests/workflows/test_forecast.py
uv run mypy src/nfl_predictor/contracts/lineage.py src/nfl_predictor/storage/parquet.py src/nfl_predictor/runtime/lineage.py src/nfl_predictor/workflows/forecast.py
```

Expected: PASS; fault-injected orphan objects remain invisible and retry becomes visible exactly once.

- [ ] **Step 8: Review, commit, and leave push for the final amendment gate**

Review only the seven listed files, confirm no secrets/private data are staged, then commit:

```bash
git add src/nfl_predictor/contracts/lineage.py src/nfl_predictor/storage/parquet.py src/nfl_predictor/runtime/lineage.py src/nfl_predictor/workflows/forecast.py tests/storage/test_ledger.py tests/runtime/test_lineage.py tests/workflows/test_forecast.py
git commit -m "fix: gate runtime facts on committed capture batches"
```

---

### Task 2: Relationship-Complete Point-in-Time Selection

**Files:**

- Modify: `src/nfl_predictor/storage/facts.py`
- Modify: `tests/storage/test_facts.py`

**Interfaces:**

- Changes: both `PointInTimeStore.for_event_features(...)` and `DuckDbPointInTimeStore.for_event_features(...)` select facts whose `team`, `offense_team`, or `defense_team` matches either event participant.
- Preserves: cutoff eligibility and `_latest` ordering/deduplication.

- [ ] **Step 1: Write a failing cross-direction parity test**

Create one prior game with `home_team` on offense and one with it on defense. Assert both implementations return both facts for the target event, while excluding a third unrelated matchup.

```python
@pytest.mark.parametrize("store_factory", STORE_FACTORIES)
def test_event_features_include_offense_and_defense_relationships(store_factory, facts, event):
    selected = store_factory(facts).for_event_features(event, CUTOFF)
    assert {fact.entity_keys["offense_team"] for fact in selected} == {event.home_team, "OPP"}
```

- [ ] **Step 2: Run the focused test and verify RED**

Run: `uv run pytest tests/storage/test_facts.py -q`

Expected: the opponent-offense row is absent from both implementations.

- [ ] **Step 3: Implement relationship-complete predicates**

The in-memory predicate checks `team`, `offense_team`, and `defense_team`. The DuckDB query adds equivalent `json_extract_string` predicates with bound parameters; do not interpolate team values into SQL.

- [ ] **Step 4: Verify, review, and commit**

Run:

```bash
uv run pytest tests/storage/test_facts.py -q
uv run ruff check src/nfl_predictor/storage/facts.py tests/storage/test_facts.py
uv run mypy src/nfl_predictor/storage/facts.py
git diff --check -- src/nfl_predictor/storage/facts.py tests/storage/test_facts.py
```

Then confirm the index contains no secret/private files and commit:

```bash
git add src/nfl_predictor/storage/facts.py tests/storage/test_facts.py
git commit -m "fix: select both sides of team epa evidence"
```

---

### Task 3: Stateless, Contract-Compatible Football Capture

**Files:**

- Modify: `src/nfl_predictor/runtime/capture.py`
- Modify: `tests/runtime/test_capture.py`

**Interfaces:**

- Produces: pure `NflverseFootballNormalizer.normalize_events(...)`, paired `normalize_capture(...)`, and fail-closed `normalize_facts(...)` that requires explicit schedule context for PBP.
- Consumes: `ScheduleFact.to_event_version()`, `NormalizedFact.input_capture_ids`, `DurableLineageRepository.publish_capture_batch(...)`, and relationship-complete point-in-time selection.

- [ ] **Step 1: Write failing kickoff and canonical-identity tests**

Assert a September `18:00` nflverse kickoff becomes `22:00Z`, a December `18:00` kickoff becomes `23:00Z`, and canonical IDs use `ScheduleFact` format while retaining nflverse `game_id` only in `source_event_ids`.

```python
def test_schedule_kickoff_converts_eastern_with_dst(normalizer, manifest):
    september, december = normalizer.normalize_events(schedule_ipc, manifest)
    assert september.kickoff_at_utc.hour == 22
    assert december.kickoff_at_utc.hour == 23
```

- [ ] **Step 2: Write failing exact-shape and FeatureBuilder integration tests**

Assert exact counts and payload/entity shapes for every fact family. `division_alignment` must carry `canonical_event_id`, `season`, `home_team`, and `away_team`, with payload `{"same_division": bool}`. Feed the normalized capture through `DurableLineageRepository.publish_capture_batch` and the production `FeatureBuilder`; assert an exact V1 snapshot is built without fake collaborators.

- [ ] **Step 3: Write failing statelessness, closure, identity, and validation tests**

Cover:

- PBP normalization without explicit schedule context rejects instead of returning an empty tuple.
- Reusing a normalizer across two captures cannot consume the first capture's schedules.
- Every completed game has exactly `{home -> away, away -> home}` EPA directions; missing or third-party directions reject.
- Repeated identical normalization yields identical fact IDs/content hashes.
- Missing columns, duplicate provider IDs, non-finite EPA/CPOE, invalid teams, impossible/partial scores, and rows after capture receipt reject.
- Target-game scores and PBP remain excluded.
- Derived strength facts list both schedule and PBP capture IDs; source-specific facts list only their actual input.

- [ ] **Step 4: Run focused tests and verify RED**

Run: `uv run pytest tests/runtime/test_capture.py -q`

Expected: failures for UTC conversion, canonical identity, division shape, FeatureBuilder integration, mutable state, directional closure, and multi-input lineage.

- [ ] **Step 5: Implement stateless paired normalization**

Replace `_schedules` instance state with immutable local schedule rows passed through `normalize_capture`. A direct PBP `normalize_facts` call must receive an explicit schedule context or raise `ValueError("pbp normalization requires paired schedules")`. Localize `gameday + gametime` with `ZoneInfo("America/New_York")`, reject ambiguous/nonexistent wall times, and convert to UTC.

Build event candidates through `ScheduleFact(...).to_event_version()`. Validate exact home/away directional closure before emitting EPA facts. Construct every fact identity from fact type, entity keys, payload, provider record ID, observation/availability/capture timestamps, all input capture IDs, and normalization schema version.

- [ ] **Step 6: Implement contract-compatible facts and publication**

Emit accepted FeatureBuilder payloads exactly. Use the schedule manifest for schedule-only facts, PBP manifest for PBP-only facts, and both IDs for combined rating priors. Replace the sequential append sequence in `RequiredFootballCapture` with `publish_capture_batch`; raw bytes and stored manifests must still verify before normalization or publication.

- [ ] **Step 7: Run football and integration verification**

Run:

```bash
uv run pytest tests/runtime/test_capture.py tests/features tests/storage tests/capture tests/sources -q
uv run ruff check src/nfl_predictor/runtime/capture.py tests/runtime/test_capture.py
uv run mypy src/nfl_predictor/runtime/capture.py
```

Expected: PASS with zero socket/DNS attempts.

- [ ] **Step 8: Review and commit**

Confirm only the two listed files changed for this task and commit after the secret/private-file check:

```bash
git add src/nfl_predictor/runtime/capture.py tests/runtime/test_capture.py
git commit -m "fix: harden production football capture"
```

---

### Task 4: Authoritative Odds Accounting and Documented Outcomes

**Files:**

- Modify: `src/nfl_predictor/runtime/capture.py`
- Modify: `tests/runtime/test_capture.py`

**Interfaces:**

- Changes: `OptionalOddsCapture` requires `DurableLineageRepository`; successful response quota headers finalize the reservation before normalization/persistence and can never be overwritten by uncertain finalization.
- Changes: `ArrowOutcomeAdapter` verifies raw bytes, selects one exact source event, and emits only documented `final` or `unresolved` states.

- [ ] **Step 1: Write failing odds ordering and persistence tests**

Use fault injection to prove:

- disabled policy or blank key never constructs the adapter;
- timeout after send consumes the reservation uncertainly;
- known quota headers finalize authoritative usage even when normalization fails;
- a persistence failure after known headers does not re-finalize uncertainly;
- construction without lineage is rejected;
- successful capture persists the manifest and every accepted quote before returning, and bundle/rejection payloads contain no key or secret detail.

- [ ] **Step 2: Write failing documented-outcome tests**

Test exactly-one source selection, raw-hash verification, score/team validation, final and unresolved mapping, retrieval-time fallback, and rejection of partial scores. Add a test documenting that absent scores—including any externally implied postponed/cancelled/suspended condition unavailable in the frozen nflverse schema—remain `unresolved` rather than being guessed.

- [ ] **Step 3: Run focused tests and verify RED**

Run: `uv run pytest tests/runtime/test_capture.py -q`

Expected: failures for quota finalization order, mandatory lineage, persistence fault handling, raw verification, and explicit unresolved behavior.

- [ ] **Step 4: Implement single-finalization odds flow**

Track whether authoritative quota finalization completed. Reserve before adapter creation/call. Immediately after `CaptureService.capture`, validate allowlisted quota headers and call `finalize_success`; only call `finalize_uncertain` when no authoritative response was finalized. Require lineage in `__init__`, persist manifest and quotes before returning, and preserve non-secret reason codes only.

- [ ] **Step 5: Implement documented Arrow outcomes**

Verify the stored raw bytes match the manifest hash before parsing. Require exact frozen schedule fields and one matching `source_event_id`. Both scores present maps to `final`; both absent maps to `unresolved`; partial scores reject. Never infer the other three `OutcomeStatus` values from undocumented fields.

- [ ] **Step 6: Run the complete Task 2 amendment matrix**

Run:

```bash
uv run pytest tests/runtime/test_capture.py tests/runtime/test_lineage.py tests/storage/test_facts.py tests/storage/test_ledger.py tests/workflows/test_forecast.py tests/features tests/capture tests/sources tests/markets/test_budget.py tests/markets/test_normalizer.py -q
uv run ruff check src/nfl_predictor/contracts/lineage.py src/nfl_predictor/storage/parquet.py src/nfl_predictor/storage/facts.py src/nfl_predictor/runtime/lineage.py src/nfl_predictor/runtime/capture.py src/nfl_predictor/workflows/forecast.py tests/storage/test_ledger.py tests/storage/test_facts.py tests/runtime/test_lineage.py tests/runtime/test_capture.py tests/workflows/test_forecast.py
uv run mypy src/nfl_predictor/contracts/lineage.py src/nfl_predictor/storage/parquet.py src/nfl_predictor/storage/facts.py src/nfl_predictor/runtime/lineage.py src/nfl_predictor/runtime/capture.py src/nfl_predictor/workflows/forecast.py
```

Expected: PASS with zero network attempts and no orphan fact visible without a committed batch.

- [ ] **Step 7: Review and commit**

Review only the two Task 4 files, check the index for `.env`, secrets, databases, raw provider payloads, and private ledgers, then commit:

```bash
git add src/nfl_predictor/runtime/capture.py tests/runtime/test_capture.py
git commit -m "fix: finalize odds quota and constrain nflverse outcomes"
```

---

### Task 5: Amendment Acceptance, Branch Commit, and Push

**Files:**

- Verify: all files named in Tasks 1–4
- Update: `docs/superpowers/plans/2026-09-02-task-12b-production-runtime.md`
- Update: `tasks/todo.md` in the legacy checkout ledger only

**Interfaces:**

- Produces: clean Task 2 acceptance evidence and pushed `codex/nfl-predictor-v2` commits.

- [ ] **Step 1: Run fresh full-project verification**

Run:

```bash
uv run pytest -q
uv run ruff check .
uv run mypy src/nfl_predictor
git diff --check
```

Expected: PASS with no network/provider calls.

- [ ] **Step 2: Run a fresh independent amendment review**

Review the exact amendment diff against the Task 2 rejection. Every Critical and Important finding must be resolved or the fix loop continues; green tests alone are not acceptance.

- [ ] **Step 3: Reconcile and commit previously untracked V2 source**

Before staging, inspect every untracked path and confirm no `.env`, key, credential, database, raw provider payload, generated private ledger, or unrelated user artifact is present. Stage only the reviewed V2 source/config/tests/docs/workflow/deploy files required by the project. Do not stage `.superpowers/sdd` scratch artifacts.

Commit any reviewed, still-uncommitted V2 foundation as one integration checkpoint:

```bash
git commit -m "feat: add auditable nfl predictor v2 foundation"
```

- [ ] **Step 4: Push the authorized branch**

Run:

```bash
git push -u origin codex/nfl-predictor-v2
```

Expected: the remote branch updates successfully; `main` remains unchanged.

- [ ] **Step 5: Resume the parent Task 12B plan**

Mark original Task 2 accepted, then continue original Tasks 3–6 under the already selected Subagent-Driven workflow. Push additional reviewed commits after the parent plan's final verification.
