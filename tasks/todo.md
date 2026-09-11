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

## Status — 2026-09-11
- [x] Remote/local fd7e189 confirmed; clean V2 checkout at start.
- [x] Dependency audit and deterministic clock repair committed/pushed 8bb85ed.
- [x] Season runtime implemented and tested; running locally under season launchd agent.
- [x] Scoring policy/dashboard implemented and tested; live http://127.0.0.1:8510/.
- [x] Historical rebuild: 2,593 exact-42 rows; 46 unsupported venue exclusions; all 2015–25 raw captures retained privately.
- [x] Chronological evaluation: 2025 untouched holdout, 265 matched games; Elo logloss .66116 vs EPA .69455. Challenger rejected; Grade-C reconstruction ineligible for promotion.
- [x] Weekly immutable analysis/improvement entries implemented and running; partial until week finalized. No automatic model promotion.
- [x] Season/postseason simulation implemented, tested, running locally; automatic material-evidence refresh and dated history visible. Uncalibrated strength sensitivity explicitly labeled.
- [x] Full suite 1,146 passed, zero exclusions; focused changed-file Ruff passed. Legacy unchanged lint exception remains accepted.
- [x] Season runtime, scoring and simulations committed/pushed 87cbca9; remote SHA verified.
- [x] Candidate evaluate-if-changed ledger implemented/tested and local launchd installed; first scheduled run exited 0. Dataset/code/config identity controls immutable experiment reuse.
- [x] Recovery source milestone committed/pushed 6282b6f.
- [x] Evaluation coverage payload now participates in immutable experiment identity.
- [x] Official single-game inactives parser implemented/tested: both teams, NFL-only sources, current game/week, source timestamps, correction ordering, January rollover. Actual source cycle verified 2026-09-11 07:35 CDT: 272 games and both published NFL single-game articles; after-kickoff captures never applied retrospectively. Final source delta is included in this milestone.
- [ ] Full V2 production promotion BLOCKED: challenger underperforms; historical captures cannot establish original PIT availability; no reviewed champion package/registry.
- [ ] Future official inactives remain MISSING until a verified report is published/captured. Unsupported roundup formats fail closed. Weather lacks provider issuance time. Neither input receives an unvalidated Elo adjustment.
- [ ] Cloud deployment BLOCKED: no persistent private hosting/storage/authentication with verified zero-paid compute allowance. Local Mac must stay awake/online.

## Verified behavior
All 272 regular-season games remain in coverage. Current source checks, forecast generation,
next run and worker health are independent. Source checks every two hours, five-minute near-game
polling, retained T72/T60/FINAL windows, durable pregame publication, duplicate prevention,
actual kickoff changes, metadata-only venue changes, outcome corrections/retractions and
numeric version ordering are tested. Earlier kicked-off games retain retrospective-policy flags.
Original Week1 Elo forecasts are never relabeled as V2. Paid usage remains zero.
