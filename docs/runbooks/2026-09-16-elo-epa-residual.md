# Elo-plus-EPA residual experiment — 2026-09-16

Completed research milestone; production Elo, existing shadows and historical forecasts unchanged.
No promotion, wagering, paid APIs or external deployment.

## Method
One declared four-feature EPA residual: production Elo logit is an offset with coefficient fixed
at one. Fit a ridge-penalized intercept and standardized offensive, defensive, passing and rushing
EPA differences. Penalty 1; no parameter search. Control fits the intercept without EPA.
Both use identical sigmoid calibration, fitted on the preceding season only. Estimator/scaler use
2018 through evaluation season minus two. Evaluate 2021–2025, T60 primary and T72 secondary.
Baseline uses the unchanged production formula; all models have identical tie mass.
All 1,359 scheduled finalized evaluation games per horizon are matched; no model-specific
exclusions. Four ties enter Brier/log loss and are excluded from winner accuracy (N=1,355).
Three-outcome Brier sums squared errors (0–2); log loss is natural logarithm.
Calibration bins and Wilson intervals are in the report and dashboard. Paired uncertainty
resamples season/week clusters, 1,000 draws. Intervals are exploratory, not multiplicity-adjusted.

All 2016–2025 data have influenced development. 2021–2024 are reused validation;
2025 is a known benchmark, not an untouched holdout. Grade-C reconstruction uses corrected
PBP/provider EPA and a strict kickoff-plus-24-hour final availability proxy. Native feature
lineage is verified independently for each cutoff. No original historical input issuance timestamps,
no timestamped historical moneylines and no claim of same-information market performance.
The existing standalone EPA report remains available but has a longer training history (2016+);
this experiment isolates incremental EPA against its own identically trained control.

## Actual results

| Cutoff | Season | Model | Correct/non-ties | Brier | Log loss |
|---|---|---|---:|---:|---:|
| T60 | 2021 | elo_calibrated_control | 173/271 | 0.461089 | 0.676833 |
| T60 | 2021 | elo_epa_residual | 173/271 | 0.460043 | 0.675086 |
| T60 | 2021 | production_elo | 158/271 | 0.473297 | 0.684626 |
| T60 | 2022 | elo_calibrated_control | 171/269 | 0.462167 | 0.684708 |
| T60 | 2022 | elo_epa_residual | 177/269 | 0.459337 | 0.681650 |
| T60 | 2022 | production_elo | 168/269 | 0.459368 | 0.680809 |
| T60 | 2023 | elo_calibrated_control | 164/272 | 0.464986 | 0.663074 |
| T60 | 2023 | elo_epa_residual | 167/272 | 0.464848 | 0.662975 |
| T60 | 2023 | production_elo | 163/272 | 0.468214 | 0.667749 |
| T60 | 2024 | elo_calibrated_control | 181/272 | 0.430717 | 0.624628 |
| T60 | 2024 | elo_epa_residual | 181/272 | 0.429424 | 0.623342 |
| T60 | 2024 | production_elo | 187/272 | 0.426532 | 0.619019 |
| T60 | 2025 | elo_calibrated_control | 176/271 | 0.454058 | 0.664118 |
| T60 | 2025 | elo_epa_residual | 175/271 | 0.461415 | 0.673070 |
| T60 | 2025 | production_elo | 172/271 | 0.456275 | 0.665142 |
| T60 | overall | elo_calibrated_control | 865/1355 | 0.454598 | 0.662656 |
| T60 | overall | elo_epa_residual | 873/1355 | 0.455010 | 0.663211 |
| T60 | overall | production_elo | 848/1355 | 0.456735 | 0.663456 |
| T72 | 2021 | elo_calibrated_control | 173/271 | 0.461017 | 0.676713 |
| T72 | 2021 | elo_epa_residual | 173/271 | 0.459565 | 0.674578 |
| T72 | 2021 | production_elo | 158/271 | 0.473244 | 0.684569 |
| T72 | 2022 | elo_calibrated_control | 171/269 | 0.462068 | 0.684580 |
| T72 | 2022 | elo_epa_residual | 177/269 | 0.459131 | 0.681410 |
| T72 | 2022 | production_elo | 168/269 | 0.459254 | 0.680657 |
| T72 | 2023 | elo_calibrated_control | 164/272 | 0.465083 | 0.663191 |
| T72 | 2023 | elo_epa_residual | 166/272 | 0.465523 | 0.663662 |
| T72 | 2023 | production_elo | 163/272 | 0.468317 | 0.667885 |
| T72 | 2024 | elo_calibrated_control | 181/272 | 0.431086 | 0.625039 |
| T72 | 2024 | elo_epa_residual | 181/272 | 0.429694 | 0.623650 |
| T72 | 2024 | production_elo | 187/272 | 0.426914 | 0.619452 |
| T72 | 2025 | elo_calibrated_control | 176/271 | 0.453885 | 0.663940 |
| T72 | 2025 | elo_epa_residual | 175/271 | 0.462029 | 0.673855 |
| T72 | 2025 | production_elo | 172/271 | 0.456108 | 0.664981 |
| T72 | overall | elo_calibrated_control | 865/1355 | 0.454622 | 0.662677 |
| T72 | overall | elo_epa_residual | 872/1355 | 0.455186 | 0.663418 |
| T72 | overall | production_elo | 848/1355 | 0.456766 | 0.663496 |

## Interpretation and decision
EPA improves over the calibrated control in all four reused validation seasons at T60,
but only three of four at T72. It worsens the known 2025 benchmark at both horizons.
Overall incremental benefit remains unproven despite more correct winner picks:
- T60 residual minus control log loss: +0.000555; 95% paired interval [-0.002557, +0.003847].
- T72 residual minus control log loss: +0.000741; 95% paired interval [-0.002398, +0.003973].

Both intervals include zero. Small aggregate improvements against uncalibrated Elo do not
establish an incremental EPA benefit. No feature-group removal was selected in this milestone;
the four-feature package remains inconclusive. Production and existing live shadows continue.
This evaluator does not issue a new prospective EPA shadow.

Single next milestone: archive timestamped EPA input snapshots and freeze a prospective shadow
protocol, including eligible decision windows and missing-input behavior, to collect new evidence.
Do not retune on the already-inspected 2025 benchmark or promote this challenger from these results.

## Verification and limitations
- Five new tests; 51 focused tests pass. Training/calibration separation, training-only scaler,
  fixed Elo offset, missing/duplicate/cutoff/label mismatches and numerical convergence tested.
  Existing native feature leakage and horizon tests included.
- Full suite: **1,258 passed, 42 failed, 1 skipped**. Failure names exactly match the independently
  tested unchanged baseline (`/tmp/nfl-audit-reference-tests.txt`). Example output:
  `tests/runtime/test_services.py:852: AssertionError: assert 'MISSED' == 'FAILED'`.
  Existing fixed `tests/workflows/test_forecast.py:50` kickoff is now past; actual receipt publication
  ctime at `src/nfl_predictor/workflows/forecast.py:1946` correctly enforces the cutoff.
  This adjacent fixture problem was not changed. Source capture test skipped at
  `tests/test_season_sources.py:97` because the optional real-capture fixture is unavailable.
- Changed Ruff, JS syntax, existing source mypy (81 files), and diff whitespace checks pass.
- Real browser checks at 1440px and 390px: both horizons, season filters, calibration details,
  unchanged live scorecard, no page errors or document overflow. Tables scroll horizontally on mobile.
  Screenshots `/tmp/nfl-residual-1440.png`, `/tmp/nfl-residual-390.png` inspected.
- 8,154 predictions identical across two final runs. All 2,718 baseline game/horizon predictions
  match the prior evaluation's probabilities, labels and cutoffs exactly.
- All 13,380 original production/shadow artifacts in the preservation manifest are byte-identical.
- Initial incomplete run stopped on a near-machine-precision optimizer threshold; added a
  Newton-step convergence criterion and regression test before publishing results. A subsequent
  provenance-hash rerun stopped on an incorrect dependency path, corrected to the actual
  `src/nfl_predictor/ratings/elo.py`. Neither incomplete run published a report.

## Artifacts and launch
Final run: `f7cf193b327859ccaf77f27f7989f386becc63871050fedd1cf912c48011ed75`.

Private immutable declarations, predictions and reports:
`~/nfl-predictor-live-data/insights/residual/{declarations,predictions,evaluations}/<run>.json`.
The dashboard reads `~/nfl-predictor-live-data/insights/residual.json`; original evaluations remain intact.
Final repeat runs: `f60d38570cdb15869498f3474968823ffcc75a76ef177b1412bb37bd76c8b82d`,
`f7cf193b327859ccaf77f27f7989f386becc63871050fedd1cf912c48011ed75`.

Working dashboard: http://127.0.0.1:8510/performance . Open the historical season/cutoff filters;
“Does EPA add information beyond Elo?” follows the native EPA comparison.
Existing local viewer LaunchAgent was restarted; no external deployment.
To rerun research from the V2 checkout: `.venv/bin/python ops/season_residual.py`.
To restart the existing viewer: `launchctl kickstart -k gui/$(id -u)/com.su760.nfl-week1-viewer`.
The source is included in the coherent V2 evaluation/dashboard milestone; the delivery report records the final commit.
