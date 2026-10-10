# Frozen spread/total experiment: points-ridge-v1.2

Statistical specification declared October 10, 2026, before the v1 comparison. The accompanying TOML and
immutable private declaration capture the exact specification and input/code hashes.
The initial v1 comparison is already known. Version v1.1 fixes only a null-kickoff
live-sheet failure found before any archive; all statistical definitions below
remain those declared before v1 comparison. Both declarations/results are retained;
v1.1 rerun reproduced identical paired predictions, metrics and selected alphas.
Version v1.2 additionally replaces the234MB whole-view source snapshot with a
compact exact points-input projection after two ENOSPC attempts, before any archive.
It preserves the full parent-view SHA and these original game fields: identity,
season/week, team names, kickoff, schedule version, venue-neutral flag, time-valid
and game status, and every saved outcome revision. The last successful source-check
time is preserved. Historical scores/PBP retain their separate original hashes;
live pace retains its original raw capture/receipt. No other view field enters
the points model. All earlier benchmark results are known; v1.2 must again reproduce
the identical statistical evaluation. No old declaration/result is rewritten.
Reconciliation is complete; its missing fantasy/QB/cloud evidence does not block
this points-only experiment. Official winner probabilities and worker are unchanged.

Targets are final home points minus away points (margin), and their sum (total),
including overtime. Ties and zero scores are valid. Only regular-season targets;
prior regular-season games from the target and immediately preceding season.
Historical availability is conservatively kickoff +24 hours, not proof of original
publication. Historical inputs are reconstructed benchmark evidence (Grade C).
2016–2025 have already informed development; none is a fresh untouched holdout.
No injury/QB/weather/market features or season-end aggregates are used.

## Frozen baselines and features

For a target season, estimate home advantage as the mean nonneutral home margin
over prior regular seasons <=T-2, including the 2015 warmup year. This stays fixed
throughout that season.
The initial 2016 feature season has no <=2014 input history; it uses the declared
2.5-point seed only. Subsequent years estimate HFA from available <=T-2 seasons.
Massey uses only prior games available strictly before kickoff minus 60 minutes;
neutral games receive zero home advantage. Reuse the existing sum-zero Massey
solver. Shrink each team rating by n/(n+4), n = prior games across the two seasons.
Margin baseline = shrunk home rating − shrunk away rating + venue home advantage.

For each team, take its last eight available games within these two seasons.
Rolling points scored and allowed = (sum +4*prior league points/team-game)/(n+4).
League mean uses the same cutoff-eligible history. Total baseline = half the sum
of both teams' rolling scored and allowed values. At least three prior games per
team are required; otherwise both estimates are explicitly unavailable.

One Ridge regression per target with an unpenalized intercept. Margin features:
rating difference, venue home advantage, rolling scored difference, rolling allowed
difference, rolling offensive-play-count difference. Total features: absolute
rating difference, venue home advantage, rolling scored sum, rolling allowed sum,
rolling offensive-play-count sum. Pace counts rush_attempt==1 OR pass_attempt==1
OR sack==1 in prior PBP; null attempt flags do not qualify a play. Only games with
known offense teams count. Pace is averaged across available PBP games among the
last eight, with at least three observed games per team. Missing pace is excluded
and reported, never filled with zero. No standardization/imputation uses test data.
Mean/population standard deviation from estimator rows only; constant scales use 1.
No outcome clipping or rounding for evaluation.

Alpha in {1,10,100}, chosen separately by lowest pooled MAE in forward development
folds 2019 and 2020 (fit 2016 through T-2, preceding year reserved for calibration).
Ties select the smaller alpha. Freeze selected alpha before 2021–2025 comparisons.
No other model/window/feature search. For each evaluation season T, reuse outer_fold:
fit <=T-2, calibrate T-1, evaluate T. Both models use identical candidate-eligible
games; also report baseline-only coverage and reasons for exclusions.

Nominal 80% symmetric intervals use the finite-sample absolute-residual order
statistic ceil((n+1)*0.8), capped at n, on the preceding calibration season, separately
for each target/model. Report later coverage and mean width. Cluster bootstrap
resamples whole season/week blocks, preserving paired predictions (2,000 draws,
seed 20261010), with 95% intervals for metrics, paired deltas, coverage and width.
These intervals are empirical, with no exchangeability guarantee across seasons.

## Gate and prospective freeze

Before comparisons: >=2% lower pooled MAE, no higher pooled RMSE, identical eligible
games, separately per target. Report every year's variation. Passing permits
experimental tracking only; it establishes no betting value and permits no
production replacement. Failed candidates remain visible research benchmarks;
their baseline can still be archived, with the failed candidate scoring-excluded.

The deployment fit is fixed at estimator 2016–2024, intervals calibrated on 2025;
HFA fixed from 2015–2024. Coefficients, preprocessing, alpha and interval radii remain
unchanged through the first 100 distinct eligible future games. Features may update
using prior games only. First successful archive per game is the primary forecast,
generated within eight days of future kickoff, with fresh saved schedule/results
(<=2h old), valid venue, >=3 score and pace history games per team, and at least one
passing target. No retrospective archive, revision or replacement. Atomic write
checks wall clock again immediately before durable publication. No tuning against
interim outcomes; future revisions need a separate version/declaration.

Grading reads immutable original forecasts and subsequently observed FINAL results,
including overtime. Schedule/kickoff/venue mismatch, unavailable/retracted results,
invalid or conflicting finals, and forecasts no longer preceding kickoff are
explicitly excluded/pending. One paired population per target; corrections create
new content-addressed scorecards, preserving old ones. Interim scores are descriptive.
Collection requires standalone forecast runs; production worker is not modified.

## Separate market evidence

Only explicitly timestamped pregame quotes qualify: provider/sportsbook, target,
side, line, American odds, market timestamp and retrieval timestamp, both <=comparison
cutoff and <kickoff. Quotes cannot influence estimates or gate decisions. Unknown
timestamps and untimestamped closing lines are unavailable, not actionable prices.
No cover/over probabilities derived from point estimates; no profitability claim.
Vig, pushes, availability and costs require a subsequent separate value experiment.

Home margin +3 means model-implied home spread -3; away margin favorite is the away
team when home margin is negative. UI labels intervals, baselines, gate failures,
version, generated time/cutoff, and stale/unavailable states. No redesign.
