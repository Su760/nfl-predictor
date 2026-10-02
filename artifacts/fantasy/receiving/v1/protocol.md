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
