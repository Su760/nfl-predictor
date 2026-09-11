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


## Readable dashboard and saved game analysis

The existing loopback viewer serves `/`, `/performance`, and `/games/<game_id>`;
`/api/games/<game_id>` returns the same saved analysis as the game page. Week selection
keeps every scheduled game in coverage. Missing pregame forecasts are distinct from
incorrect picks, pending outcomes, and ties. Large team probabilities retain the separate
tie mass; model identity and input limitations remain visible. Detailed execution and
capture records are expandable. The viewer does not publish private files or synthesize
analysis when someone opens a page.

With `analysis_enabled`, a new forecast embeds its explanation before the normal durable
publication and kickoff checks. Verified Elo explanations reproduce the saved ratings,
policy, venue adjustment and tie layer. They do not attribute changes to unused injuries,
QBs or weather. Older forecasts require hash-verified preserved model/policy artifacts;
reconstructed explanations carry their actual reconstruction time and never imply pregame
publication. Missing proof produces an unavailable explanation, not invented reasoning.
Forecast files and prior explanation records remain immutable.

With `postgame_enabled`, finalized games obtain free ESPN summary evidence through the
existing zero-cost source adapter. Event, teams, final score and outcome version must
match. Raw captures and normalized evidence stay private. Optional missing statistics
produce partial evidence; failures are visible. Successful source checks are separate
from the original evidence capture time. Repeated unchanged summaries reuse evidence;
corrected results or statistics retain new immutable versions. No synthetic results enter
this pipeline. Scoring plays support factual descriptions, not causal injury or luck claims.

Reviews include the frozen official prediction, its explanation reference, finalized result,
three-way probability scores and evidence limitations. Wins and losses receive the same
review process. A favorite losing does not alone establish a defect; the saved probability
of the realized outcome is shown. Score/margin expectations are unavailable for this Elo
model. Retracted results return to pending while preserving earlier review history.

Improvement entries preserve game/forecast/review references, source availability,
hypothesis, experiment dependencies, status, decision and rollback. Existing chronological
candidate evaluations appear separately with their model/data/code versions, folds, matched
results and rejection rationale. A game review never automatically changes a model.
Unstarted context experiments remain explicitly unstarted: they require validated original
pregame timestamps and predeclared untouched evaluation periods. Historical reconstructed
backtests never count as live forecasts.

The performance view reports accuracy and coverage separately, Brier/log loss with sample
sizes, calibration, model-version records and T72/T60/FINAL scorecards. Revision comparisons
pair each earlier horizon (and the earliest valid revision) with the official latest valid
pregame forecast on exactly the same finalized games. Deltas are latest minus earlier;
negative is better. Pending games and identical revisions are excluded. Later forecasts
are never selected according to the outcome, and the deltas are observational rather than
causal evidence. Week-level comparisons use their own matched sample.

Betting and playoff simulation work is deferred. `simulation_enabled = false` pauses new
simulation runs while preserving existing code and private snapshots. The season worker,
two-hour input checks and protected pregame snapshot windows remain active.
