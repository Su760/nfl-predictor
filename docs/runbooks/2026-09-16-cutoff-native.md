# Cutoff-native football features — September 16, 2026

The milestone is implemented on the V2 branch. The performance dashboard now evaluates all **1,359 finalized 2021–2025 games at both T−72h and T−60m**, using separately reconstructed inputs. This recovers all374 previously excluded T60 games (348 cutoff failures plus26 missing venue-dependent cached rows) and removes the T72 football-model sample blocker. No production model or policy was changed, and nothing was promoted. The delivery report records the final commit.

Dashboard: http://127.0.0.1:8510/performance . Choose the historical season and cutoff below the live scorecard. Both horizons show five models, proper scores, accuracy, calibration uncertainty and coverage. The T60 overall view also compares the old985-game sample with the same games under the new training/calibration coverage. The earlier report and original predictions remain preserved.

## What changed and why

The old reconstruction used preceding calendar dates at `ops/season_rebuild.py:480` and a later reconstruction clock at :550. Strict caller-side filtering therefore discarded useful games. New `ops/season_cutoff.py` reconstructs inputs from the existing archived PBP and frozen schedule separately at each prediction cutoff. It reuses the original EPA aggregation, opponent-adjustment ridge fits, prior regression and blending. Only the four features actually consumed by this EPA model are produced: offense, defense, passing and rushing EPA differences. Neutral venues no longer cause exclusion because these four features do not consume venue data. No other features are filled with placeholders.

Every history game must have its final-availability proxy strictly before cutoff, including equality rejection. Current-game and future-season rows are excluded. Priors come only from the preceding season. Current-season ratings use only completed eligible games. The original forecast cutoffs and production path are untouched.

Historical publication receipts do not exist: final availability remains the explicit kickoff+24h proxy, and corrected PBP/provider EPA versions can contain later revisions. Outputs are **grade-C reconstructed research**, never predictions represented as issued before kickoff. Full coverage does not remove that limitation. The frozen schedule has271 finalized games in2022 and272 in each other evaluation season.

The existing EPA wrapper requires42 input columns even though it selects four. A checked research adapter reuses its exact StandardScaler/logistic pipeline on four genuine columns; it rejects nonfinite inputs/invalid labels and fits only non-ties. A regression test verifies identical probabilities to the original wrapper when supplied equivalent feature columns. Pipeline behavior was checked against the [official scikit-learn documentation](https://scikit-learn.org/stable/modules/generated/sklearn.pipeline.Pipeline.html). No production wrapper was weakened.

## Frozen protocol and lineage

Final evaluation `804a12a745dad3afe41cc391e7ad0d5dc5eda3ce558056af81868aa264fe9c7f`, declared `2026-09-16T22:36:27.119640Z`. Native build `0a57cbea164b90ad7079ca7146f44412a3b39270eed913257d35c888bd623e81`.

Private artifacts live under `~/nfl-predictor-live-data/insights/`:

- `cutoff-native/<build>/declaration.json`: configuration, source/code hashes and package versions.
- `cutoff-native/<build>/input-manifests/`: real hash-addressed manifests of the preceding/current-season PBP and frozen schedule; historical publication time explicitly null.
- `cutoff-native/<build>/selections/`: per-game/per-horizon cutoff, all prior/current history game IDs, latest eligible availability proxy and exact feature values.
- `cutoff-native/<build>/{T72,T60}.jsonl`:2,639 games per horizon across2016–2025.
- `evaluations/<run>.json`, `predictions/<run>.json`: immutable evaluation reports and predictions; `evaluation.json` is the dashboard pointer. No live forecast directory is written.

Reproduce from the V2 root with:

```sh
.venv/bin/python ops/season_evaluation.py
```

An unchanged build reuses hash-verified datasets. Source/code/config changes create a different build namespace. Per-row lineage, input-manifest and dataset hashes are checked when loading. Interrupted builds can safely repeat deterministic writes; earlier artifacts remain untouched. Repeated real evaluations produce the same13,590 model/game/horizon predictions byte-for-byte. Intermediate failed attempts are recorded separately in `run_status`; they did not replace the dashboard report with partial scores.

For target season T, estimator seasons end T−2 and calibration uses T−1 non-ties. Scaler fitting uses estimator data only. Hyperparameters and the sigmoid calibration family remain fixed. The same two predeclared feature removals and last-three-estimator-seasons comparison are rerun; no post-result variant search was added. Ties are fitted using previous seasons, included in three-outcome Brier/log loss and excluded from winner accuracy. Every model comparison has identical games and cutoffs; revisions are not additional games.

2016–2025 already influenced development.2021–2024 are reused validation/research,2025 is a known benchmark; none is an untouched holdout. Future forecasts after the freeze remain prospective. Historical price timestamps, expected-starter evidence, weather and injury histories are not invented. No market-only or market-informed challenger is possible from the audited historical price data available here.

## Actual full-coverage results

Each horizon:1,359 scored games,1,355 non-ties,4 ties,zero feature-coverage exclusions. Brier is three-outcome sum of squared errors (0–2); log loss uses natural logarithms. Lower is better. Production Elo and the simple Elo baseline are the same formula, reported once.

|Horizon|Model|Correct/non-ties|Accuracy|Brier|Log loss|
|---|---|---:|---:|---:|---:|
|T72|football_epa|831/1355|61.33%|0.466130|0.674407|
|T72|football_epa_recent_training|825/1355|60.89%|0.468385|0.676684|
|T72|production_elo|848/1355|62.58%|0.456766|0.663496|
|T72|without_passing_epa|829/1355|61.18%|0.466899|0.675254|
|T72|without_rushing_epa|841/1355|62.07%|0.465373|0.673569|
|T60|football_epa|833/1355|61.48%|0.465316|0.673393|
|T60|football_epa_recent_training|824/1355|60.81%|0.467769|0.675848|
|T60|production_elo|848/1355|62.58%|0.456735|0.663456|
|T60|without_passing_epa|828/1355|61.11%|0.466172|0.674333|
|T60|without_rushing_epa|839/1355|61.92%|0.464516|0.672503|

|Horizon|Season|Games|Elo correct/non-ties|Elo Brier|Elo log loss|EPA correct/non-ties|EPA Brier|EPA log loss|
|---|---:|---:|---:|---:|---:|---:|---:|---:|
|T72|2021|272|158/271|0.473244|0.684569|162/271|0.468870|0.680866|
|T72|2022|271|168/269|0.459254|0.680657|173/269|0.460364|0.683540|
|T72|2023|272|163/272|0.468317|0.667885|167/272|0.467707|0.664969|
|T72|2024|272|187/272|0.426914|0.619452|176/272|0.434870|0.628820|
|T72|2025|272|172/271|0.456108|0.664981|153/271|0.498815|0.713877|
|T60|2021|272|158/271|0.473297|0.684626|163/271|0.469031|0.680932|
|T60|2022|271|168/269|0.459368|0.680809|172/269|0.460280|0.683469|
|T60|2023|272|163/272|0.468214|0.667749|167/272|0.466425|0.663509|
|T60|2024|272|187/272|0.426532|0.619019|176/272|0.435996|0.630014|
|T60|2025|272|172/271|0.456275|0.665142|155/271|0.494829|0.709078|

EPA loses to Elo on aggregate proper scores and in three of five seasons at each horizon. This is evidence against promotion, not proof that Elo is universally superior: paired Elo-versus-EPA log-loss intervals still include zero.

## Feature comparisons

Variant minus complete EPA;95% paired season/week-cluster bootstrap,1000 replicates, seed42. Negative favors the variant. Intervals are exploratory and not corrected for multiple comparisons.

|Horizon|Variant|Log-loss change|95% interval|
|---|---|---:|---|
|T72|without_passing_epa|+0.000846|[+0.000064, +0.001637]|
|T72|without_rushing_epa|-0.000838|[-0.001899, +0.000282]|
|T72|football_epa_recent_training|+0.002277|[+0.000204, +0.004237]|
|T60|without_passing_epa|+0.000940|[+0.000051, +0.001843]|
|T60|without_rushing_epa|-0.000890|[-0.001927, +0.000198]|
|T60|football_epa_recent_training|+0.002455|[+0.000272, +0.004527]|

- Removing the separate passing indicator worsens log loss in4/5 seasons at both horizons; aggregate intervals are above zero. Retain it pending prospective evidence.
- Recent-only training worsens log loss in4/5 seasons at both horizons; expanding history is supported by this comparison.
- Removing the separate rushing indicator improves5/5 T72 seasons and4/5 T60 seasons, but both aggregate intervals include zero. The small benefit remains inconclusive. Aggregate offensive EPA still contains rushing plays; this is not removal of all rushing information.

On the original985 T60 games, complete EPA improves from log loss0.669221 to0.664591 (change−0.004630,95% interval[−0.008196,−0.001091]). Elo remains exactly0.655371 on those games. This is not solely an effect of changing game features: training/calibration coverage expanded as well. Full-population EPA log loss is0.673393, illustrating why the easier old subset should not be compared directly with the recovered population.

## Verification

- All2,639 games reconstructed at both horizons; no missing directional PBP histories. Strict date/cutoff selection is saved for every row.
- Formula parity on old cutoff-safe rows:1,988 T60 games, maximum absolute feature difference6.11e−16;123 T72 games,2.50e−16.
- Different horizons produce materially different features on2,253/2,639 games, including1,189/1,359 evaluation games. They are not relabeled copies of one snapshot.
- Tests cover equality/future cutoffs, future/target EPA invariance, duplicate plays, nonfinite and fractional fields, team closure, neutral-site coverage, deterministic restart, dataset/input-manifest/lineage tampering, equivalent four-column/legacy classifier probabilities, future-label invariance and legacy model-label sample matching.
-46 focused tests passed. Full suite:1,253 passed,42 inherited failures,1 skipped; failing test names exactly match the independently verified baseline. Example: `tests/runtime/test_services.py:852`, `AssertionError: assert 'MISSED' == 'FAILED'`. The dated fixture at `tests/workflows/test_forecast.py:50` conflicts with real filesystem publication times at `src/nfl_predictor/workflows/forecast.py:1946`; that adjacent fix remains outside this milestone. No full-green claim.
-Changed Python files pass Ruff; JS syntax and diff checks pass; mypy reports no issues in81 source files.
-Desktop1440/mobile390 real rendering verified: full coverage and all five models at both horizons, season/calibration filters, common-sample comparison, unchanged live scorecard, no document overflow or JavaScript errors. Screenshots inspected.
-13,380 original production/shadow forecast files and receipts are hash-identical. The old cached dataset is also unchanged. Original successful native evaluations reproduce13,590 predictions exactly after provenance-only manifest improvements.

## Next milestone

Freeze a bounded **football-only Elo-plus-EPA residual challenger**, asking whether EPA contributes incremental information beyond Elo rather than replacing the stronger baseline. Fit only on chronological training/validation data; evaluate both horizons and retain a future prospective shadow period. No automatic promotion and no new paid data, wagering or deployment.

## Saved Weeks 1–2 comparison update — September 21, 2026

This is a report-only derivation from immutable prediction run `804a12a745dad3afe41cc391e7ad0d5dc5eda3ce558056af81868aa264fe9c7f`.
No model was refit and no historical prediction was regenerated. The report update is stored
separately under `insights/report-updates/`; only the mutable dashboard pointer changed. Every model
uses the same 160 Weeks 1–2 games (159 non-ties, one tie) and 1,359 full-season games (1,355
non-ties, four ties) at its stated cutoff. Accuracy uncertainty is a 95% Wilson interval. Ties enter
three-outcome Brier/log loss and are excluded from winner accuracy. Brier is the sum of three squared
outcome errors, range 0–2; log loss uses natural logarithms.

|Horizon|Model|Weeks 1–2 correct / non-ties|Accuracy (95% CI)|Brier|Log loss|Full correct / non-ties|Accuracy (95% CI)|Brier|Log loss|
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|
|T−72h|Production Elo|95/159|59.75% (51.98–67.05%)|0.477906|0.696866|848/1355|62.58% (59.97–65.12%)|0.456766|0.663496|
|T−72h|Football EPA|97/159|61.01% (53.25–68.24%)|0.467756|0.686321|831/1355|61.33% (58.71–63.89%)|0.466130|0.674407|
|T−72h|EPA, recent 3|98/159|61.64% (53.89–68.83%)|0.466419|0.684691|825/1355|60.89% (58.26–63.45%)|0.468385|0.676684|
|T−72h|Without passing EPA|94/159|59.12% (51.35–66.46%)|0.469257|0.687977|829/1355|61.18% (58.56–63.74%)|0.466899|0.675254|
|T−72h|Without rushing EPA|100/159|62.89% (55.16–70.02%)|0.466815|0.685254|841/1355|62.07% (59.45–64.61%)|0.465373|0.673569|
|T−60m|Production Elo|95/159|59.75% (51.98–67.05%)|0.477906|0.696866|848/1355|62.58% (59.97–65.12%)|0.456735|0.663456|
|T−60m|Football EPA|96/159|60.38% (52.62–67.65%)|0.465606|0.684142|833/1355|61.48% (58.86–64.03%)|0.465316|0.673393|
|T−60m|EPA, recent 3|97/159|61.01% (53.25–68.24%)|0.464072|0.682193|824/1355|60.81% (58.19–63.38%)|0.467769|0.675848|
|T−60m|Without passing EPA|94/159|59.12% (51.35–66.46%)|0.467466|0.686219|828/1355|61.11% (58.48–63.67%)|0.466172|0.674333|
|T−60m|Without rushing EPA|100/159|62.89% (55.16–70.02%)|0.464478|0.682851|839/1355|61.92% (59.30–64.47%)|0.464516|0.672503|

The early Elo estimate is 2.83 percentage points below its full-season estimate, but its interval is
wide and overlaps the full-season interval. Early challenger differences are likewise uncertain;
their paired season/week-cluster log-loss intervals versus Elo all include zero. This small reused
research slice does not justify a model change.

The saved **622/982 (63.34%)** Elo result is the legacy T−60m cutoff-safe cached subset: 985 scored
games, three ties, and 374 exclusions. The **848/1355 (62.58%)** result is the cutoff-native full
population: 1,359 scored games, four ties, and zero feature-coverage exclusions. They answer different
coverage questions and must not be blended or treated as a before/after accuracy improvement.
