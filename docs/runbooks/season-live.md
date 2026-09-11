# Continuous season forecasting

Run `uv run python ops/season_live.py --once` to capture current free sources, reconcile
the schedule, preserve final outcomes/corrections, generate only eligible pregame revisions,
and rebuild scorecards. Run without `--once` for the season daemon. It has no Week 1 stop.
The existing viewer at http://127.0.0.1:8510/ reads `/api/season`. `/api/week1` retains the
original Week 1 snapshot API. No raw files or credentials are served.

## Honest model and data status

Production remains the explicitly labeled uncalibrated Elo fallback. It reuses V2's Elo
and tie components, reconstructs 2016–2025 ratings, and updates them with finalized current
season results observed before generation. Original Week 1 records are read unchanged.
Historical reconstruction does not establish historical point-in-time availability.
The full V2 registry, complete feature snapshots and reviewed artifacts remain a separate
dependency; a complicated challenger is not automatically promoted.

Official NFL injury reports, nflverse timestamped depth charts, ESPN game status and weather
are archived with actual retrieval times. Depth-chart QB1 is an expected starter, not official
confirmation. Missing, ambiguous, stale and unparsed sources must remain visible. Weather
without a provider issue time is provenance-limited. None of these optional signals gets an
invented probability adjustment; changes cause an evidence-bearing revision with unchanged
probability when the fitted model has no supported adjustment.

## Scheduling and immutability

Normal input checks occur every two hours. Near kickoff and while results are expected,
checks occur every five minutes. Targeted T72/T60 runs keep the existing ±10-minute windows;
the final pregame target is T−5m and publication must finish before kickoff. A late origin
is missed, never backdated. Kickoff/venue changes create a schedule version and invalidate
prior-version forecasts for official scoring while retaining their history. TBD, postponed,
missing-source and in-progress games cannot receive new pregame predictions.

A process lock prevents concurrent writers. Every revision stores model state, code base
SHA, dirty-worktree flag, exact source hash, dependency lock hash, input times and change
reasons. Source versions are archived privately, including precommit versions. Publication
requires a durable candidate, receipt and original before-deadline durability evidence.
Unreceipted/late candidates remain invisible. No forecast file is overwritten.

Private state defaults to `~/nfl-predictor-live-data/season` and may be overridden by
`NFL_SEASON_DATA_DIR`; repository paths are rejected. Configuration is in
`configs/season_live.toml`. Captures, outcome revisions, forecast revisions, source versions,
scorecards and scoring policy stay private. Public code never contains those datasets.

## Official scoring policy

The private `scoring-policy.json` is created once with the real effective time before new
forecasts. Games already started at that instant are explicitly retrospective-policy;
we do not claim the policy was declared before their outcomes. The official prediction is
the latest valid production-role forecast before kickoff matching the current schedule
version. Separate T72, T60 and FINAL scorecards select only their own origins. Selection
never depends on the outcome. Fallback and challenger models remain identifiable.

Winner accuracy excludes ties; three-outcome log loss and the sum-of-squared-errors Brier
score include ties. Missing forecasts remain in coverage denominators. Pending games and
missed forecasts are distinct. Results settle only on an explicit final source status with
valid scores; final scores can be corrected, and retractions return games to unresolved.
All source result revisions remain in the history. Pairwise comparisons use the same games
and horizons, never separate convenient samples.

Weekly analysis is computed from the selected pregame records and finalized results. It
includes wins, confident misses, calibration and source gaps. Game outcomes alone do not
establish a defect or an injury effect. Production models never rewrite or promote themselves.

## Hosting status

Local launchd is the current execution environment. This Mac must remain awake and online.
GitHub Actions is enabled for the public code repository, but no private NFL data repository
or deployed application exists. An unattended private runtime needs persistent private
storage, an authenticated dashboard and a verified zero-paid-use compute allowance; these
are not provided by the public code repository. No paid service is enabled as a workaround.

## Verified official inactives

The source adapter discovers NFL single-game news articles from the official inactives page,
requires both teams and matching game/week, and stores publication, modification and capture
times. Reports must be published within the configured 72-hour pregame window; future source
times and postkickoff body modifications are rejected. Newer verified reports take precedence
over link order. January games retain their NFL season identity. NFL-only curl redirects are
enforced. Missing/unpublished articles and unsupported roundup formats remain explicit gaps.
Reports first captured after kickoff cannot enter any earlier forecast. The Elo fallback does
not apply unsupported injury or inactive probability adjustments.

An end-to-end result regression verifies final → corrected final → retracted → restored final
across view-cache loss, preserving the exact pregame forecast and all four outcome versions.
