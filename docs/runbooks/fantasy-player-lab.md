# Player Lab runbook — milestones 1–4

Local preview: <http://127.0.0.1:8520/fantasy>, branch `codex/fantasy-player-lab`.
Historical WR/RB/TE usage only: targets, carries, receiving air yards, target/carry/snap
shares and verified red-zone opportunities. Three viewing windows, searchable and
sortable leaderboards, two-player comparisons, metric definitions, sample counts,
source coverage and timestamps. Weekly receiving projections are now experimental;
ESPN sync, rankings, trades, chat and
sports betting remain deferred. The September 30 closeout authorizes a selective
milestone commit and normal push to `origin/codex/fantasy-player-lab`. No merge or
deployment is authorized; the worktree and its unrelated edits are preserved.

## Milestone 4 — weekly receiving projections (experimental)

A separate **Upcoming receiving projections** sheet estimates WR/TE targets, catches
and receiving yards with nominal 80% ranges, opponents, kickoff, cutoff/horizon, samples,
source timestamps and unverified-availability assumptions. All positions' receiving
history, including RB/FB/QB, consumes allocation; team attempts and credited targets
are distinct, and unallocated targets remain explicit. It never uses future actual volume,
rosters or opportunities. Existing expected points remain retrospective and frozen.

The one fixed retrospective comparison failed criteria: target/catch RMSE were worse
than rolling averages and outcome coverage was only 6,565/9,102 (72.13%). Yard RMSE
improved 28.779→27.956. 2,537 missing rows stay DNP/no-stat/unknown, not zero. The feature
is experimental; no tuning or second test-set comparison followed. Priors: 2019–2022;
2023 calibrates intervals; 2024–2025 corrected files are already-inspected retrospective
benchmarks, with no historical publication-time proof. [Full protocol/results](fantasy-receiving.md).

273 WR/TE Week 4 predictions were genuinely archived before kickoff at
**2026-10-02 23:01:58.658 UTC**, with first-version bytes preserved in
[the receiving bundle](../../artifacts/fantasy/receiving/v1/README.md). Raw data, subsequent
working archives, local outcomes and browser calls remain excluded from Git.
Forecast generation is manual: `.venv-fantasy/bin/python -m fantasy.receiving_data`.
Reload reads saved archives only. The fantasy preview is still port 8520; only its
process restarted. Existing viewer allowlists now include the new script/API for a
future normal viewer restart; the running NFL session was not restarted.

**My prediction** saves browser-local ID/game versions before captured kickoff,
without overwriting older calls. Numeric outcomes later match by stable IDs; missing
outcomes remain unavailable. Device-clock timestamps are not trusted/tamper-proof,
changing origin loses access, and concurrent tab saves use last-write-wins. Corrupt,
blocked or full storage visibly refuses unsaved changes. Comparison now toggles window
totals/per-game counts; shares retain summed denominators and missing coverage stays null.

Validation: 92 focused Python checks, lint/syntax, new desktop/mobile projection/storage
checks, and existing fantasy UI checks. The frozen expected-points model/artifact bytes
and compatibility remain unchanged. Next bounded task: **grade first Week 4 prospective
forecasts on October 6 after numeric outcomes arrive**, by position/tier/horizon with
missing coverage and all versions preserved. No automated tuning, TD/full-point forecasts,
ESPN sync, rankings, trade advice, routes or betting.

## Frozen experiment preservation — October 1, 2026

The completed experiment is archived in the versioned, public-only
[bundle v1](../../artifacts/fantasy/expected-points/v1/README.md): original model,
freeze, validation and consumed-holdout report bytes, all 14 original historical
source receipts, scoring-policy copy, provenance manifest and SHA256SUMS (about 40 KB).
The manifest identifies experiment code commit
`148eb58e32ac2425f5d44b2b7974956fe8e2bfce` and exact method/config compatibility.
Sealed canonical-JSON fingerprints and raw file-byte hashes are labeled separately.
The bundle directory is excluded from automatic formatting to preserve evidence bytes.

```sh
.venv-fantasy/bin/python -m fantasy.restore
# Optional isolated destination; does not create or start a preview:
.venv-fantasy/bin/python -m fantasy.restore --cache-dir /tmp/fantasy-restored-v1
```

Restore checks every packaged byte and inventory, compatible source/config bytes,
and the existing API's model/validation seal and saved-report gates before any write.
It restores only `.fantasy-cache/expected-points/{model,freeze,validation,holdout}.json`;
identical files are left unchanged and differing/partial local evidence is refused.
No refit, holdout replay, model selection, bootstrap or acceptance-gate change is allowed.
The 2024–2025 holdout remains consumed. Source receipt hashes were checked against all
14 locally saved raw files without fetching or interpreting them again. Retrieval times
and HTTP modification times remain distinct; no source publication time is inferred.

Preservation validation: 19 focused restore/server checks passed, including exact bytes,
idempotence, API acceptance without a usage snapshot, corruption, incompatible method/
repository, divergent local evidence, complete receipt coverage and path traversal.
An isolated CLI restore also reproduced all four original file-byte hashes. No UI changed.
Raw current/historical datasets, current snapshot/refresh receipts, environments, caches,
logs, browser-local watchlists and private NFL archives remain outside the bundle/Git.
Public model integrity is restorable without those inputs; live usage display still
requires the separate local snapshot. Next work remains prospective descriptive
coverage/calibration monitoring with this model frozen, not a new historical experiment.

## Current milestone 3 — opportunity quality and supported points

The new **Opportunity quality & points** section supports every WR/RB/TE in the
existing catalog and the last-game, last-three and season windows. Select a player
and standard/half-PPR/PPR scoring. It shows separate targets and carries at or inside
the 20, 10 and 5, plus verified average target depth, explicit opportunity counts,
covered/expected games and exact game samples. Thresholds are nested, not additive.
Nullified/deleted plays and two-point attempts are excluded. Zero targets means
unavailable average depth, not zero depth; negative target depths are valid.

Supported points award 0.1 per rushing/receiving yard, 6 per rushing/receiving TD,
and 0 / 0.5 / 1 per reception. Turnovers, bonuses, passing, two-point conversions,
kicking, defense, returns and recovery TDs are excluded. These are component totals,
not complete league scores. Missing components stay unavailable.

Retrospective expected points passed the fixed historical gates and are shown alongside
actual points and **actual minus expected**. The simple field-position/target-depth
baseline was fitted only on 2019–2022, finalized on 2023 and evaluated once on the
untouched-for-this-method 2024–2025 holdout. There was no post-holdout tuning or refit.
The app includes method details and MAE/RMSE/signed bias tables by position/scoring;
[the frozen protocol and full report](fantasy-expected-points.md) document all seasons,
coverage, exclusions, hashes and uncertainty. This is descriptive opportunity valuation,
not a forecast, ranking, skill measure or buy/sell recommendation.

Holdout overall (baseline → context RMSE): standard **3.843 → 3.641**, half-PPR
**4.012 → 3.847**, PPR **4.217 → 4.082**. MAE also improved in all formats and every
position improved RMSE. Eligible: 5,131/5,138 validation rows, 5,175/5,200 in 2024 and
5,356/5,373 in 2025. The same general play/box-score reconciliation rule excludes
unsupported attribution across every season. All model comparisons use identical rows.

### Latest current-season capture and examples

2026 regular season still covers **Weeks 1–3, 48 completed games**, latest PHI–CHI on
September 28, final 7–27. Source retrieval: **2026-10-01 05:49:17.922–05:49:18.803 UTC**;
snapshot build: **05:49:19.056 UTC**. These are local capture/build times, not publication
times. Stats/PBP provider modifications remain September 30, snaps/IDs September 29;
schedule supplies no modification timestamp. Original receipts and bytes are retained.

There are 433 catalog players / 1,129 observations. All **966 box-score observations**
have verified zone counts and scoring components; **964** reconcile for expected points.
The **163 snap-only observations** retain unavailable usage, quality and actual points.
The same three Cody White snap-ID gaps remain; snaps are not used in this points model.

| Position in box-score rows | Quality/actual covered | Expected-point eligible | Excluded from expected points    |
| -------------------------- | ---------------------: | ----------------------: | -------------------------------- |
| WR                         |              457 / 457 |               455 / 457 | 2 outcome-attribution mismatches |
| RB                         |              274 / 274 |               274 / 274 | none                             |
| TE                         |              235 / 235 |               235 / 235 | none                             |

Examples from the complete Weeks 1–3 sample:

- Trey McBride: **8 red-zone targets, 6 inside-10 targets, 3 inside-5 targets**;
  182 air yards / 34 targets = **5.35-yard mean depth**. PPR actual **59.10**,
  expected **61.35**, difference **−2.25**.
- Bijan Robinson: **11 red-zone carries, 7 inside-10 carries, 5 inside-5 carries**;
  mean target depth **−1.75 yards**. PPR actual **77.70**, expected **66.59**,
  difference **+11.11**. This is descriptive, not evidence of a future reversal.
- Khalil Shakir (Week 1) and Deebo Samuel Sr. (Week 3) have lateral attribution
  mismatches. Their actual box-score points and verified quality remain available;
  three-game expected points and differences are unavailable with **2/3** coverage.
  The code has no named-player exception; it compares all five scoring components.

### Validation and operation

67 focused Python checks, Ruff and JavaScript syntax passed. Both browser suites passed
at 1440px/390px: all positions and scoring formats, exact quality/points, sample changes,
real attribution and missing-data cases, research-only gate behavior, HTTP failure recovery,
and the existing trends/watchlist checks. No JS errors or page overflow. Screenshots are
in `/tmp/fantasy-quality-*.png`; the mobile quality table wraps samples within the screen.
Tests were run red before implementation, including missing modules/API fields and the
new UI selector, then green after implementation. Initial lint found one unused import,
which was removed before evaluation was sealed.

```sh
.venv-fantasy/bin/python -m pytest tests/test_fantasy*.py tests/test_week1_viewer.py -q -o cache_dir=.fantasy-cache/pytest
.venv-fantasy/bin/ruff check --no-cache fantasy tests/test_fantasy*.py
node --check ops/viewer/fantasy.js
NODE_PATH=/Users/supashramesha/.agents/skills/gstack/node_modules node tests/fantasy_quality_ui.cjs
NODE_PATH=/Users/supashramesha/.agents/skills/gstack/node_modules node tests/fantasy_ui.cjs
```

Source refresh remains manual. Historical inputs and fitted artifacts live only in the
ignored fantasy cache. A fresh checkout can build them with the validate/holdout commands
in the evaluation report, then refresh sources; neither command starts a worker. The API
withholds expected points if artifacts are absent, inconsistent with the sealed method,
or fail the fixed gates. The existing NFL processes, configuration, models and archives
remain untouched. Only the dedicated fantasy preview was restarted.

Next bounded task: prospective descriptive monitoring of opportunity-quality coverage
and expected-point calibration on newly completed games, with this model frozen and
no automatic promotion/refit. The 2024–2025 holdout is now consumed. ESPN, rankings,
projections, routes and betting remain deferred; no merge or deployment is authorized.

## Milestone 2 — weekly usage and local watchlist

TEs now participate in all summary windows, position filters and two-player comparisons.
Weekly trends show every NFL week through the latest represented completed week,
with exact dates/opponents, observed/expected games and metric-specific coverage.
Each target/carry/snap share uses summed player counts / summed matching team counts;
both counts are visible. A change is **week W minus week W−1 in percentage points**.
These calendar-week periods share no games and are independent of the summary window.
The optional disjoint three-game change remains deferred.

Week 1 has no baseline. Byes, missing observations, unfinished scheduled games,
missing paired counts, zero denominators and unresolved team changes withhold the
affected delta. A gap never borrows an older appearance. A week without a captured game
is labeled “No scheduled game (bye or schedule gap)”: schedule absence alone does not establish
the reason. A published zero with a positive denominator is still a valid zero share.
For legacy snapshots without a schedule, trends stay unavailable until manual refresh.

Watch/Unwatch controls and the saved list persist only in localStorage for this browser
and exact origin (`http://127.0.0.1:8520`). Stored membership is versioned GSIS player IDs,
not names, at `nfl-player-lab.watchlist.v1`. Reloads retain it; other tabs at the same
origin update when storage changes. There is no server write, account or device sync.
Changing host/port or clearing browser data changes/removes this local list. Missing
catalog members remain removable by ID. Invalid storage is preserved until explicit
Clear; blocked/full storage shows failure and does not claim the attempted change saved.
Simultaneous edits in separate tabs use the browser's last completed write; no transactional
multi-tab editing guarantee is claimed.

### Current data audit

Manual source retrieval: **2026-09-30 23:25:12.366–23:25:13.202 UTC**
(18:25:12–13 CDT); snapshot built **23:25:13.354 UTC**. Five raw source hashes
match the prior milestone's capture. The schedule file changed only in unused market
columns on 16 future-game rows; IDs, dates, weeks, teams and results are unchanged.
All six current files match their saved receipt hashes. HTTP provider modification timestamps
remain those listed in the historical table below; they are not exact publication
times. Retrieval time is not evidence that a newer game or provider revision exists.

Loaded **2026 regular season, Weeks 1–3, 48 completed games**, latest **PHI at CHI,
September 28, final 7–27**, now represented by 23 WR/RB/TE observations. The captured
schedule contains 272 regular-season games. There are **433 distinct players and
1,129 player-game observations**. All counts describe source coverage, not full rosters.

| Source-row position | Distinct IDs | Observations | Missing targets/carries/air yards/red-zone, each | Missing snaps/team snaps, each |
| ------------------- | -----------: | -----------: | -----------------------------------------------: | -----------------------------: |
| WR                  |          197 |          504 |                                               47 |                              3 |
| RB                  |          116 |          297 |                                               23 |                              0 |
| TE                  |          121 |          328 |                                               93 |                              0 |

TE audit before enabling support: **235 box-score rows, 103 GSIS IDs**, all with
targets/carries/air yards and all 235 matched to snaps by the exact ID bridge.
The normalized TE rows add **93 snap-only observations** with unknown box-score usage;
red-zone opportunities also stay unavailable. All 328 TE-labeled observations have
verified offensive snap denominators. The current TE filter contains **120 players**:
Jackson Meeks has TE snap-only labels in Weeks 1–2 and a WR stats label in Week 3.
The catalog consistently uses latest observed position, so he is listed once as WR.
Per-position source coverage is not additive for distinct IDs and is labeled in the UI.

| Latest-position catalog | Last-game complete usage / players | Last-three or season complete usage / players | Last-three or season complete snap share / players |
| ----------------------- | ---------------------------------: | --------------------------------------------: | -------------------------------------------------: |
| WR                      |                          154 / 197 |                                     119 / 197 |                                          142 / 197 |
| RB                      |                           90 / 116 |                                      66 / 116 |                                           77 / 116 |
| TE                      |                           82 / 120 |                                      51 / 120 |                                           96 / 120 |

“Usage” here covers targets, carries, air yards, verified red-zone opportunities and
target/carry shares, whose complete-player counts coincide in this snapshot. Across
all positions, 163 observed rows lack box-score usage; three rows lack snaps (the same
Cody White ID-bridge gap). Entirely absent observations are additional coverage gaps.
The original 70 WR/RB snap-only rows retain their unavailable usage.

Real examples, independently checked against raw player/team files:

- Trey McBride: target share **10/26 = 38.5%** in Week 2, **11/50 = 22.0%** in
  Week 3, **−16.5 pp**; snap share **44/50 = 88.0%** to **79/87 = 90.8%**, **+2.8 pp**.
- Brock Bowers: Week 3 targets **13/30 = 43.3%**, but no Week 2 player observation;
  the Week 3 change remains unavailable. Missing Weeks 1–2 are not asserted to be injuries.

### Milestone 2 verification and roadmap

**53 focused Python tests passed**, including TE ingestion, legacy snapshots, summed
multi-game denominators, disjoint weeks, byes, missed/unfinished games, early history,
nulls, legitimate zeros, zero denominators, duplicates, transfers and API window
independence. Ruff and JavaScript syntax checks passed. Browser checks passed at
1440px and 390px with no page overflow or JS errors: TE filters/comparisons, exact weekly
coverage and pp values, missing-week rendering, watchlist reload/cross-tab/mobile add/remove,
unavailable IDs, corrupt/blocked/full storage, and the existing source-failure cases.
Screenshots: `/tmp/fantasy-trends-{desktop,mobile}.png`.

The initial browser run failed while UI wiring was incomplete; the subsequent cross-tab
test stopped with `Error: Please use browser.newContext()`. The harness now creates an
explicit shared context; the complete final suite passed. The orphaned fantasy preview
was restarted on its own port after the tool daemon interruption. The NFL worker/viewer,
configuration, models and forecast archives were not restarted or modified.

Next bounded build: **audit opportunity-quality inputs and implement evaluated,
scoring-specific retrospective expected points**, with explicit scoring settings,
chronological evaluation, sample coverage and a clear separation from projections.
ESPN context, waivers/trades and evaluated weekly/rest-of-season projections remain later.
Routes and dated roster histories remain unavailable. No recommendations, projections,
ESPN sync, betting, merging or deployment are part of this milestone.

## Isolated operation

Run from `/Users/supashramesha/Desktop/nfl-fantasy`:

```sh
uv venv .venv-fantasy --python 3.11
uv pip install --cache-dir .fantasy-cache/uv --python .venv-fantasy/bin/python -r fantasy/requirements.txt
.venv-fantasy/bin/python -m fantasy.sources
.venv-fantasy/bin/python ops/week1_viewer.py --fantasy
```

The last command enters the fantasy server before importing any forecast configuration.
It binds `127.0.0.1:8520`; it neither starts nor restarts a forecast worker. The page
and API also have integration routes in the existing viewer. The fantasy preview's
NFL link points to the existing viewer on port 8510, configured in `configs/fantasy.toml`.
That viewer was not restarted, so its running navigation need not yet show Player Lab.

All settings and URLs are in `configs/fantasy.toml`. `FANTASY_CONFIG` may select another
TOML file; its cache must resolve inside this worktree and bind must be loopback.
No dependencies are installed into the NFL environment. `.venv-fantasy/` and
`.fantasy-cache/` are ignored. Raw files are content-addressed and receipts retain
SHA-256, source URL, capture time and HTTP Last-Modified. `snapshot.json` and
`refresh.json` are atomically replaced. No private NFL archive is read or written.

Source refresh is manual. “Reload saved data” reads the snapshot, without downloading
providers. A required-source failure preserves the previous snapshot and records the
failed attempt. An optional-source failure produces unavailable affected metrics;
old bytes are not silently reused. Snapshot age over the configured 24 hours is
labeled stale. Snapshot freshness measures local capture age, not provider latency.

## Calculation contract

- Last game: latest observed team's most recent eligible completed game.
- Last three: that team's latest up-to-three eligible completed games.
- Season: all that team's eligible completed regular-season games.
- Byes do not consume a game. Missed games are not replaced by earlier appearances.
  Missing rows cannot distinguish inactivity/injury from missing provider data and
  are never zero-filled. Published numeric zeros remain zeros.
- Full-window totals and shares require complete metric coverage. Each cell shows
  covered / expected games; an incomplete result stays unavailable. Short early-season
  history uses its actual sample. Comparison samples and weeks are shown separately.
- Target share is summed player targets / summed team targets. Carry share uses all
  team carries, including QBs. Multi-game shares never average weekly percentages.
- Snap share uses summed offensive snaps / summed team offensive snaps. PFR IDs join
  through a unique GSIS↔PFR mapping. A team denominator is accepted only if a single
  integer agrees with every team's player count and its rounded two-decimal PFR
  fraction. Ambiguous denominators/IDs remain unavailable. Snaps are never routes.
- Air yards are credited receiving air yards on targets, including incompletions;
  negative values remain valid.
- Red-zone opportunities are credited targets + carries starting at or inside the
  opponent's 20. Deleted/no-play/two-point plays are excluded. Complete PBP must have
  `END GAME`, unique play IDs, known opportunity yardlines, and player target/carry
  counts exactly matching the box-score row. Only then can a zero be established.
- The schedule lacks an explicit final flag. Only scored games dated before the
  current UTC date are eligible; same-day/live games are excluded. This is a daily
  historical usage view, not a live score feed.
- Team is latest observed team, not a verified current roster. Detected transfers
  withhold full-season values without dated roster history; short windows use the
  latest observed team. Players absent from both stats and matched snap records are
  outside the catalog. No transfer is present in the audited WR/RB box-score sample.

## Historical milestone 1 source audit

Snapshot built **2026-09-30 18:39:22 UTC (13:39:22 CDT)**. Actual retrieval/capture
times span **18:39:20.125–18:39:22.566 UTC (13:39:20–22 CDT)** that day. These are
local retrieval times, not source publication times. The HTTP Last-Modified values
below describe provider file modification; an exact publication timestamp is not
supplied. Current-season files were downloaded directly; no prior-season or fixture
fallback is used. Closeout inspected these saved bytes without refreshing them.

Latest represented completed game: **2026_03_PHI_CHI**, Philadelphia at Chicago,
September 28, 2026, final **PHI 7–CHI 27**. The snapshot has 17 WR/RB observations
for that game. The source schedule plus completed PBP establish the represented game;
the snapshot build time does not imply any later game is included.

| Source                | Captured rows | Provider file modification (UTC) |
| --------------------- | ------------: | -------------------------------- |
| Weekly player stats   |         3,339 | Sep 30 16:25:46                  |
| Weekly team stats     |            96 | Sep 30 16:25:48                  |
| Play-by-play          |         8,311 | Sep 30 16:15:15                  |
| PFR snap counts       |         4,488 | Sep 29 11:01:26                  |
| Player ID registry    |        24,834 | Sep 29 14:32:37                  |
| Schedule, all seasons |         7,548 | Not supplied                     |

Normalized coverage: **48 completed games, Weeks 1–3; 313 WR/RB players; 801
player-game observations**, against 272 scheduled regular-season games. There are
731 WR/RB box-score rows with targets/carries/air yards populated and 70 additional
snap-only observations. Those 70 keep box-score usage and red-zone counts unavailable.
Three rows lack matched snap evidence, all Cody White (`00-0035891`), Weeks 1–3.
The registry's PFR ID for him is blank. Snap records named Cody White exist under
`WhitCo05` (11, 7 and 15 offensive snaps), but no verified ID bridge connects them.
The app correctly declines a name-only match and shows snap share unavailable.
The 70 snap-only observations span 54 players; they are additional player-game
observations, not 70 different players. Their verified snaps can be shown while
targets/carries/air yards/red-zone opportunities and related shares remain null.
Snapshot null rates: targets/carries/air yards/red-zone 70/801 (8.74%) each;
snaps/team snap denominators 3/801 (0.37%) each. These rates exclude entirely absent
player-game observations. Full three-game coverage: 185 players for box-score and
red-zone metrics; 218 for snap share. Other players remain visible with coverage gaps.

| Metric family                                       | Populated player-game observations | Complete last-game players | Complete last-three / season players |
| --------------------------------------------------- | ---------------------------------: | -------------------------: | -----------------------------------: |
| Targets, carries, air yards, red-zone opportunities |                     731 / 801 each |             244 / 313 each |                       185 / 313 each |
| Target share and carry share (paired counts)        |                     731 / 801 each |             244 / 313 each |                       185 / 313 each |
| Snap share (paired counts)                          |                          798 / 801 |                  268 / 313 |                            218 / 313 |

Last-three and season happen to cover the same games at this three-week snapshot;
they are not independent comparison periods. Neither is treated as a change baseline.

Routes and route-based metrics are unavailable. Exact game-end-to-publication latency
cannot be inferred from HTTP file modification/capture timestamps; no such latency
is claimed. Provider schedules describe intended cadence, not guaranteed availability.
The older `player_stats/player_stats_2026.csv` URL returned HTTP 404; the current
`stats_player/stats_player_week_2026.parquet` asset is used instead.

Definitions and schedules were checked against the provider's
[player stats documentation](https://nflreadr.nflverse.com/reference/load_player_stats.html),
[snap dictionary](https://nflreadr.nflverse.com/articles/dictionary_snap_counts.html),
[PBP dictionary](https://nflreadr.nflverse.com/articles/dictionary_pbp.html), and
[availability schedule](https://nflreadr.nflverse.com/articles/nflverse_data_schedule.html).
Source URLs and receipts are also visible in the app.

## Validation

```sh
.venv-fantasy/bin/python -m pytest tests/test_fantasy_trends.py tests/test_fantasy_usage.py tests/test_fantasy_sources.py tests/test_fantasy_server.py tests/test_week1_viewer.py -q -o cache_dir=.fantasy-cache/pytest
.venv-fantasy/bin/ruff check --no-cache fantasy tests/test_fantasy*.py
node --check ops/viewer/fantasy.js
NODE_PATH=/Users/supashramesha/.agents/skills/gstack/node_modules node tests/fantasy_ui.cjs
git diff --check
```

Milestone 1 validation: 37 focused Python tests passed, including the existing viewer checks. Browser checks
passed at 1440px and 390px: WR/RB filters, ordering/search, all windows, two-player
selection and clearing, duplicate-player prevention, null/stale/failed/empty source
responses, HTML escaping, failed-window recovery, no page overflow and zero JS errors.
Playwright is used from the existing local browser tooling; set `NODE_PATH` to another
installation if needed. Synthetic responses are injected only into the test browser,
never persisted as live data. Screenshots are under `/tmp/fantasy-player-lab-*.png`.

Independent raw-file arithmetic checked Chris Olave targets **36/124 = 29.032%** and
Bijan Robinson carries **66/104 = 63.462%**, matching the app. Parser failure during
initial real ingestion was traced to three non-player records with null IDs; filtering
to WR/RB before identity validation fixed it after a failing regression. Browser
regression reproduced stale data reappearing after a failed window request; clearing
the old snapshot at request start fixed it. Both regressions now pass.

Initial network/server/browser runs were denied by sandbox permissions and were
rerun with approved access. PyArrow emitted nonfatal sandbox CPU-info warnings during
read-only audits. No full forecasting suite, worker cycle, model fitting or production
restart was run; focused checks are the validation boundary for this milestone.

## Historical milestone 1 September 30 closeout

Revalidated the healthy preview, all six saved source hashes, actual retrieval times,
latest represented game and the complete missing-data sets. The 37 focused Python
tests, lint, JavaScript syntax and existing desktop/mobile browser suite passed again.
A live browser audit additionally verified **all 313 rows and 863 unavailable metric
cells**, including coverage labels and Cody White/Brenen Thompson in comparison.
No missing value was rendered as zero and no code fix was necessary in this closeout.
No data refresh, forecast worker, forecast configuration/model/archive edit or restart
was performed. The previously running preview was reused.

The roadmap in `tasks/todo.md` now specifies weekly usage trends, TE support and a local
watchlist next; disjoint weekly/three-game comparison periods with explicit samples,
coverage and summed share denominators; opportunity quality and scoring-specific
expected points then; and ESPN context, waivers/trades and evaluated weekly/rest-of-season
projections later. None of those future features is implemented here.

Remaining data limitations (not closeout blockers): missing usage in snap-only records,
the Cody White ID bridge, routes, dated roster history and exact provider publication
latency. Any agent stop-hook warning is separate tooling status; no stop-hook failure
was observed in the validation commands and it is not evidence of a Player Lab failure.

### Unrelated edits preserved outside the milestone commit

Read-only formatting normalization found the following 21 whole-file diffs equivalent
to HEAD. JSON files were also compared as parsed values. They remain unstaged:

```text
.github/workflows/nfl-v2-dispatch.yml
.vscode/settings.json
CLAUDE.md
README.md
deploy/private-data-repo/.github/workflows/capture-and-forecast.yml
deploy/private-data-repo/.github/workflows/private-due-check.yml
deploy/private-data-repo/.github/workflows/settle-and-report.yml
deploy/private-data-repo/config/dispatch-windows-2026.json
deploy/schedules/dispatch-windows-2026.json
docs/runbooks/2026-09-15-week1-audit.md
docs/runbooks/2026-09-16-cutoff-native.md
docs/runbooks/2026-09-16-elo-epa-residual.md
docs/runbooks/2026-09-16-football-insights.md
docs/runbooks/season-live.md
docs/superpowers/plans/2026-09-10-season-forecasting.md
features_schema.json
ops/viewer/app.js
ops/viewer/insights.js
ops/viewer/styles.css
tasks/lessons.md
tests/fixtures/odds/nfl_h2h.json
```

Two mixed files retain formatting-only remainders: `ops/viewer/index.html` and
`tasks/todo.md`. Only the Player Lab navigation link and fantasy plan/roadmap section
are selected into the index, reconstructed on their HEAD versions. Working copies
retain all formatting. Total expected uncommitted tracked files after closeout: **23**.
Environment, source bytes, receipts, cache, screenshots and local data are excluded.
