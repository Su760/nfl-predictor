# NFL Predictor V2 operations runbook

## Current state and non-authorization notice

The checked automation is a local, disabled template. It does not authorize creating a
repository, enabling Actions, configuring a secret, pushing a commit, calling a provider,
or spending money. `deployment_enabled` is `false`, `zero_dollar_mode` is `true`, the
private repository and approved public SHA are blank, and the cost allowance has not
been verified.

There is no active 2026 event-version schedule in this worktree. The only reviewed
schedule fact is NE at SEA at 17:20 PDT on 2026-09-09, equivalent to
`2026-09-10T00:20:00Z`; its first T−72 target is
`2026-09-07T00:20:00Z`. The checked crons demonstrate schedule-specific coverage for
that reference only. They must not be described or enabled as a complete 2026 schedule.

Every prospective run must preserve these invariants:

1. Use an aware UTC real clock. A historical `--at` is replay, never Grade A recovery.
2. Resolve exactly one active event version and confirm its origin window is still open.
3. Print the event, event version, origin, window, public code SHA, and schedule-manifest
   SHA before any capture.
4. Require both trusted `github.actor` and `github.event.sender.login` to exactly match
   the reviewed allowlist; then validate repository, default-branch ref, event type,
   fixed fields, cluster, code SHA, manifest SHA, and an unseen nonce.
5. Commit and push the nonce as the only staged append, with at most one optimistic
   rebase/retry, before public checkout or any provider-secret step. A push failure is a
   rejected dispatch.
6. Independently hash the actual public/private schedule manifests, active-event
   manifest, and every listed event data file and cross-compare them with config and the
   dispatch SHA before due computation.
7. Burn execution-bound odds admission in a separate no-provider-secret job, then
   fast-forward push before the provider worker starts. Check the admitted data commit
   and current trusted `github.run_id:github.run_attempt`; fail on a stale rerun token.
8. Append immutable evidence and validate it before push. Never force-push or rewrite a
   prior forecast, feature snapshot, quote decision, outcome, or settlement.
9. Require UTC year and manifest season `2026`; later-year cron wakeups stop before work.
10. Never place a wager. Reports use units and do not imply guaranteed profit.

## Reviewed action pins

Only full commit pins are allowed. The locally reviewed template uses:

- `actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1`
  (`actions/checkout` v7.0.1).
- `astral-sh/setup-uv@08807647e7069bb48b6ef5acd8ec9567f424441b`
  (`astral-sh/setup-uv` v8.1.0).

Before changing a pin, verify the commit against the action's official repository,
record the release and commit here, inspect the diff, and re-run the BaseLoader workflow
tests. Tags or moving branches are not acceptable pins.

## Initial setup after separate approval

Obtain explicit approval for repository creation, Actions enablement, each secret, and
the first push. At execution time, resolve rather than guess both names. Confirm the
public repository's `nameWithOwner` and default branch, then show the proposed private
`owner/repository` to the approver. Stop if either differs from the reviewed config.

Create the private repository only after that confirmation. Copy only
`deploy/private-data-repo/` into it. Keep it private, keep branch protection enabled, and
limit Actions to the pinned actions in this template. Do not enable the workflows yet.
The private checkout must be the absolute sibling `${{ github.workspace }}/data` and the
reviewed public checkout `${{ github.workspace }}/code`; use
`${{ github.workspace }}/code/configs/base.toml` as `NFL_V2_RUNTIME_CONFIG`. Never run a
public CLI from `code` with a relative `data` root.

Create a fine-grained dispatch token with:

- access to the single approved private data repository, not all repositories;
- the documented `Contents: write` repository permission required for the approved
  repository-dispatch path;
- no organization administration, packages, workflows, pull requests, or unrelated
  repository permissions; and
- a short expiry recorded in the operator log.

Store it only as `NFL_DATA_REPO_DISPATCH_TOKEN` in the public repository's Actions
secrets. Store `ODDS_API_KEY` only in the private repository's Actions secrets. Set the
non-secret repository variables `NFL_PUBLIC_REPOSITORY`, `NFL_DATA_REPO`,
`NFL_V2_ARTIFACT_REGISTRY_SHA256`, and `NFL_V2_DEPLOYMENT_ENABLED` only after their exact values are reviewed. Never print a
token value, pass it as a command argument, or put it in config, a URL, an artifact, or a
retained log.

Before enablement, replace the incomplete reference manifest with a complete active
2026 event-version schedule from a reviewed official schedule source. Record source URL,
retrieval time, raw SHA-256, reviewer, flex/TBD status, and the selected active version
for every event. Exclude unresolved TBD kickoffs. Then render:

```text
uv run nfl-predictor schedule render-dispatch --season 2026 --public-offsets=-8,-3,2,7 --private-offsets=-6,-1,4,9
```

Review every generated UTC target and cron. Public offsets are `(-8,-3,+2,+7)` and
private offsets are `(-6,-1,+4,+9)`; none may be top-of-hour or outside the ±10-minute
origin window. Copy the byte-identical manifest to the private config directory, record
its SHA-256, record the active-event manifest path/SHA and every file SHA, set the exact
approved public commit SHA, exact actor allowlist, exact private repository, and advance
the month lock. A schedule manifest cannot approve itself: its external SHA-256 belongs
in `data_repo.toml` and the dispatch envelope. The checked public and private bytes,
config hash, and envelope hash must all agree at runtime.

## Zero-dollar deployment gate

The planner must count all of the following for the reviewed schedule:

- schedule-specific private due ticks;
- full T−72 and T−60 forecast workers;
- postgame settlement jobs;
- Wednesday official-correction jobs; and
- the bounded retry allowance.

GitHub bills by rounding each job duration up to a whole minute. The correct projection
is `sum(job_count * ceil(seconds_per_job / 60))`, not a ceiling of aggregate seconds.
Compare that total and projected retained storage with the user's freshly verified
remaining included private Actions minutes and storage. Record the verification time.
The planner must reject when projected paid Actions minutes or paid storage is nonzero.

The checked reference projects 143 billed minutes: eight nonce jobs at one minute,
sixteen admission jobs at three minutes, eight private and eight public-dispatched
workers at three minutes each, ten heartbeat jobs at three minutes, a two-minute
settlement, a one-minute correction and two three-minute retries:
`8 + 48 + 24 + 24 + 30 + 2 + 1 + 6 = 143`. These durations are conservative planning
assumptions and must be verified before deployment; they are not measured guarantees.
Private due ticks do full environment restoration and worker work. The two target+5
cron ticks run heartbeat only; all eight private backup ticks also run that
check. Both original public/private due offset sets remain unchanged.

The odds projection is two origins × two admitted attempts per active event, plus two
configured schedule-change calls for this one-event reference (six credits). The actual
limiter reads the private hard stop and never exceeds the public policy cap or 400.
`per_origin_admission_attempts = 2` is the frozen maximum across every trigger and
rerun. Reservations are charged before the admission push, so even two lost runners
consume only two admissions for that obligation. Later workers continue football-only.
Retained storage is projected at 100 MiB. Both included allowances remain zero, so
all of these figures remain explicit enablement blockers. Verify the deployed file:

```text
uv run python -m nfl_predictor.cli odds budget-plan --season 2026 --private-config /absolute/path/to/data/config/data_repo.toml
```

An enabled config exits nonzero for any cost, request-cap, hash, or schedule blocker. A
billing spend limit, runner-class change, storage purchase, or paid API plan is a
separate user decision and is never implied by changing `deployment_enabled`.

Only after the active schedule, exact repositories/SHA, zero-paid-use projection, local
tests, and explicit enablement approval all pass may both config and manifest be changed
to enabled. Keep `zero_dollar_mode = true`.

## Normal operating checks

At the start of each football month:

1. Reconcile the active official schedule, flex/TBD state, manifest hashes, cron lists,
   month lock, approved public SHA, and default branch.
2. Recalculate per-job rounded private Actions usage, remaining included minutes, and
   retained artifact/storage bytes. Keep screenshots or API output that excludes secrets.
3. Confirm GitHub has not disabled the public scheduled workflow because of repository
   inactivity. GitHub can disable scheduled workflows after prolonged public-repository
   inactivity; inspect the Actions UI and default-branch workflow state.
4. Confirm the public and private cron sets differ and still cover every origin with
   redundant in-window ticks.
5. Confirm the last completed origin has an immutable terminal record and that duplicate
   trigger paths converged on it.
6. Schedule a heartbeat at target+5 and recheck on later private ticks.
   It scans all verified active obligations whose target+5 has elapsed, including
   missing runs and expired windows; it never infers completion from process exit.
   `FAILED`/`MISSED` are failures and `COMPLETE`/`FOOTBALL_ONLY` are success.
   Store only event, origin, status and code SHA under a stable event/origin/policy
   identity. Missing/failed observations map to one safe deduplicated issue. GitHub
   scheduler delay or dropped runs can prevent timely alert delivery.

After each run, inspect only the compact safe summary: event, version, origin, run ID,
status, reason code, code SHA, and safe log link. Do not expose raw provider credentials,
authorization headers, private paths, or retained request URLs.

## Manual in-window recovery

Manual recovery is allowed only while the exact prospective origin window is open. It is
not a backfill. First run the no-network preview with the real current UTC timestamp:

```text
uv run python -m nfl_predictor.cli dispatch due --at 2026-09-07T00:20:00Z --dry-run
```

The preview must print the active event ID and version, origin, target, open/close times,
code SHA, manifest SHA, and `due` state. Verify them against the private config and
official schedule. Abort if the event is absent, the version is stale, the SHA differs,
the clock is outside the window, or the preview does not refuse an expired run. Never
add `--mode replay` to a prospective recovery. Manual workflow time must match strict
`YYYY-MM-DDTHH:MM:SSZ`; offsets such as `+00:00`, fractional seconds, whitespace, and
multiline values are rejected before any secret step and never reach `$GITHUB_ENV`.

For a local approved recovery, invoke the same idempotent boundary with the real current
UTC value and explicit event/origin:

```text
uv run nfl-predictor forecast run --event 2026_REG_01_NE_SEA --origin T72 --trigger manual --at 2026-09-07T00:20:00Z
```

The IDs and times above illustrate only the reviewed kickoff reference; resolve the
current active version before use. For a private Actions recovery, use the manual inputs
in `capture-and-forecast.yml` after the same preview. Supply the exact approved code SHA
and schedule-manifest SHA. The workflow repeats validation and refuses a closed window.

## Failure and recovery procedures

### Schedule flex, postponement, cancellation, or TBD

Disable deployment before changing schedule state. Capture the new official source as a
new immutable schedule fact, reconcile a new active event version, and never overwrite
the prior kickoff. Re-render both cron sets and manifest, update the external manifest
hash, review every changed target, update the month lock if needed, and re-run local
tests. A stale cron may wake a job, but the active-version due check must exit without a
forecast. A TBD event remains excluded.

### HTTP 429 or provider outage

Provider work has a 15-minute job timeout and at most two attempts separated by the
fixed five-second reviewed delay, only while the origin window remains open. If an
attempt changes the odds reservation/usage bytes, treat it as potentially consumed and
do not retry. Optional close/reference calls yield to required origin calls. If
odds are unavailable or quota is exhausted, retain the football forecast with
`FOOTBALL_ONLY` and no odds-based decision. If required football capture cannot finish
before close, append the safe target+5 failure/missed heartbeat and update the single
event/origin alert; never reconstruct a Grade A forecast later and never spend an
unapproved credit.

### Duplicate dispatch

An identical nonce is rejected before checkout or secrets. Admission is not complete
until that nonce is durably committed and pushed; the only conflict recovery is one
fetch, duplicate check, rebase, and retry. A distinct nonce for the same
event/version/origin reaches the same transactional idempotency key and must return the
single immutable terminal result. Investigate repeated distinct nonces, preserve safe
run IDs, and rotate the dispatch token if unauthorized activity is suspected. Do not
delete the winning record or retry recursively from the dispatcher.

### Malformed or hostile payload

Reject missing, extra, non-string, overlong, or disallowed fields; wrong event type;
wrong source repository/default-branch ref; malformed or unapproved code/manifest SHA;
unknown cluster; and replayed nonce. No public checkout, odds reservation, provider call,
or secret-bearing step may follow. Record only a safe reason code and run ID.

### Private push conflict or storage interruption

Do not force-push. Fetch the protected default branch, inspect both append sets, and
rebase only when immutable IDs and hashes do not conflict. Re-run the leak scan and
immutable validator before a normal push. If the same ID has different bytes, quarantine
both attempts and stop for manual review. A completed capture whose terminal marker was
not durably pushed remains an incomplete attempt, not a forecast.

### Outcome correction and unresolved settlement

The postgame lane captures the official outcome and appends versioned settlements. The
Wednesday `official_correction` lane may append a corrected outcome/settlement and
refresh challenger training inputs, but it cannot mutate an issued prediction, feature
snapshot, displayed quote, or earlier outcome version. Preserve exact outcome/version
and operator-evidence lineage.

Leave postponed, suspended, ambiguous, or operator-conflicted positions unresolved.
Never coerce them to a loss, infer a book rule, or silently void them. Record the reason,
operator evidence needed, reviewer, and follow-up date; settle only after explicit
evidence resolves the state.

## Credential rotation and revocation

For dispatch-token rotation, first keep deployment disabled. Create the replacement with
the same single-repository, minimum-permission, short-expiry scope; update the public
secret through an approved secret-entry channel; run only a no-network dry-run; then
re-enable after approval. Revoke the old token immediately after the first validated
dispatch. On suspected compromise, disable workflows, revoke first, inspect safe audit
logs and nonce history, and do not replay queued payloads blindly.

For odds-key rotation, disable capture workflows, create the replacement at the provider,
update only the private `ODDS_API_KEY` secret, revoke the old key, and reconcile provider
usage without printing the key. Run readiness and budget checks before re-enabling. A key
failure defaults to football-only or missed according to whether required football data
completed in-window.

At season end, disable all schedules, revoke the dispatch token and odds key, reconcile
final Actions/storage/provider usage, retain immutable evidence according to provider
terms, and archive the year-specific cron manifests so they cannot recur in a later year.

## Durable admission and receipt acceptance status

Budget transactions append hash-chained immutable JSON snapshots under
`ledger/budget-reservations/<month>/`. A kernel directory lock serializes local
transactions and automatically releases on process exit. Admission charges credits
before provider access, then pushes its commit as a fast-forward compare-and-swap.
A failed push exports no permission to the worker; never rebase a stale budget snapshot.
Recompute against a fresh private checkout on a new authorized execution instead.

Workers check out the exact admitted data SHA and compare the admitted execution with
current trusted `github.run_id:github.run_attempt`. Rerunning only a failed worker
cannot reuse an older admission. An immutable local claim blocks duplicate sends;
crashes leave the pre-pushed credit spent. Provider headers can increase conservative
consumption once; absent or uncertain responses never refund it. Each attempt refreshes
the real UTC clock and stops if the origin window has closed.

Immutable `terminal-publication-v1` evidence binds the execution key, run and receipt
hashes, and original publication/post-fsync UTC observations. Original publication
deadline checks precede proof creation; restoring historical proof is not a new
publication. Legacy or interrupted receipts without valid proof remain unverified;
never manufacture proof from checkout metadata. The offline Git-restore and heartbeat
probe passes. Overall Task 5/deployment acceptance remains blocked by two existing
outcomes regression assertions that require post-publication metadata changes to
invalidate valid proof; test-only reconciliation awaits scope approval. All other
readiness and allowance gates still apply.

### Reviewed writer serialization and quota reconciliation

The nonce validation job exports its pushed private commit SHA; public budget admission
starts from that exact commit. Detached provider workers publish with an explicit
`HEAD:refs/heads/<private-default-branch>` fast-forward refspec. Budget transactions are
never rebased. Capture, private due and settlement workflows share one private-writer
concurrency group. The heartbeat job depends on admission and the provider with an
`always()` deployment gate, then checks out and validates the latest explicit private
branch. It still observes failed or skipped workers without racing their commits;
serialization and scheduler delay may postpone the target+5 observation.

Authoritative provider usage is reconciled with the additional cost of outstanding
prepaid admissions. A reported usage increase can therefore block an already admitted
but unattempted request. Claim authorization and the local claim append share the budget
transaction lock. Only current-execution claims suppress provider retries. The explicit
private budget CLI hashes real active-event manifest/data bytes and shares actual-event
request/job derivation with runtime planning; missing bytes, understated requests and
omitted jobs block deployment and cause a nonzero CLI exit.
