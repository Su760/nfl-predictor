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
re-run the per-job rounded cost projection, and leave deployment disabled unless
projected paid Actions minutes and paid storage are both zero.

The three workflows provide these independent paths:

- `capture-and-forecast.yml` validates a fixed public dispatch before checking out its
  exact public SHA or exposing `ODDS_API_KEY`.
- `private-due-check.yml` runs the same idempotent due command on different
  schedule-derived minute offsets.
- `settle-and-report.yml` separates postgame settlement from an official-correction
  pass and never changes previously issued forecasts.

Only append-only ledger, raw, and report paths may be pushed. No workflow places a wager.
