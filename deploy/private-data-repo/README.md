# NFL Predictor V2 private-data repository template

This directory is a disabled, zero-dollar template for a separately approved private
repository. Copying or viewing it does not authorize repository creation, Actions,
secrets, provider calls, or deployment.

The checked configuration deliberately cannot run. It has no private repository name,
no approved public code SHA, no active 2026 event-version manifest, and no
user-verified remaining private Actions or storage allowance. The one NE-at-SEA kickoff
is a reviewed schedule reference used to exercise schedule-specific cron rendering; it
is not an active event version and is not sufficient for enablement.

Before deployment, follow the public operations runbook and obtain separate approval for
each external mutation. Replace the incomplete schedule manifest from a reviewed active
schedule, regenerate both cron lists, set the exact public SHA and private repository,
re-run the per-job rounded cost projection from `config/data_repo.toml`, and leave
deployment disabled unless projected paid Actions minutes and paid storage are both
zero. The checked reference projects 143 billed minutes: eight nonce jobs (8 minutes),
sixteen budget-admission jobs (48), eight private workers (24), eight public-dispatched
workers (24), ten heartbeat jobs (30), settlement (2), correction (1), and
retry allowance (6). Durations are conservative planning assumptions, not measured
runtime guarantees. It projects six admitted odds credits and 100 MiB retained.
Both verified allowances are zero, so those values are blockers rather than permission
to spend. The odds hard stop is 400 requests and may only be lowered for deployment.

Each job checks out this repository at `${{ github.workspace }}/data` and public code at
the sibling `${{ github.workspace }}/code`; every production CLI call receives those
absolute data/config paths and the separately reviewed `NFL_V2_ARTIFACT_REGISTRY_SHA256`
repository variable. Before work, the job independently hashes the public and
private dispatch manifests, the active-event manifest, and every listed active-event
data file, then cross-binds those hashes to reviewed config and the dispatch envelope.
The year and manifest season must both be exactly 2026.

The three workflows provide these independent paths:

- `capture-and-forecast.yml` requires both the trusted workflow actor and event sender
  to match the exact allowlist. It atomically writes, commits, and pushes the nonce with
  one optimistic retry before checking out public code or exposing `ODDS_API_KEY`.
- `private-due-check.yml` runs the same idempotent due command on different
  schedule-derived minute offsets, plus target+5 heartbeat-only cron ticks.
  Both capture paths require a successful `admit-budget` job before provider secrets.
- `settle-and-report.yml` separates postgame settlement from an official-correction
  pass and never changes previously issued forecasts.

Manual scheduler time is accepted only as `YYYY-MM-DDTHH:MM:SSZ` and travels through a
random-delimited step output, never raw `$GITHUB_ENV`. Every provider attempt refreshes
the real UTC clock and rechecks its origin window. The frozen private configuration
permits at most two odds admissions per obligation across all triggers and reruns;
exhausted admissions leave the football forecast running without market evidence.

Admission appends hash-chained budget snapshots beneath
`ledger/budget-reservations/<month>/`. It charges each reservation before pushing and
before a worker sees provider secrets. The fast-forward push is a compare-and-swap;
a failed push exports no admission, and budget snapshots are never rebased. Successful
admission binds the exact data commit and trusted `github.run_id:github.run_attempt`.
A rerun of only a failed worker cannot reuse an older successful admission. Local claim
records prevent a second request in the same execution; crashes retain spent credits,
and authoritative quota headers can increase consumption but never refund uncertainty.
Unattempted prepaid admissions remain additional to provider-observed usage, so a usage
jump to the hard stop prevents another admitted claim. Retry suppression checks only
claims belonging to the current trusted execution, never historical unrelated claims.

Public budget admission starts at the exact nonce commit exported by its validation
job. Every detached worker publishes to the explicit private branch ref with a
fast-forward push. The explicit private budget CLI reads and hashes real active-event
manifest/data bytes, derives requests and job counts, and exits nonzero on blockers.

The heartbeat job runs after admission and the provider finish, using an `always()`
dependency condition so it still runs after failure or skipped capture. It checks out
and validates the latest explicit private branch before writing; all three workflows
share one writer concurrency group. This prevents competing sibling commits. It
records the first observation at or after target+5 and consults
persisted domain status. `FAILED` and `MISSED` are failed heartbeats; only `COMPLETE` and
`FOOTBALL_ONLY` are complete. Stable identity is event/origin/forecast-policy, independent
of scheduler time and code revision. GitHub may delay or drop scheduled runs, so +5 is
a scheduled check, not a delivery guarantee; later ticks and manual runs detect gaps.
Alerts expose only event, origin, status and code SHA and update one deduplicated issue.

Only new files under ledger, raw, reports, lineage, forecast and outcomes-and-reports
may be pushed. The shared NUL-safe guard checks staged, unstaged and untracked paths
and rejects tracked modifications, deletion, copying and renaming. Prior records stay
unchanged. No workflow places a wager.

Immutable `terminal-publication-v1` evidence now binds the execution key, run and
receipt hashes, and original publication/post-fsync UTC observations. Original
publication deadline checks precede proof creation; restoring proof is not a new
publication. Legacy or interrupted receipts without valid proof remain unverified;
never manufacture proof from checkout metadata. The offline Git-restore and heartbeat
probe passes. Overall Task 5/deployment acceptance remains blocked by two existing
outcomes regression assertions that require post-publication metadata changes to
invalidate valid proof; test-only reconciliation awaits scope approval. All other
readiness and allowance gates still apply.
