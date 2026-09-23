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

## Readable forecast and analysis milestone — 2026-09-11

Ruling: user's explicit implementation request authorizes this interface/analysis design and execution without another planning-only approval. Preserve prior work; betting/playoff simulations deferred. Existing simulation artifacts/code retained, automatic new simulations paused by config for this milestone.

Design: existing Python HTTP stack, light cool-gray canvas #f3f6fa, white game rows, navy #142d4e text, blue #175cd3 and teal #087f8c probability segments, amber #b55b08 uncertainty. Large tabular probabilities/full team names; readable system sans; matchup-focused two-column desktop/single-column mobile, same real data. Game pages show saved explanation and postgame review before expandable audit details.

Scope: ops/week1_viewer.py, ops/viewer/index.html, ops/viewer/app.js, ops/viewer/styles.css; ops/season_analysis.py, ops/season_postgame.py, ops/season_live.py, ops/season_scoring.py; configs/season_live.toml; tests/test_season_analysis.py, tests/test_season_postgame.py, tests/test_season_live.py, tests/test_season_scoring.py, tests/test_week1_viewer.py; docs/runbooks/season-live.md, tasks/todo.md, tasks/lessons.md. Root tasks bookkeeping only.

- [x] Audit actual branch/API/artifacts: remote81c60e0, cleanV2, seasonElo fallback, workerhealthy, genuineSF@LA pregamerecord+final.
- [x] Persist explanations with new forecasts; reconstruct old explanations only from preserved model/input evidence and label reconstruction time.
- [x] Capture free sourced postgame summary/PBP where available; immutable review/scoring/improvement records, corrections and idempotency.
- [x] Build readable responsive dashboard, clickable /games/<id>, performance view; model identity and tie probability explicit.
- [x] Connect matched horizon update comparisons, candidate experiments and decisions; never outcome-select revisions or auto-promote.
- [x] Verify realSF@LA flow, allgamecoverage, deadline/clock/corrections/duplicates, desktop/mobile/screenshots, meaningful regression suite.
- [x] Activate locally, verify URLs/worker, commit/push coherent V2 milestones, report exact full-model/data/hosting blockers.

Status terms: implemented, tested, running locally and deployed are separate. Localhost is not clouddeployment.


### Analysis milestone verification — 2026-09-11
- Implemented: exact saved Elo explanations, prepublication embedding, labeled immutable reconstruction; official forecast reviews, sourced final-game statistics/scoring plays; immutable improvement and historical evaluation records.
- Implemented: responsive game cards/details/performance, weekly navigation, tie mass, separate missing vs incorrect status, matched revision deltas and version records. No betting or new playoff simulation work.
- Tested: full suite **1,187 passed**, no exclusions; changed-file Ruff clean. Includes schedule/kickoff cutoffs, stale input handling, publication durability, win/loss/tie/missing/corrected/retracted results, evidence identity/null source handling, immutable explanations and repeated report/evidence reuse.
- Running locally: real SF@LA original pregame → finalized27–7 → frozen official probability scores → reconstructed explanation + sourced postgame review + observed hypothesis. No synthetic live results. Desktop1440 and mobile390 browser checks: correct game click, zero JS errors, no horizontal overflow on dashboard/detail/performance.
- Running locally: season worker restarted with analysis integration; healthy heartbeat verified. Latest source check22:15Z; nextscheduled00:15Z. Existing two-hour input and candidate-evaluation cadence retained.
- Still blocked: no reviewed full V2 champion package/registry or validated historical original pregame timestamps; EPA candidate worse than Elo. No matched live challenger scorecard yet. New context experiments await valid timestamped data and predeclared untouched periods.
- Not cloud deployed: loopback viewer and launchd require this Mac awake/online; no verified zero-cost private persistent hosting/storage/authentication. Simulation code/history retained, new runs paused by config.
- Source/UI milestone committed and pushed: bbf052ec810ef603ded33ee715a44fe9af77d3fb; remote SHA verified equal to local. Final worker restarted; dashboard /health returned status=ok.

Final checks: 272/272 live game-detail routes reconcile with scorecards; six isolated browser outcome states pass; weekly selector works; actual new ATL@PIT explanation is VERIFIED/PREGAME at generation; original SF@LA forecast hash unchanged. Desktop/mobile/dashboard/game/postgame/performance screenshots captured privately under /tmp. Fullsuite1,187passed, zeroexclusions, changedfileRuffclean.


Delivered URLs (local only): http://127.0.0.1:8510/ ; http://127.0.0.1:8510/games/2026_01_SF_LA ; http://127.0.0.1:8510/performance .
Source milestone: https://github.com/Su760/nfl-predictor/commit/bbf052ec810ef603ded33ee715a44fe9af77d3fb . No credentials, private captures, DBs or screenshots staged. Accepted unchanged legacy lint exception preserved. The final source test run passed1,187; no skipped/excluded regression tests. All requested unblocked interface/analysis work is implemented, tested and running locally; full V2 promotion and cloud deployment remain explicitly blocked above.


## Probability improvement continuation — 2026-09-12
Continue the existing blocked context/model evaluation tasks; no replacement infrastructure.
User explicitly authorizes implementation and V2 push. Production Elo identity/policy and
original records stay unchanged; challenger forecasts remain shadow/research with no promotion.
2025 was inspected: it is a known benchmark, never again called untouched. Freeze experiment
config before evaluating; development <=2023, validation2024, known benchmark2025;
prospective eligible future2026 forecasts generated after this experiment freeze are untouched.
Never substitute eventual target starters for historical expected-starter availability evidence.

Scope: ops/season_live.py, ops/season_sources.py, ops/season_scoring.py, ops/season_analysis.py;
new ops/season_probability.py, ops/season_qb.py, ops/season_shadow.py;
new configs/season_probability.toml; configs/season_live.toml;
ops/viewer/app.js, ops/viewer/styles.css; corresponding tests/test_season_probability.py,
tests/test_season_qb.py, tests/test_season_shadow.py, tests/test_season_live.py,
tests/test_season_sources.py, tests/test_season_scoring.py, tests/test_week1_viewer.py;
docs/runbooks/season-live.md, tasks/todo.md, tasks/lessons.md. Root task bookkeeping only.

- [x] Verify clean V2 at4d90616 and live healthy heartbeat03:47Z Sep13; next04:01Z; actual upcoming14Week1games and retainedSFfinalreview.
- [x] Audit/reproduce fixed production Elo; evaluate tuning/calibration on declared chronological periods, matched metrics/uncertainty; inspect EPA failure.
- [x] Audit timestamped QB evidence, fit minimal residual QB challenger when supported; archive prospective evidence now; historical eventual starter use research-only.
- [x] Integrate immutable pregame shadow predictions and independent scorecards/reviews, missing/conflicting QB fallback explicit, no official selection change.
- [x] Focus UI: scored sample alongsideaccuracy; distinguish notpublished/fetchfailed/stale/unused; lastcheck vs savedforecast and nochange explanation.
- [x] Verify real lifecycle and baseline/challenger example; full meaningful regressions, localactivation, coherent V2 commits/push and exact blockers.

Interface review: probability evaluator owns frozen config+private evaluation artifacts; QB module owns player evidence/model and conditional calculation; root shadow runtime consumes both after validation, never alters production policy. UI consumes existing fields plus root shadow status; no forecast writes. Tests stay separated by module. QB conflict observed ATLdepthTagovailoa vs injuryOut must remain unresolved, not a weighted guess. No architecture conflict identified; shared runtime edits owned only by root.


### EPA audit correction, within probability-improvement scope
Confirmed root cause: ops/season_rebuild.py globalqb_epa/cpoe nonnull gate removesallrushes;
normalizer runtime/capture.py also globallyrequiresQBmetrics. 2025raw48,771plays/15,347rushes
becomes17,490passes/0rushes; all2593cachedrows haveoff_diff==pass_diff andrush_diff==0.
OldElo comparator is a fitted logistic Elo-difference baseline, notexactliveformula.
Expand edit scope to ops/season_rebuild.py, src/nfl_predictor/runtime/capture.py,
tests/test_season_rebuild.py, tests/runtime/test_capture.py. This repairs the existing
requested EPA evaluation integrity, not a new featuregroup. Preserveoldprivatecached
selections/datasets/reports in their namespace; newselection/dataidentity only. Separate
team EPA eligibility from QB observation eligibility; neverzero-impute missingQBstats.
Regressionmixedpass/rush/nullQBmetrics plus leakage/captureguards required. Evaluate new
EPA results only as research with2025knownbenchmark; no promotion or retroactive overwrite.
- [x] Repair/review/testEPA source selection andnormalization; preserveoldcache; reportnewdataquality/metrics ifunblocked.


Probability continuation progress Sep13 continuation: EPArepair implemented/reviewed/tested (67focused; fullsuite1213pass) and pushed b72556e. Corrected2593-row private rebuild is running separately; outputs not yet evaluated; originaldataset/report retained. UIinputstatus/sampleN changes implemented/tested and servedlocally. Shadow immutablepublication/pairedscoring implemented/tested; prospectiveinputcapture running, actualmodels pendingactivation. Scope adds configs/season_qb.toml for separate QBhyperparameterfreeze beforeQBfit; Elofreeze remains unchanged. Production policy unchanged.

Improvement-view audit task: label verified defective legacy EPA dataset by configured content hash, retain its result, load corrected immutable experiments separately; regression test before local activation.


Probability milestones verified Sep13: EPA sourcefix b72556ecbc805a1f6f39aacf9a174cad3ac71102
and chronological evaluator840c141 are committed/pushed toV2. Evaluator15tests/Ruffpass.
CorrectedEPA build/evaluation COMPLETE2593rows/265benchmarkgames; original3artifacthashes
unchanged; EPA stillworse0.703729vsfittedElo0.661164. No researchjobs remain forEPA.
Calibration SHADOW-RUNNING locally:29prospectiveforecasts04:45:52Z, independentN0scorecard,
nooriginal573productionfilechanges; actual desktop1440/mobile390dashboard/detail/performance
verified0JSerrors/nooverflow. Workerreloadedpid63892,healthy05:24:56Z,next06:45:52Z.
QBimplementation/alignment/tests/realfit/stateintegration still IN PROGRESS; not promoted
and noQBforecast claimed. Pending finalintegration/fullsuite/secondsourcecommit/ledgerclosure.


Sep13 QB review continuation: extend existing experiment scope to ops/season_qb_experiment.py
and tests/test_season_qb_experiment.py for a reproducible archived-input CLI, not a new plan.
QB fitting excludes ties from the conditional likelihood while retaining ties in proper scores.
Duplicate player/game observations and nonfinite EPA fail closed; historical state starts2016,
strict final availability remains before each cutoff. Uncertain starters yield unweighted
conditional scenarios, separate immutable publication and no scorecard inclusion. Fullsuite
1257passed without exclusions before final CLI/integration; 45focused tests pass after saved
feature-value evidence added. Original573productionfiles remain hash-identical.


Probability milestone implemented/tested/running locally: calibrated Elo and corrected QB
shadow records, independent scorecards, immutable conditional scenarios, exact saved features,
free-source refresh and missing-input guards. First10QB forecasts06:08Z; latest06:27Z
20QB revisions/10games and116calibration revisions/29games; finalized shadow sample0.
Original573productionfiles unchanged. RealSF@LA pregame→final→score→review remains retained;
Week1coverage15/16,0/1correct,14pending,1missing. No backfills or production model promotion.
Actual desktop/mobile clickthrough/probabilities/conditional uncertainty verified. QB CLI
repeated-run reuse verified, all1850reproduced probabilities exact; corrected QB loses on
matched2024/2025 periods. Exact metrics/artifacts/reproduction in docs/runbooks/season-live.md.
Final verification:1264tests passed in20.83s, zero exclusions; changed-file Ruff and git diff--check clean. Saved baseline and QB probabilities reproduced with zero error. Source milestone includes this ledger; commit/push verified separately after staging only the declared source/config/docs/tests. No secrets, raw inputs, databases or screenshots are newly tracked.
Blocked: reviewed champion registry, original historical expected-starter availability receipts,
sufficient positive prospective evidence, unavailable current inputs/stats for individual games,
verified zero-dollar persistent private cloud host. No paid services enabled. Betting/playoffs deferred.


## Final live operation check — September13,11:48CDT
Source milestone f823ba78462be4fb97b9742ea34f41d70667ef32 is pushed to V2; remote matches,
worktree clean at verification. Actual11:46:05CDT source/forecast cycle succeeded, next11:51:05;
348calibration revisions across29games and100QB revisions across10games, prospective settledN0.
Week1official record0/1correct,14pending,15/16coverage remains unchanged. Paidusage0.

Operational limitation observed, not hidden: eight noon games missed T60. Worker log scheduled
16:00UTC but next completed at16:12:15UTC, outside the10minute horizon window. macOS power
logs show sleep across that interval (11:01:34–11:11:10CDT, plus earlier maintenance sleep).
No caffeinate process/assertion existed. Existing pregame forecasts remain valid; no later
revision was relabeled T60. FINAL snapshots remain scheduled before noon kickoff.

Local mitigation installed in the existing private LaunchAgent:
~/Library/LaunchAgents/com.su760.nfl-season-worker.plist ProgramArguments now wrap the
existing Python command with /usr/bin/caffeinate -i. Original plist is preserved privately
under season/operation-backups/100437337…plist. LaunchAgent reloaded; initial bootstrap
returned input/output error during unload, retry succeeded after service removal. launchctl
reports running; pmset confirms the worker-specific PreventUserIdleSystemSleep assertion.
WorkerPID54635/guardPID54636, healthy11:48:40CDT. This prevents idle sleep only; closed-lid,
shutdown, network loss and unavailable private always-on hosting remain real dependencies.
It does not recover missed windows or weaken kickoff deadlines. Rollback is the preserved
plist followed by bootout/bootstrap of the same label; no global power settings were changed.

## Week1/Week2 audit — September16 UTC
User authorizes operational/scoring repairs and verified source commits, preserving Elo and all historical forecasts.
- [x] Read-only audit: 16 finals, 15 production forecasts, 9 correct; paired shadow metrics recovered.
- [x] Repair verified ESPN date-range HTTP400 via separately archived calendar-year captures, retain complete schedule validation.
- [x] Make failed/stale checks and missed windows visible; retain actionable provider errors.
- [x] Verify automatic settlement/review idempotency and corrections, live cycle, historical hashes, Week2 readiness.
- [x] Publish per-game report, matched comparison, improvement evidence and paper-only dependencies/policy.
- [x] Verify tests and live behavior; commit/push scoped V2 changes.
Repair scope: ops/season_sources.py, configs/season_live.toml, ops/week1_viewer.py, ops/viewer/app.js, tests/test_season_sources.py, tests/test_week1_viewer.py; tasks/todo.md and docs/runbooks/2026-09-15-week1-audit.md.
Root cause: season_sources.py:600 requests a now-failing ESPN date range; year queries work but require both calendar years for 272 games. _curl_download at :97 throws a verbose command error whose truncation hides the source/reason. Viewer exposes worker failure only inside collapsed operations and leaves worker_status HEALTHY from old view.

QB readiness scope expansion: ops/season_qb.py, ops/season_shadow.py, tests/test_season_qb.py, tests/test_season_shadow.py plus existing configs/season_live.toml. Configured legacy player_stats.parquet ends2024; official current stats_player/stats_player_week_2025.parquet and _2026.parquet are available with team renamed from recent_team. Add explicit timestamped supplemental source receipts for state refresh only, retain frozen coefficient/config/primaryT60 and all historical records. Verify duplicates, source-season mismatch, future captures/finals, complete both-team coverage and real Week2 shadows.

Audit verification: source range HTTP400 repaired, nine added cases pass;123 scoped tests pass/1 optional capture skip. Full suite1230pass/42inherited failures/1skip; equivalent untouched baseline1221pass/42fail/1skip. No full-green claim. Browser desktop/mobile and visible failures verified. Week1 9/15,15/16coverage; immutable weekly report5321b349 retained.13,380original forecastfiles unchanged. Week2 production16/16, calibration16/16, QB1valid/15missinginjury; fitted definitions and primaryT60 unchanged. Full report docs/runbooks/2026-09-15-week1-audit.md. No paid calls/wagering; paper eligible-price/fee/contract dependency documented with frozen NO_BET preparation.

## Historical evaluation and football dashboard — September16
User explicitly requests short plan then execution; no further approval gate.
- [x] Audit cached feature lineage at T72/T60; freeze bounded ablations (remove passing EPA, remove rushing EPA), expanding vs last3 estimator seasons, fit only before each evaluation season. Evaluate 2021–2025, all previously reused research; no untouched historical claim.
- [x] Build read-only ratings/team/market insights from existing captures; add dashboard routes and historical performance filters, preserve styling and forecast history.
- [x] Run real evaluation, regression checks, desktop/mobile inspection, preservation hashes; publish actual findings and limitations.
Scope: tasks/todo.md; configs/season_insights.toml; ops/season_evaluation.py; ops/season_insights.py; ops/week1_viewer.py; ops/viewer/{index.html,app.js,styles.css,insights.js}; tests/test_season_evaluation.py; tests/test_season_insights.py; docs/runbooks/2026-09-16-football-insights.md.
Historical limitation: season_rebuild.py:479 selects history by preceding calendar date; :550 builds at reconstruction time. New evaluator must inspect saved selection IDs and reject any row with a result-availability proxy >= requested cutoff, including all priors. Do not relabel all cached rows as T72-safe. Preserve originals. Use existing model, scaler, chronological fold, calibration and score functions; all parameters fixed before run. Market experiment blocked by absent historical same-time paired prices; no fabricated inputs or spreads.

Re-plan after lineage audit: T72 has only2 eligible rows in each2022–2025 cached season, one-class calibration causes a clear fit failure; do not loosen cutoff or switch calibration to fabricate coverage. T72 reports full-schedule production/simple Elo only, EPA variants explicitly unavailable. T60 has191–204 eligible evaluation games/season and supports declared matched comparison. Next milestone remains native cutoff feature rebuild. Initial evaluator integration also exposed tuple return from historical_games; corrected unpacking after reading function. No production code affected.

Delivery evidence:985 matched T60 games/1359 finalized source games; T72 Elo1359, football unavailable due cutoff-safe sample failure.36 focused tests pass; browser1440/390 verifies actual routes/filters/timing, no overflow/JS errors.13,380 historical files hash-identical. Archived price comparison14/16Week1 and16/16Week2; no betting record. Runbook docs/runbooks/2026-09-16-football-insights.md. Production unchanged; source changes uncommitted.
Final checks:1243passed/42 inherited failures/1 optional skip; exact baseline failure-name match. All2593 cached kickoff/result/PBP lineage identities verified. Worker healthy/source fresh/paid0; ratings reproduce production exactly. Failed and successful evaluation declarations retain explicit run-status records.

## Cutoff-native football reconstruction — implementation plan
User approved beginning the next milestone; prior plan-and-execute authorization remains in force. Use executing-plans inline, no commit/push or production promotion.
Goal: recover matched T60 coverage and enable T72 evaluation with actual cutoff-selected completed-game features.
Architecture: separate research-only builder reuses existing opponent-adjusted EPA, prior regression and blending, producing only the four existing EPA features. Each horizon gets immutable rows and history lineage. Existing classifier/calibrator/folds remain unchanged. Historical final availability remains the explicit kickoff+24h proxy, not invented historical receipts.
Scope: tasks/todo.md; new ops/season_cutoff.py; new tests/test_season_cutoff.py; configs/season_insights.toml; ops/season_evaluation.py; tests/test_season_evaluation.py; ops/viewer/insights.js; docs/runbooks/2026-09-16-cutoff-native.md. Private derived artifacts only under existing insights data root; preserve earlier reports and all forecasts.
- [x] Freeze experiment configuration/source/code lineage; implement strict horizon selections, prior-season-only priors, directional PBP closure, immutable resumable output and regression tests. Verify parity with old features where histories match.
- [x] Reuse season-forward evaluator on new four-feature matrices at both horizons; rerun the same declared ablations and training-window comparison on identical games. Report recovered/excluded coverage and differences on prior matched samples separately.
- [x] Publish actual results in existing performance view, verify cutoff mutation/invariance, reproducibility, real desktop/mobile rendering and forecast hashes; document limits and next evidence-driven milestone.
Root cause: ops/season_rebuild.py:480 filters history by kickoff date, :550 uses reconstruction time. A strict caller-side audit excluded348 timing-invalid and26 unavailable cached T60 rows across2021–2025; T72 insufficient. EPA features themselves do not require target venue, QB, weather or current-game statistics; their rebuilt inputs can cover neutral sites without inventing unused features.

Interface finding: EpaLogisticModel.fit/predict delegates to baselines._as_feature_matrix, which requires exactly42 columns (src/nfl_predictor/models/baselines.py:79). Native rows correctly contain4 measured features, so the initial native evaluation failed before publishing scores. Reuse that model's identical scaler/logistic pipeline through a finite, shape/label-validated four-column adapter; preserve the production42-column wrapper. Add adapter parity and future-data invariance tests. No placeholder padding.

Delivered cutoff-native build 0a57cbea164b90ad7079ca7146f44412a3b39270eed913257d35c888bd623e81 and evaluation 804a12a745dad3afe41cc391e7ad0d5dc5eda3ce558056af81868aa264fe9c7f. Both horizons1359/1359 evaluation coverage; no promotion. Same985-game EPA log loss improves0.669221→0.664591, full-population0.673393; production Elo0.663456 fullT60.46focused tests pass;1253pass/42inherited failures/1skip full suite; real desktop/mobile verified;13380original files preserved. See docs/runbooks/2026-09-16-cutoff-native.md. No commits/pushes, paid usage or production-policy changes.

## Elo-plus-EPA residual milestone — 2026-09-16
User approved beginning the next milestone; execute this bounded research comparison.
Scope: configs/season_residual.toml, ops/season_residual.py,
tests/test_season_residual.py, ops/season_insights.py, ops/viewer/insights.js,
docs/runbooks/2026-09-16-elo-epa-residual.md, tasks/todo.md.
- [x] Freeze one four-feature EPA residual with fixed Elo logit offset and ridge penalty;
  compare production Elo and identically fitted/calibrated intercept-only Elo control.
  Estimator 2018..T-2, calibration T-1, evaluation 2021..2025; T60 primary, T72 secondary.
  Reuse cutoff-native lineage; all years previously inspected; no historical holdout.
- [x] Run real matched evaluation, preserve immutable declaration/predictions/report,
  report per season and overall, paired uncertainty, coefficients and calibration.
- [x] Publish separate research results in existing performance dashboard; verify desktop/mobile,
  chronology and matching regressions, deterministic rerun and forecast preservation.
No parameter search, model promotion, paid use, wagering, deployment or commits.

Residual delivery: 1,359 matched games/horizon, 8,154 reproducible predictions. EPA incremental
benefit inconclusive; 2025 deterioration; no promotion. 51 focused tests pass; full suite
1,258 passed / 42 unchanged baseline failures / 1 optional fixture skip. 13,380 preserved
forecast files unchanged. Desktop/mobile verified; report docs/runbooks/2026-09-16-elo-epa-residual.md.

## Historical early-season reporting continuation — 2026-09-21
Approved implementation in the existing V2 worktree. Reuse immutable cutoff-native predictions;
do not rerun completed model experiments, alter production Elo, or rewrite archived forecasts.
Scope: tasks/todo.md; ops/season_evaluation.py; tests/test_season_evaluation.py;
ops/viewer/insights.js; docs/runbooks/2026-09-16-{football-insights,cutoff-native}.md.
- [x] Derive Weeks 1–2 and full-season metrics from the saved native prediction artifact for
  each horizon/model, enforcing identical game IDs and cutoffs and retaining paired uncertainty.
- [x] Publish a deterministic immutable report update and refresh only the mutable dashboard view;
  distinguish legacy 622/982 from full-coverage 848/1355 and disclose coverage/ties/limitations.
- [x] Verify focused checks, exact inherited 42-failure fingerprint, forecast preservation,
  dashboard output, secrets/private-data exclusion, then review, commit and push V2 only.

Review re-plan: extend scope to ops/season_cutoff.py, tests/test_season_cutoff.py,
ops/season_insights.py, tests/test_season_insights.py and ops/viewer/app.js. Require identical
season/week/outcome context in paired samples; hash all direct report/build dependencies; strip
absolute artifact paths from the local API; show winner counts/accuracy in each live horizon row.
These are concrete pre-commit correctness/privacy findings, not a new experiment or model change.

Delivery verification: report update d8f2db1b69d662d62f79e0bcef652945127ff7dd16156a9309a058f967529239;
32 focused tests pass. Full suite 1,262 passed / 42 failed / 1 skipped; all 42 failure names
exactly match the pre-change set (1 runtime services, 22 forecast, 19 outcomes). Clean committed
HEAD has those 42 plus two already-repaired failures, so this milestone adds none. Immutable native
evaluation/prediction SHA256 remain 143c4d41…/dfd588fd…. API and repository diff contain no
machine-local paths, credentials, raw captures, DBs or secrets. Production Elo/policy unchanged.

Follow-up, separate from this milestone: repair the 42 runtime/forecast/outcome test failures by
making test clocks and filesystem publication-time fixtures deterministic. Investigate the fixed
2026 kickoff and real marker ctime interaction first; do not weaken production timestamp enforcement.

Backlog: design a future paper-betting tracker using prospectively captured timestamped two-sided
odds, explicit eligibility/fees/settlement rules, and prospective results. No betting execution.

## Runtime receipt-clock repair and live freshness — September 23, 2026
Authorized bounded continuation on `codex/nfl-predictor-v2`; preserve main-worktree changes,
production Elo, archived forecasts, cutoff enforcement, and prior historical experiments.
Scope: `src/nfl_predictor/workflows/forecast.py`; `tests/workflows/test_forecast.py`;
`tests/workflows/test_outcomes.py`; `tasks/todo.md`;
`docs/runbooks/2026-09-16-football-insights.md`. Add live-operation files only if a read-only
freshness audit proves the existing supported refresh process needs repair.
- [x] Add a narrow receipt-marker metadata reader seam whose production default remains real
  filesystem `st_ctime_ns`; bind fixed-date tests to their logical clocks.
- [x] Add focused on-time, late, and anti-backdating regressions; resolve the exact saved
  1 runtime / 22 forecast / 19 outcome failure set before running the full suite.
- [x] Audit worker/source timestamps, saved Week 2 outcomes, and Week 3 horizon coverage without
  regenerating completed-game predictions or overwriting archived forecasts; repair only through
  the documented supported refresh path if stale and record missed cutoffs explicitly.
- [x] Update the runbook with root cause and evidence, run lint/diff/secrets checks, and prepare
  the scoped V2 commit. Push/remote-SHA verification immediately follow this recorded work.

Verified before commit: representative real ctime was 2026-09-23T07:08:48Z against a fixed
2026-09-13T19:35:00Z deadline. All 239 pre-change tests in the affected modules passed under the
diagnostic fixture-time reader; the implemented suite is 241/241. Full suite: 1,306 passed and one
optional real-capture fixture skipped, zero failures. Live worker healthy, source cycle05:27:35Z,
Week2 finals16/16 and official10/16 correct; Week3 official16/16, T721/16 with every other horizon
not yet due and no missed cutoff. No refresh, forecast regeneration, model search or promotion ran.

## Historical accuracy claim reconciliation — September 23, 2026
Authorized read-only artifact and git-history audit from V2 commit `4b175aa`; no training search,
model promotion, forecast rewrite, betting work, or main-worktree changes.
Initial scope: `tasks/todo.md` and the existing historical evaluation runbooks. Change a dashboard
label only if saved artifacts prove it misleading; declare that file before editing.
- [x] Inventory every repository and private saved artifact that can support the reported
  62.58%, 68.75%, 63.34%, and approximate 66–68% claims, including relevant git history.
- [x] Reconstruct each evaluation contract: model/version, train/validation/evaluation seasons,
  cutoff assumptions, season type, coverage/ties/exclusions, and research reuse.
- [x] Where immutable predictions overlap, score old and current models on identical game IDs,
  horizons, and outcome rules; otherwise state that no comparable test is possible.
- [x] Update existing evaluation documentation with a compact provenance/comparability table and
  correct only proven misleading labels. Run checks proportional to documentation/UI changes.
- [x] Review for secrets/private paths, commit and push only V2, then verify the remote SHA and
  report whether the old claim is substantiated and whether comparable evidence shows regression.
