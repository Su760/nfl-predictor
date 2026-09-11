# Season forecasting execution plan

Authority: current user request explicitly authorizes implementation, free source access,
local automation, and coherent verified commits/pushes to codex/nfl-predictor-v2.
Betting work deferred. Main checkout user edits remain untouched. Private data outside Git.

## Milestones and concrete tasks
1. Audit/recovery: verify fd7e189 remote; inventory private registry/active schedule/facts;
   repair two test clocks only (test_fixture_pipeline.py, test_outcomes.py), preserve
   actual production receipt deadlines. Inventory reusable training/split/metric APIs.
2. Season operation: ops/season_live.py, ops/season_sources.py, configs/season_live.toml,
   tests/test_season_live.py, tests/test_season_sources.py. Reuse existing Elo and atomic
   ledger primitives; check sources every two hours and at due origins; short near-game
   checks for material verified information. Immutable revisions, real publication guard,
   schedule versions, final pregame horizon, finalized results/corrections, no Week1 stop.
   Import existing forecasts with original identity/time and explicit legacy provenance.
3. Scoring/visibility: ops/season_scoring.py, tests/test_season_scoring.py,
   ops/week1_viewer.py, docs/runbooks/season-live.md. Freeze policy prospectively now;
   never claim predeclared policy for already started games. Latest valid pregame scoring,
   separate T72/T60/FINAL, all-game coverage, ties, calibration/Brier/logloss, model groups,
   matched Elo comparisons, revision/history and health/next run exposed.
4. Recovery/evidence: reproducible chronological challenger build and manifests using
   existing V2 APIs; evaluate against Elo, reserve untouched period, no auto-promotion.
   Write exact missing access/data evidence. Declare additional builder/test files before
   edits. Weekly immutable error analysis + improvement log, retain wins and misses.
5. Season simulations after operation/scoring work: verify official current NFL rules,
   implement tested standings/tiebreak/seeding/reseeding/no-tie playoffs and uncertainty;
   snapshot probabilities/movement, publish only verified simulations. Declare files first.
6. Run local replacement automation and dashboard, check live output and duplicate/stale/
   cutoff/correction tests, review, commit and push coherent milestones. Zero-dollar cloud
   dependency audit; never create paid infrastructure or expose private inputs publicly.

## Interface decisions / conflicts
Runtime produces dictionaries matching existing Week1 record fields plus revision_id,
schedule_version, model_version/code_sha, reasons, inputs and outcome history. Scoring is
a pure consumer; it cannot generate predictions or select revisions using outcomes.
Legacy Elo snapshots are distinct from future adaptive Elo and any evaluated V2 model.
Official policy first frozen now: earlier kicked-off games labeled policy predates=false.
Outcome finalization requires explicit source final flag; score presence alone insufficient.
An observed schedule change invalidates old-version forecasts for the official latest slot;
history remains visible. New forecasts need a verified future kickoff and source freshness.
Injury/QB/weather captures do not imply supported model adjustments. Missing inputs visible.
Local uptime limitation persists; launchd stays private. No arbitrary playoff tiebreaks.

## Status
- [x] Remote/local fd7e189 confirmed; clean V2 checkout at start.
- [ ] Audit dependency report; deterministic clock repair.
- [ ] Season runtime implemented / tested / running locally (record separately).
- [ ] Scoring policy and dashboard implemented / tested / running locally.
- [ ] Challenger rebuild/evaluation and weekly improvement loop.
- [ ] Verified season/postseason simulation.
- [ ] Reviewed milestone commits pushed; hosting status explicit.
