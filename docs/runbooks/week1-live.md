# Week 1 live baseline viewer

This separate operational lane produces real-clock pregame Elo estimates using the existing
rating implementation and frozen model-policy parameters. It is not the reviewed V2 champion.
No injury, lineup, weather or odds adjustment is claimed. Results through 2025 supply ratings;
the 2026 schedule supplies matchup, kickoff and neutral-site context. Original raw CSV bytes,
retrieval receipts, model details and immutable forecasts stay in the configured private root.

From the V2 checkout:

```
uv run python ops/week1_live.py --once --on-demand
uv run python ops/week1_viewer.py
uv run python ops/week1_live.py
```

Viewer: http://127.0.0.1:8510/ . JSON: http://127.0.0.1:8510/api/week1 .
The periodic worker checks due origins every 60 seconds and refreshes the free nflverse schedule/results source
at most every 15 minutes unless an outstanding origin is due.
T72 and T60 records can be created only within their configured ±10-minute window and before
kickoff. ON_DEMAND records are never relabeled as scheduled forecasts. Missing past origins
stay MISSED. A Mac that is asleep/offline cannot execute an origin; do not backfill it as live.
All tuning and source/root/port/poll settings are in configs/week1_live.toml.

The V2 cloud deployment templates remain disabled. No private data repository, verified
allowance, reviewed complete active-event manifest or promoted champion registry was found.
Preparing local origins does not enable those templates or authorize paid usage.

The viewer serves only a fixed HTML page, a health route and compact prediction JSON on
loopback. It cannot browse the private raw directory. Do not expose it to the Internet without
an authenticated hosting design. Model estimates are not wager instructions.

On the owner Mac, launchd jobs `com.su760.nfl-week1-worker` and
`com.su760.nfl-week1-viewer` start at login and restart after failures. Their plists and
logs are local private operational state, not repository content. The worker finishes
after the last Week 1 kickoff plus the configured window; launchd does not restart a
successfully finished worker. Mac sleep, logout, power loss or network failure can still
cause missed origins. The viewer detects stale heartbeats after two polling intervals.

`NFL_WEEK1_DATA_DIR` can override the private root (default `~/nfl-predictor-live-data`).
Raw captures and prediction records use atomic, exclusive publication. Forecast files are
never overwritten, and publication checks the real clock after flushing temporary data.

Verification on September 10, 2026 (Central): the live viewer and all 15 saved pregame
forecasts were inspected in Safari and over HTTP. Six new timing/atomicity tests pass.
The full regression run has two known failures: `test_fixture_source_to_scorecard_chain_never_uses_network`
and `test_schedule_flex_uses_newer_obligation_only_event_and_rejects_version_conflict`.
Their fixed fixture deadlines elapsed against actual filesystem receipt ctime, correctly
producing `EXECUTION_CROSSED_WINDOW` / MISSED instead of their expected COMPLETE.
These are not waived as passing; test-only clock repairs remain outside this task's scope.
The remaining suite passes (1083 passed, two explicitly deselected). Scoped V2/new-ops
Ruff is clean; repository Ruff retains the approved 31 unchanged legacy findings.
No legacy lint or production deadline enforcement was weakened.
