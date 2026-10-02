# Weekly receiving projections — v1 protocol

Approved bounded milestone October 2, 2026. This protocol/config is recorded before
the new receiving model's benchmark results are computed. The frozen retrospective
expected-points experiment is separate and is not refitted or reevaluated.

One method: last four prior completed same-season team games; shrink team pass attempts
and credited target volume toward 2019–2022 team-game means (three prior games).
Shrink each pass catcher's target share toward its historical position mean (40 team
targets), catch probability (20 targets), and receiving yards per catch (20 receptions).
Fit priors only on 2019–2022. Include all receiving positions in allocation. Forecast
WR/TE candidates from earlier observed games, on their last observed team, with at least
one valid receiving row in the team's lookback. Do not inspect the prediction game's
roster, outcomes, target volume or opportunities to choose its candidates or inputs.
Missing prior rows are unknown, not zeros: shares use matching known team denominators;
their covered/expected history is shown. Cap summed allocations at observed historical
target coverage (and at one), retaining the remainder rather than scaling shares upward.
Receptions <= targets; target volume <= attempts; no TD/fantasy-point/injury predictions.

For historical forecasts use 24 hours before captured kickoff and only games on earlier
Eastern calendar dates than the cutoff (conservative completion rule; no same-day/live inputs).
Eastern schedule gametime is converted with America/New_York including DST. Missing
kickoffs or incomplete/duplicate/invalid team data cannot create a supported forecast.
No cross-season player role carryover, rookies without observations or future roster
assumptions. Missing outcomes are excluded from errors and reported, never imputed zero.
Explicit numeric zero rows are graded. No authoritative inactive/DNP data is available:
no outcome row means DNP/no-stat/unknown, not a confirmed DNP. This is an observed-outcome
benchmark and cannot establish unconditional availability-aware accuracy.

2023 is calibration only: fixed nominal 80% marginal intervals from absolute errors,
finite-sample rank ceil((n+1)*0.8), separately for model and rolling baseline by position
and pre-cutoff usage tier. Sparse (<100) cells fall back to position then overall.
Counts have lower bound zero; yard ranges may include negative values; catch upper
bound is capped at target upper bound. Intervals are marginal, not joint guarantees.
Tiers use the shared prior observed-game rolling target average: low <3, medium [3,6),
high >=6. Baseline averages targets/receptions/yards over the same valid prior rows.
Compare both on identical forecast candidates and identical numeric outcome rows.

Run exactly one benchmark on 2024–2025, with no test-set selection/refit/tuning.
All these seasons have already been inspected for other work. Corrected historical
files and today's schedule reconstruct event chronology, but lack historical publication
receipts. They are RETROSPECTIVE BENCHMARKS, not proven as-of-cutoff or untouched evidence.
2026 is excluded from priors and interval calibration. Genuine prospective evidence
begins with archives whose generation and source captures precede captured kickoff.

Fixed criteria (all must pass to remove the experimental label): >=80% numeric outcome
coverage in each benchmark season/position; model pooled RMSE no higher than rolling
baseline for each of targets/receptions/yards; position RMSE and pooled MAE <=1.05 times
baseline for each metric; nominal 80% interval coverage between 70% and 90% overall and
by position for each metric. Report errors, signed bias (prediction minus actual),
coverage and mean interval width overall, by position and shared pre-cutoff tier, even
when gates fail. Failure means experimental, with no repeated benchmark tuning.

Sources: [weekly player stats](https://nflreadr.nflverse.com/reference/load_player_stats.html),
[weekly team stats](https://nflreadr.nflverse.com/reference/load_team_stats.html),
[schedule dictionary](https://nflreadr.nflverse.com/articles/dictionary_schedules.html).
Retrieval and provider modification times are retained separately; neither establishes
an exact historical publication time. Only schedule identity/time fields are used,
never actual QB, game score, weather or market columns as forecast inputs.

Prospective archives use actual generation cutoffs, with hours before kickoff shown.
Their horizons can differ from the benchmark T24 cutoff; do not pool them as identical
horizon evidence. The immutable write time must also precede every included kickoff.

Source joins normalize OAK→LV and LAR→LA consistently in stats/schedule; original game
IDs are retained. This follows [nflverse team standardization](https://nflreadr.nflverse.com/reference/clean_team_abbrs.html).
Other scheduled-team contradictions fail closed; aliases do not represent future roster information.

## Completed one-time benchmark and decision

Protocol/code/config sealed **2026-10-02 23:00:19.030 UTC**, calibrated model saved
before the benchmark. Comparison completed once at **23:00:25.752 UTC**. Fixed criteria
failed; ship **experimental**. No post-result tuning or refit. Targets/catches got worse
than rolling averages; yards improved modestly, with systematic underprediction.
All intervals below are conditional on published numeric outcomes, not absent/DNP rows.

| Group       | Numeric outcomes / forecasts | Missing outcome rows |
| ----------- | ---------------------------: | -------------------: |
| ALL         |         6565 / 9102 (72.13%) |                 2537 |
| WR          |         4375 / 5897 (74.19%) |                 1522 |
| TE          |         2190 / 3205 (68.33%) |                 1015 |
| tier/low    |         2973 / 4862 (61.15%) |                 1889 |
| tier/medium |         2059 / 2494 (82.56%) |                  435 |
| tier/high   |         1533 / 1746 (87.80%) |                  213 |
| 2024/WR     |         2147 / 2908 (73.83%) |                  761 |
| 2024/TE     |         1070 / 1562 (68.50%) |                  492 |
| 2025/WR     |         2228 / 2989 (74.54%) |                  761 |
| 2025/TE     |         1120 / 1643 (68.17%) |                  523 |

2,537 absent outcomes are DNP/no-stat/unknown; confirmed DNP counts are **unavailable**,
not zero. There were zero malformed observed rows in this sample. Opening-week forecasts
for 32 teams per season were unsupported because same-season history was absent.
New/no-prior players are outside the candidate population, beyond the coverage table.
This observed-outcome selection is especially severe in the low tier (61.15% coverage).

| Group       | Metric          | Model MAE / RMSE / bias   | Rolling MAE / RMSE / bias | Model 80% coverage / mean width | Rolling 80% coverage / mean width |
| ----------- | --------------- | ------------------------- | ------------------------- | ------------------------------- | --------------------------------- |
| ALL         | targets         | 1.907 / 2.647 / -0.666    | 1.830 / 2.549 / -0.093    | 79.45% / 5.540                  | 81.46% / 5.331                    |
| ALL         | receptions      | 1.432 / 1.992 / -0.458    | 1.405 / 1.960 / -0.068    | 80.75% / 4.028                  | 82.86% / 4.157                    |
| ALL         | receiving_yards | 19.622 / 27.956 / -4.970  | 20.077 / 28.779 / -0.730  | 80.26% / 56.518                 | 80.53% / 64.303                   |
| WR          | targets         | 2.028 / 2.772 / -0.677    | 1.939 / 2.678 / -0.069    | 79.86% / 5.966                  | 82.10% / 5.751                    |
| WR          | receptions      | 1.471 / 2.018 / -0.417    | 1.428 / 1.983 / -0.047    | 81.21% / 4.207                  | 83.68% / 4.310                    |
| WR          | receiving_yards | 21.508 / 30.194 / -5.105  | 21.892 / 31.107 / -0.554  | 79.82% / 61.538                 | 80.41% / 70.789                   |
| TE          | targets         | 1.665 / 2.377 / -0.645    | 1.613 / 2.269 / -0.142    | 78.63% / 4.688                  | 80.18% / 4.490                    |
| TE          | receptions      | 1.353 / 1.939 / -0.539    | 1.359 / 1.913 / -0.110    | 79.82% / 3.668                  | 81.23% / 3.852                    |
| TE          | receiving_yards | 15.855 / 22.836 / -4.700  | 16.452 / 23.446 / -1.081  | 81.14% / 46.489                 | 80.78% / 51.347                   |
| tier/low    | targets         | 1.295 / 1.751 / 0.023     | 1.143 / 1.764 / -0.499    | 79.82% / 3.403                  | 83.42% / 3.005                    |
| tier/low    | receptions      | 0.973 / 1.330 / 0.006     | 0.902 / 1.372 / -0.324    | 80.66% / 2.476                  | 83.59% / 2.270                    |
| tier/low    | receiving_yards | 13.047 / 17.754 / 1.199   | 11.897 / 18.832 / -3.450  | 80.73% / 33.850                 | 79.52% / 37.520                   |
| tier/medium | targets         | 2.131 / 2.806 / -1.015    | 2.077 / 2.634 / -0.185    | 79.02% / 6.240                  | 80.09% / 6.000                    |
| tier/medium | receptions      | 1.596 / 2.106 / -0.676    | 1.582 / 2.045 / -0.118    | 81.06% / 4.514                  | 82.32% / 4.877                    |
| tier/medium | receiving_yards | 21.900 / 29.968 / -7.931  | 23.392 / 30.818 / -1.513  | 78.39% / 60.273                 | 79.89% / 70.840                   |
| tier/high   | targets         | 2.793 / 3.671 / -1.535    | 2.830 / 3.532 / 0.817     | 79.32% / 8.743                  | 79.52% / 8.941                    |
| tier/high   | receptions      | 2.100 / 2.757 / -1.064    | 2.145 / 2.680 / 0.495     | 80.50% / 6.385                  | 82.19% / 6.849                    |
| tier/high   | receiving_yards | 29.314 / 39.107 / -12.955 | 31.490 / 39.793 / 5.598   | 81.87% / 95.434                 | 83.37% / 107.467                  |

Errors/widths are in targets, catches or receiving yards per player-game; signed bias
is predicted minus observed. Every comparison has identical scored rows. Full position×tier
and season×position results, source receipts and missing counts are in the versioned
[bundle](../../artifacts/fantasy/receiving/v1/README.md), also exposed in the sheet.
The bundled protocol.md preserves the original pre-result protocol hash; this runbook
now adds the results. The calibrated priors/radii did not change after benchmark.

## First genuinely prospective version

Archived **2026-10-02 23:01:58.658 UTC**, input/prediction cutoff **23:01:58.627 UTC**;
273 WR/TE rows for remaining Week 4 games, all before captured kickoff. Public stats
include Weeks 1–3 plus completed Thursday Week 4 PIT–CLE; that already-kicked game is
excluded from upcoming forecasts. Current receiving files contain 98 team rows (49
games). The separate usage snapshot remains at its original Weeks 1–3 capture; it is
not silently refreshed. Source retrievals: players **23:01:58.029**, teams **.353**,
schedule **.609 UTC**; HTTP modification times: players **15:48:18**, teams **15:48:21
UTC October 2**, schedule unavailable. None is an exact source publication timestamp.

Examples from the sealed archive (point estimate; nominal 80% marginal range):

- Trey McBride, ARI vs NYG, kickoff 2026-10-04T17:00:00+00:00: targets 7.74 (4.00–11.49); receptions 5.69 (2.53–8.85); receiving_yards 52.52 (13.32–91.72). Prior observed/expected games: 3/3; horizon 42.0 hours.
- Justin Jefferson, MIN vs MIA, kickoff 2026-10-04T20:05:00+00:00: targets 4.92 (1.73–8.11); receptions 3.41 (1.10–5.73); receiving_yards 44.77 (14.67–74.86). Prior observed/expected games: 3/3; horizon 45.1 hours.
- Brock Bowers, LV vs KC, kickoff 2026-10-04T20:25:00+00:00: targets 5.98 (2.24–9.72); receptions 4.28 (1.12–7.44); receiving_yards 47.10 (7.90–86.30). Prior observed/expected games: 1/3; horizon 45.4 hours.

Bowers' missing Weeks 1–2 stay unknown; the estimate assumes observed participation
continues, without asserting injury recovery or a current role. All-position allocations
include RB/FB/QB/other receiving history, with explicit remainder. First archive bytes
are preserved in Git as first-prospective.json; subsequent local versions need backups.

## Operation, personal calls and next grading checkpoint

Manual forecast/source capture: `.venv-fantasy/bin/python -m fantasy.receiving_data`.
Use `--reuse-saved` only to retain explicitly old source timestamps. UI/API reload reads
saved files, never generates forecasts or starts a worker. Different method/config is
refused; the sealed receiving evaluator refuses a second comparison. Raw receiving
inputs and local forecast working copies are under ignored `.fantasy-cache/receiving/`.

My prediction stores append-only stable-player-ID/game-ID versions at browser key
`nfl-player-lab.predictions.v1`. Each save records device timestamp, captured kickoff,
revision, target/catch/yard values and archive time; earlier versions remain visible.
Catches cannot exceed targets. Late calls are refused against captured kickoff.
Browser storage has no trusted-clock/tamper-proof guarantee, account or device sync;
concurrent tab writes are last-write-wins. Corrupt/blocked/full storage must not claim
success or overwrite unreadable earlier calls. Later source capture matches numeric
outcomes by player ID/game ID, displaying every call and call-minus-actual; absence stays
unavailable and never becomes a DNP zero. Clearing browser data loses personal calls.

Next prospective grading checkpoint: **October 6, 2026**, after Week 4 Monday kickoff
and provider numeric outcomes arrive. Preserve the first archived model version and all
personal calls; match outcomes by stable ID/game, report observed/missing counts and
errors/range coverage by position/tier and horizon. Grade every archived version without
selecting the best retrospectively; identify a primary first version before scoring.
Archive input/outcome receipt hashes and corrections. Do not refit or promote based on
this single week. If outcomes are not published, defer grading and report the gap.

## Prospective grading command and saved scorecard

On October 6, after the Monday game and provider update, run exactly:

```sh
cd ~/Desktop/nfl-fantasy
.venv-fantasy/bin/python -m fantasy.receiving_grade --week 4 --refresh-outcomes
```

This separate command fetches only player stats and the schedule into its own grading
input cache. It does not call forecast generation, model fitting, interval calibration
or either historical experiment. It works even when there are no future games left.
Without `--refresh-outcomes`, it grades against the latest already-captured outcomes.
`--week 4` asserts the preserved primary's week, rather than selecting a new population.
No new acceptance gates, historical refit or promotion are introduced by grading.

The checksum-verified `artifacts/fantasy/receiving/v1/first-prospective.json` is always
the primary **273-player-game** population. Other local Week 4 archives are separate
versions, never selected by accuracy. Identical archive copies are deduplicated and
their locations retained. Invalid archives are listed separately; invalid primary
integrity fails closed. Saved points and both sets of ranges are read directly, with
no recalculation using actual volume, outcomes or later rosters.

Stable player/game IDs match outcomes. Schedule/team contradictions, duplicate rows,
nonfinite/missing components, negative or fractional counts and catches above targets
are invalid; negative receiving yards are valid. All three numeric outcome components
and both saved point estimates are required for the paired error population. An explicit
numeric zero is observed. A missing row after completion is **DNP/no-stat/unknown**,
excluded with no invented zero or inactive designation. Completion conservatively
requires numeric schedule scores and a game on an earlier Eastern calendar day; live
or same-day games remain pending even if scores exist. Missing schedule evidence also
remains pending. Grading is incomplete unless every primary forecast has a supported
paired outcome; missing/invalid rows remain visible even after all games finish.

MAE, RMSE and signed bias (estimate minus actual) use identical paired rows for model
and rolling baseline. Report overall, position, archived prior usage tier, forecast
horizon, position×tier and position×horizon. Frozen tiers are low <3, medium 3–<6,
high ≥6 prior rolling targets. Horizon is captured kickoff minus archived input cutoff:
0–<24, 24–<48, 48–<72 and ≥72 hours. These disjoint bins were fixed in the separate
grading config before observing prospective outcomes. Interval coverage is inclusive
of bounds; width is the mean saved high minus low. Missing/invalid ranges do not
contribute: interval n and unavailable-range counts are reported separately for each
model/metric. No observed rows means unavailable errors/coverage/width, not zero.

Immutable normalized outcomes are saved under `.fantasy-cache/receiving/grading/outcomes/`
and immutable scorecards under `scorecards/`, with exclusive timestamp/hash filenames.
Raw inputs and timestamped source receipts are under `grading/inputs/`; hashes in each
capture identify their exact bytes. Each capture links its predecessor, increments
capture sequence, and increments source revision only when source bytes change.
Repeated runs preserve earlier results, including later corrections; latest pointers
only choose the current view. Scorecards retain the grader/policy hash and outcome
capture hash. All source data and scorecards remain local and excluded from Git.

The separate **Prospective receiving scorecard** view at
<http://127.0.0.1:8520/fantasy> reads `/api/fantasy/receiving-grade` only. It defaults to
the primary version and exposes earlier scorecard captures, other forecast versions,
receipts, exclusions and stale/incomplete states. Reload never fetches outcomes or
generates forecasts. The existing personal-call view consumes independently refreshed
numeric outcomes by the same IDs; browser-local calls and their versions are unchanged.
Integrity failure clears unsupported outcomes rather than silently showing older values.

Initial real capture: **2026-10-02 23:38:22.009 UTC**, sequence/revision **1/1**.
Primary: **0 observed, 273 pending, 0 missing, 0 invalid**, so all errors/range coverage
are unavailable. There is one distinct forecast version, with the preserved bundle and
identical local copy. Player stats retrieved **23:38:21.237 UTC**, HTTP Last-Modified
**October 2 15:48:18 UTC**; schedule retrieved **23:38:22.008 UTC**, modification time
unavailable. Retrieval is not source publication time. Source hashes:

- Players: `ad728d131967bcd78d6e6a23b205a65db0bbb6f12bb7497eb15e70f36ebf1f85`.
- Schedule: `0d1fc615d6148c1e199469836b1f8ef241df9188fea71eb5b1ba8c9a25f5aad6`.

Fixtures and browser checks cover paired arithmetic, missing/zero/invalid outcomes,
duplicates, same-day completion guards, source corrections, checksum rejection,
no-future-game refresh, read-only APIs, separate versions and desktop/mobile scrolling.
The existing projection/personal-call UI checks still pass. Both frozen experiments,
the four method-hashed receiving files, existing predictions and 23 unrelated edits
are unchanged. Next bounded task is the October 6 capture and completeness audit;
repeat outcome capture later if publication gaps remain, without generating new calls.

## Verification boundary

92 focused Python checks passed (receiving math/time/leakage/identity/archives, original
fantasy modules and viewer, frozen artifact restoration). Ruff and JavaScript syntax
passed. New desktop/mobile browser suite verifies real API estimates, filters, sample
assumptions, version reload/coherence/kickoff locks, missing vs numeric-zero outcomes,
storage failure/corruption and totals/per-game comparison. Independent data/leakage and
saved-evaluation reviews passed; initial date-boundary and franchise-join failures were
fixed before the one real benchmark. The first browser attempt hit ECONNREFUSED because
a shell-launched preview exited; detached fantasy startup fixed it. A collapsed-details
test assertion was corrected to open the benchmark before inspecting visible text.
No real expected-points experiment, NFL worker or shared runtime was started or changed.
