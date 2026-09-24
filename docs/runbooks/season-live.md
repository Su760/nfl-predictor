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


## Production probability audit (2026-09-12)

Production remains `elo-season-v1`. Its source is `ratings/elo.py`, built by
`ops/week1_live.py:build_model` and updated by `ops/season_live.py:model_for_current_results`.
Every team starts at1505; saved 2016–2025 history includes2,761 regular/postseason games.
At each offseason, ratings become1505 + (rating−1505)×2/3; the transition into2026 is
applied once after the completed historical seasons. Current2026 updates use only the
latest explicitly finalized outcome versions observed before generation. Corrections cause
reconstruction from preserved outcomes, never retrospective rewriting of old forecasts.

For each game, conditional home win probability is
`q = 1 / (1 + 10 ** (−(home_rating − away_rating + venue_points)/400))`.
`venue_points` is65 at home and0 at a neutral site. After a final result, let actual be1
for home win,0 for away win,0.5 for tie. The rating change is
`20 × ln(max(abs(score_margin),1)+1) × 2.2/(winner_rating_difference×0.001+2.2) × (actual−q)`.
The same change is added to the home rating and subtracted from the away rating.
`winner_rating_difference` uses the teams' unadjusted pregame rating difference, with its
sign reversed for an away win (the implementation retains the home difference on ties).
Margin affects later ratings, not a direct score-margin forecast.

The separate tie estimate uses only historical regular-season outcomes:
`p_tie = (ties+0.5)/(regular_games+1)`. The verified capture has10 ties in2,639 games,
yielding0.003977272727272727. Final probabilities are
`p_home=q×(1−p_tie)`, `p_away=(1−q)×(1−p_tie)`, and `p_tie`.
All Elo constants and the tie pseudo-count are fixed policy defaults; they were not fitted
by the production pipeline. Ratings and the tie-frequency estimate are learned from past
results. No production sigmoid/isotonic calibration is applied. The new research evaluation
does not retrospectively make those original constants tuned or validated.

Currently used: historical/current finalized scores, team identity, season transitions,
home/neutral venue state, and the historical tie allowance. Collected/displayed but not
used by production probabilities: QB depth charts, injuries, official inactives, weather,
and source freshness metadata. Those captures can cause an immutable context revision
without changing probabilities; a successful check alone does not imply a new forecast.
Missing source publication time stays missing. Prospective capture time proves what this
system had captured then, not when a publisher first made it available.

The previously inspected2025 period is now a known benchmark. New probability experiments
freeze their configuration before fitting; development ends2023, validation is2024, and
future2026 shadow forecasts saved after the experiment start provide prospective evidence.
Historical eventual starters cannot stand in for expected starters at T72/T60. QB research
using eventual starters is labeled retrospective and cannot qualify for promotion.

Shadow forecasts use separate private publication directories and the same durable kickoff
checks as production. Each retains its actual generation time, input captures, model/code
hashes and paired production reference. Independent scorecards select the latest valid
pregame shadow at each horizon and compare with that frozen reference on the same finalized
games. Missing-QB fallbacks remain explicit gaps rather than scored QB adjustments. Games
before the experiment start are excluded from shadow coverage, not backfilled. No automatic
promotion is enabled; rollback is the unchanged saved production Elo model.


### Measured probability experiments and first shadow activation

Configuration was frozen before fitting: development2018–2023, validation2024,
known benchmark2025, future2026 forecasts generated after the experiment start.
Historical final availability uses kickoff+24hours as an explicit research proxy;
it does not establish original publication time. Development results are fitting/
selection sample results. The known2025 results never select parameters or family.
All regular/postseason results update Elo; target metrics cover regular-season games.

At T60, the exact fixed production Elo formula and its selected sigmoid calibration:

| Period | Games | Production log loss | Calibrated log loss | Production Brier | Calibrated Brier |
|---|---:|---:|---:|---:|---:|
|2024 validation|272|0.619019|0.616397|0.426532|0.423060|
|2025 known benchmark|272|0.665142|0.659651|0.456275|0.451246|

Brier is the sum over all three outcomes. Winner accuracy excludes ties: known2025
production63.47%, calibrated64.21%. The paired2025 log-loss difference is−0.005491,
95% season/week block-bootstrap interval[−0.019781,+0.007184]; Brier difference−0.005029,
interval[−0.016119,+0.005295]. These intervals include zero: improvement is not established.
The27-setting development grid selected home45,K20,offseasonretention2/3; raw tuned
Elo has known2025 logloss0.660651. The production policy remains home65. Calibration
for tuned Elo was not selected on2024; a favorable2025 metric cannot override that decision.

The selected production-Elo conditional mapping is sigmoid(0.882501412638933×logit(q)
−0.15176353784172883), fit on1576 development non-ties. Original tie mass is preserved.
Artifactfa15c047… and report49831300… are private content-addressed files; exact original
sourceSHAa380fe56… and dependency versions are preserved in probability/source-versions.
`ops/season_probability.py evaluate --freeze <private-freeze.json>` is reproducible;
unchanged frozen input/config/source evaluations reuse an integrity-checked index.
Code changes produce distinct experiment identities. No model automatically promotes.

Actual first calibration shadow publication:2026-09-13T04:45:52Z,29upcoming games.
ATL@PIT productionPIT0.6395142531828811,ATL0.35650847408984615,tie0.003977272727272727;
shadowPIT0.5876441519299582,ATL0.40837857534276895,same tie. All573pre-existing production
files remained hash-identical. No shadow forecast was backfilled for already kicked-off games.
The calibration shadow is running locally with separate immutable histories and scorecards;
its finalized paired sample is currently zero. Historical evidence remains GradeC.

The EPA defect repair retains team plays independently of missing QB-only measurements.
All2593corrected rows now have nonzero rushingEPA differences and distinct offense/passEPA.
A separate immutable reconstruction produced265matched known2025 games: correctedEPA
logloss0.703729177,Brier0.491377680,accuracy55.68%; fitted Elo comparator0.661164337,
0.452788393,62.12%. The old defectiveEPA loss was0.694552582. Repairing the inputs did
not improve the model. This fitted-logistic comparator is not the exact production formula,
and its265-game sample must not be directly compared with the272-game experiment above.
Originaldataset/report/coverage remain unchanged. The improvement view marks the verified
bad dataset by content hash and loads corrected experiments separately. Corrected private
outputs are under research/repairs/epa-team-plays-v3/33c70c877…; no promotion is eligible.


QB residual research uses regular-season passing EPA per attempt, accumulated only from
finals available before each target cutoff. History begins2016, including warmup before
scored development games. Player estimates shrink toward the target team's historical
passing EPA reference; shrinkage strength is the median lagged attempt count in development.
The fitted coefficient adjusts the conditional home/away logit; the baseline tie mass stays
fixed. Tied games therefore contribute to probability scores but cannot fit that coefficient.
Team passing EPA is a proxy to reduce double-counting, not an exact QB decomposition of Elo.

Historical actual starter identities are explicitly retrospective GradeC research. They do
not establish what an expected-starter source said at T60. Original historical publication
receipts were not found in the inspected private archives. Current depth-chart publication
and capture times, official injury captures and available inactives are retained prospectively.
Missing identity/history, uncertain availability, stale state or failed sources cannot silently
become a normal QB shadow forecast. Rookies and limited-history QBs fail the frozen support
gates. Uncertain listed starters may have separate conditional scenarios from preserved depth
alternatives; no starter weights are invented, and those scenarios are excluded from scoring.
Expected starter OUT/inactive conflicts remain explicit. Saved feature values, source and
artifact hashes support reconstruction; current inputs never rewrite earlier explanations.

The superseded first QB fit allowed tied outcomes to alter its conditional coefficient. Its
private artifact840fc04…/reportc5f0cf… are retained for audit and must never be activated.
Corrected fitting has a separate freeze/artifact identity. No automatic promotion is permitted.


EPA failure audit: the current challenger selects exactly offense, defense, pass and rush
EPA differences (season_rebuild.py model indices11/14/17/20 in FEATURE_SCHEMA_V1).
It replaces the rating predictor; it is not an incremental Elo-plus-EPA model. It omits
explicit Elo difference and home/neutral columns. That omission is a future hypothesis,
not proof of why it lost. Opponent adjustment already exists: ridge fits use alpha10,
with policy-controlled prior blending and four-game EWMA support weights. The logistic
model standardizes estimator inputs and uses C1/max_iter2000; these policy defaults were
not optimized by this repair. Nonfinite selected features fail closed. Schema ordering
and target exclusion are explicit; historical prior games must precede the target date.
Identity calibration was selected for both corrected candidates by chronological folds.
Thus neither absent opponent adjustment nor an assumed calibration gain explains away
the observed loss. The data defect is established; remaining model weaknesses need a
new frozen development experiment and prospective evidence, not tuning on known2025.
The fitted Elo comparator also uses feature-policy Elo (initial1500/home0), unlike the
production policy (1505/home65), reinforcing why the separate exact-production replay is
required for the new probability comparisons.


Corrected QB experiment and matched comparison (same T60 games):

| Model | 2024 log loss (249 games) | Known2025 log loss (203 games) | Known2025 Brier | Accuracy (202 decisive games) |
|---|---:|---:|---:|---:|
| Exact production Elo | .609327 | .671820 | .458856 | 61.88% |
| Calibrated production Elo | .607556 | .667890 | .455012 | 63.86% |
| Tuned Elo | .603716 | .667252 | .454495 | 63.37% |
| Corrected QB residual | .644663 | .701457 | .483894 | 60.40% |

QB coverage is249/272 in2024 and203/272 in2025; exclusions remain recorded and are not
losses hidden from the comparison. The QB known2025 log-loss delta is+.029636,95% paired
season/week bootstrap interval[+.004604,+.052566]; Brier delta+.025038,[+.003165,+.045696].
Validation log-loss delta+.035337,[+.006997,+.064278]. These observed samples favor Elo.
Known2025 calibration intercept/slope: production−.158854/.921890, calibrated−.000336/1.044471,
tuned−.054618/.940572, QB−.051992/.607665. Bin counts and all paired intervals are saved in
matched comparison811801a6064ba095c3f7066f5ed0d047b6c411cdf511db319b291d067aff0fc2.
Calibration and tuned Elo intervals still include zero. These are inspected research periods,
not untouched tests. Future forecasts after the frozen experiment start remain prospective.

Corrected QB artifactedce0619… (fileSHAcdeab16a…) and reportff8c02be… use coefficient
5.989057953711544, fit on1393decisive development games; five development ties are excluded
from coefficient fitting, but retained in proper scoring. Total1850prepared rows include
validation and known-benchmark rows; they are not1850training games. The offline driver
`uv run python ops/season_qb_experiment.py --help` documents seven explicit private input
arguments. It archives exact source/config/raw/dependencies before fitting and validates
an immutable run index on reuse. Actual repeated invocation added/changed no files; all1850
probabilities exactly matched the corrected artifact. Private reproduction output is under
probability/qb/cli-reproducibility-v2; the active definition remains unchanged.

QB is SHADOW-RUNNING, not eligible for promotion. First10normal shadow forecasts published
2026-09-13T06:08:21Z; second real cycle retained20revisions on10games. The calibration shadow
has116revisions on29games. Both have zero finalized prospective outcomes at verification.
ARI@LAC production: home.7558747474462543/away.24014797982647298/tie.003977272727272727.
QB: home.7440243280415569/away.25199839923117023/same tie. Saved residuals LAC−.006081478698006432,
ARI+.004599561789204294 times coefficient yield logit change−.06396937048384442; applying
that to the saved baseline conditional logit reproduces the shadow exactly. Saved evidence
identifies Justin Herbert and Jacoby Brissett with source/capture times and all feature values.
ATL@PIT remains unconditionalQBfallback due to OUT conflict. A separate CooperRush/Rodgers
conditional estimate givesPIT.7829977687595693,ATL.21302495851315792,tie.003977272727272727.
It is unweighted/unscored; JackStrand is unavailable due to insufficient history. No claim
that either scenario is the expected starter distribution is made.

Live verification: http://127.0.0.1:8510/ and /games/2026_01_ARI_LAC plus /performance,
desktop1440/mobile390, realclick routes and conditional labels/probabilities; no overflow or
JavaScript errors. Screenshots are private/tmp/nfl-qb-shadow-{390,1440}.png and
/tmp/nfl-qb-conditional-{390,1440}.png. Source check06:27:09Z Sep13; next08:27:09Z
(03:27CDT); production lastsave05:47:53Z (00:47CDT), zerochanged probabilities/newrevisions
on that check. Worker reloaded; local Mac awake/online remains required. No cloud deployment
is claimed. Full V2 registry/reviewed champion and promotion-quality historical expected-QB
receipts remain absent. Missing2026finalized QB statistics block affected next-week team states;
missing week2injury reports and unsupported rookies block their QB forecasts, not production.


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

## Frozen prospective model comparison — September 23, 2026

This milestone reuses the existing shadow scheduler, immutable per-game forecast directories,
durable source receipts, paired production references and scorecards in `ops/season_shadow.py`.
There is no second forecast pipeline or result ledger. Production remains
`elo-season-v1`; its policy, probabilities and archived forecasts are unchanged.

The comparison contract is configured in `configs/season_live.toml` and written once as the
private `shadow/comparison-policy.json`. Its collection start is
`2026-09-24T00:11:30.920767Z`. Eligibility is limited to regular-season games with kickoff
after that instant. A future game's already-saved, valid, immutable forecast remains eligible;
games whose outcomes were already known at the freeze are excluded. The primary horizon is
T60 and the separately reported secondary horizon is T72, both using the existing ±10-minute
origin window. At each horizon, scoring selects the latest valid saved challenger forecast and
the production Elo reference embedded with that exact publication. No result can choose the
revision or comparison sample.

The latest observed official FINAL is the outcome; unresolved retractions remain pending.
Ties are excluded from winner accuracy and included in multinomial natural-log loss and the
three-outcome Brier score (sum of three squared outcome errors, range 0–2). Accuracy receives
a 95% Wilson interval and samples below 30 decisive games are labeled small. Operational
coverage reports all eligible games as forecasted, awaiting result, scheduled, due or missed,
with an explicit reason for each missed forecast. Performance is only the identical-game pair;
missing predictions remain visible in coverage and are not converted to losses or silently
dropped. Promotion is manual-review-only and is separate from display; weekly outcomes do not
retune or automatically promote a challenger.

Frozen challengers:

- `elo-calibration-fa15c047` uses the existing hash-bound calibration artifact and only the
  already-supported production Elo probability input. It is the currently operational
  challenger.
- `qb-residual-cdeab16a` uses the existing hash-bound QB artifact, runtime config, historical
  source and timestamped depth/injury/inactive receipts. It remains selected because the
  runtime path is implemented, but forecasts fail closed whenever current QB evidence or the
  nflverse state supplement is unavailable.
- The repaired EPA experiments remain research-only and are not selected. No supported live,
  timestamped EPA feature feed and runtime artifact exists for this scheduler; historical
  reconstructed features are not substituted for prospective inputs.

The `/performance` dashboard now shows a compact **Live model comparison** section separate
from official scoring and reconstructed research. It reports operational coverage, correct /
non-ties, Wilson uncertainty, challenger/Elo Brier and log loss on the same games, blocker
reasons and the frozen conventions. Game pages retain the detailed research-only shadow view.

Activation verification and the first live obligation audit are recorded below after the
committed worker reload. Existing archives must remain byte-identical; only new, valid future
publications may be appended. Missed windows are recorded and never backfilled.

### Activation and current obligations — September 23, 2026, 19:24 CDT

The existing LaunchAgent was reloaded at commit `da26275`, then the documented `--once`
refresh completed at `2026-09-24T00:24:36.517426Z`. The worker is HEALTHY, required sources
are FRESH, the next source check is `2026-09-24T02:24:36.517426Z`, and paid services remain
disabled. The immutable comparison policy was created with the exact contract above. Hash
verification found all 48,526 pre-existing production/shadow archive files unchanged; the
cycle appended 119 forecast receipts/evidence/policy records and one new valid production
coverage revision. It did not rewrite a completed-game forecast.

Both configured challengers are currently operational. Calibration has 8,860 immutable
revisions over 47 games; QB has 2,414 revisions over 42 games. The frozen comparison currently
has 216 eligible games with known future kickoffs; another 24 schedule entries in Weeks 16–18
have no kickoff timestamp yet and cannot have a cutoff obligation. No eligible outcome has
settled, so all accuracy, Brier and log-loss samples are N=0 and explicitly small.

At T60, both challengers are 0/216 forecasted, 216 scheduled and 0 missed because no eligible
T60 window has occurred. At T72, calibration is 1/216 forecasted with 215 scheduled and 0
missed. QB is 0/216 with 215 scheduled and one missed: ATL@GB has no valid saved QB forecast
with origin T72. Later UPDATE forecasts exist but are not relabeled or backfilled. The exact
per-game blocker at that expired window was not preserved in the latest view; period reports
show QB-state refresh failures, including network timeouts, but do not prove which failure
caused this specific miss. The dashboard therefore reports the narrower verified reason
`NO_SAVED_VALID_T72_SHADOW_FORECAST_BEFORE_CUTOFF`.

The next forecast obligations are nine Week 3 T72 games at the 17:00Z target on September 24
(allowed save window 16:50–17:10Z), followed by two games at the 20:05Z target. ATL@GB T60 is
targeted for 23:15Z (23:05–23:25Z). The daemon and worker-specific `caffeinate -i` assertion
are running. Collection is unattended while this Mac remains awake, logged in and online;
closed-lid sleep, shutdown and network/source outages remain external failure modes.

## Prospective delivery and displayed probabilities — September 23, 2026

Fresh audit time: `2026-09-24T02:18:42Z` (`21:18 CDT`). The worker heartbeat was HEALTHY at
`02:18:16Z`; the saved view's required sources were FRESH from `00:24:36Z`, with its next
deadline-aware run scheduled for `02:24:36Z`. No comparison result had settled.
The scheduled cycle then completed normally at `02:25:28.538750Z`: sources remained FRESH,
production reported zero changed probabilities, zero new forecast coverage and zero new saved
revisions, while `last_forecast_generation` correctly remained `00:24:36.512064Z`. The next
source check is `04:25:28.538750Z`; the next forecast cutoff remains the Week 3 T72 window at
`16:50–17:10Z`. Delivery counts below were unchanged by the refresh.

All frozen-comparison obligations with a known kickoff, classified at that audit time:

| Challenger | Horizon | Delivered closed windows | Missed closed windows | Blocked now | Not yet due | Due now |
|---|---:|---:|---:|---:|---:|---:|
| Elo calibration | T60 primary | 0 | 0 | 0 | 216 | 0 |
| Elo calibration | T72 secondary | 1 | 0 | 0 | 215 | 0 |
| QB residual | T60 primary | 0 | 0 | 0 | 216 | 0 |
| QB residual | T72 secondary | 0 | 1 | 0 | 215 | 0 |

The delivered calibration record for ATL@GB is immutable origin T72, generated
`2026-09-22T00:09:57.001356Z` and durably received at `00:09:57.002196Z`, inside its
`00:05–00:25Z` window. The QB T72 miss is not a scheduler miss: saved input snapshots at
`00:09:51Z` and `00:15:31Z` show expected QBs available, and the contemporaneous QB-state
artifact contains every required prior ATL/GB result. Both snapshots record injuries as
`MISSING / NO_OFFICIAL_INJURY_REPORT_FOR_GAME_WEEK`. The frozen QB guard requires an AVAILABLE
injury report and therefore returned `QB_INJURY_EVIDENCE_UNAVAILABLE`; fallback estimates are
not published as shadow forecasts. Later QB UPDATE records remain UPDATE and do not repair or
replace the missed T72 obligation.

### Which probability the UI displays

- The main card reads `scorecards.games[].prediction_id`, then displays that exact revision's
  `p_home`, `p_away` and `p_tie` from `game.predictions`. Before kickoff it is labeled **Latest
  pregame estimate · production Elo**. After kickoff the same selection is labeled **Official
  scored forecast · latest valid pregame production Elo**.
- The game-detail API independently resolves `official_prediction` from the same scorecard
  `prediction_id`; the detail card receives that exact record. Page visits never reconstruct a
  probability.
- The official scorecard chooses the latest valid production-role revision by durable
  publication time, restricted to the current schedule version and strictly before kickoff.
  Outcome contents cannot select the revision.
- T72, T60 and FINAL scorecards instead choose the latest valid revision whose `origin` exactly
  matches that horizon. A later UPDATE, FINAL or official revision does not rewrite an earlier
  horizon.

The card now shows **Forecast published** separately from **Latest source check** and states that
a newer check does not imply a changed probability. Research challenger tables are explicitly
labeled **Research shadow probabilities · not official**. Live comparison coverage uses separate
columns for future scheduled, due-now and missed/overdue obligations.

### Recalculation, publication and input use

The daemon polls its clock every 60 seconds. Normal source refresh is approximately every two
hours; within two hours of a game it checks every five minutes. Independently, `next_check`
inserts every future T72, T60 and FINAL target and selects the earliest deadline or cadence event.
T72/T60 accept publications from target−10 minutes through target+10 minutes. FINAL begins at
kickoff−5 minutes and closes at kickoff. The scheduler regression verifies a T72 target preempts
the two-hour source interval, while the five-minute near-game cadence reaches T60 even earlier.
External fetch latency or source failure can still consume a window; timestamp enforcement stays
fail-closed. No scheduler defect was reproduced, so scheduler code was not changed.

Each scheduled run refetches sources, rebuilds production Elo state from only available finalized
results, and considers games within the eight-day slate. It publishes ON_DEMAND for first
coverage, the named horizon while that window is due, or UPDATE when the saved input/model-state
fingerprint changes. A refresh may therefore save newer evidence while probabilities remain
identical.

- Production Elo probabilities use past finalized scores, team identity, offseason regression,
  home/neutral venue, fixed Elo policy and the historical tie rate. Expected QB, injuries,
  inactives and weather are collected but do not alter production probabilities.
- The calibration shadow maps production Elo's conditional home probability through its frozen
  sigmoid and preserves production tie mass. It does not use QB, injury or weather values.
- The QB shadow starts from the paired production probability, then uses timestamped expected-QB
  identity, injury/inactive availability guards, and frozen pre-cutoff player/team passing-EPA
  state. Weather does not affect it. Missing required evidence yields an unscored fallback.

At each named horizon the saved origin record becomes immutable when published and no later
origin can replace it. At kickoff all publication closes; official scoring remains fixed to the
latest valid pre-kickoff production revision, while pending outcomes can later settle or retract
without changing that forecast.

The LaunchAgent remains wrapped by `caffeinate -i`, and macOS reports an active
`PreventUserIdleSystemSleep` assertion. This supports unattended collection only while the Mac is
awake, logged in and online. It does not support closed-lid sleep, shutdown or offline operation.
