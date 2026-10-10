# Spread/total milestone evaluation — October 10, 2026

Implemented `points-ridge-v1.2`: frozen models, chronological paired evaluation, responsive experimental sheet, CSV export, immutable future-only archives and descriptive grading. Margin passes; total fails. Official winner probabilities and the production worker are unchanged.

The statistical specification and gate were declared before v1 comparisons. v1.1 fixes null future kickoffs; v1.2 preserves a compact exact source projection after two full-snapshot ENOSPC attempts. All three versions have exactly identical feature rows, paired prediction bytes, selected alphas and target reports. Original declarations/results remain private and immutable. No candidate was tuned against these results or prospective outcomes.

Historical 2016–2025 seasons were already inspected and informed development. These are reconstructed development/benchmark data, not an untouched holdout. Overtime is included in all final-score labels. Only regular-season games are targets and history.

## Population and chronological protocol

All available 2021–2025 regular-season games are paired: **1,359 per target**, zero source/feature exclusions and zero baseline-only games in those periods. The saved 2022 source contains 271 regular-season rows; the other four evaluation seasons contain 272 each. No absent source game is reconstructed. Exclusion lists (empty here) and game identities remain in the private machine report.

One standardized Ridge candidate per target. Penalty **alpha=1** selected for both targets using pooled MAE in earlier 2019/2020 development folds, never benchmark outcomes. Training-only means/population standard deviations; constant scales become 1; no missing-to-zero imputation. Exact features, rolling 8 windows, minimum 3 observations and 4-game shrinkage are in the specification and frozen config. HFA includes 2015 warmup history, fixed from <=T-2 per season. Deployment coefficients fit 2016–2024; intervals calibrate 2025; HFA estimates 2015–2024. No subsequent refit through the 100-game cohort.

Outer folds reuse `outer_fold`: estimator <=T-2, calibration T-1, evaluation T. Historical inputs strictly precede kickoff−60 minutes using kickoff+24 hours as a conservative availability proxy. That proxy is not original publication evidence. The forecast sheet uses an on-demand real cutoff up to 8 days before kickoff; this horizon difference limits transfer from the historical T60 benchmark.

| Evaluation | Estimator years | Calibration / n | Last calibration outcome | First evaluation cutoff |
|---|---|---|---|---|
| 2021 | 2016–2019 | 2020 / 256 | 2021-01-05T01:20:00Z | 2021-09-09T23:20:00Z |
| 2022 | 2016–2020 | 2021 / 272 | 2022-01-11T01:20:00Z | 2022-09-08T23:20:00Z |
| 2023 | 2016–2021 | 2022 / 271 | 2023-01-10T01:20:00Z | 2023-09-07T23:20:00Z |
| 2024 | 2016–2022 | 2023 / 272 | 2024-01-09T01:20:00Z | 2024-09-05T23:20:00Z |
| 2025 | 2016–2023 | 2024 / 272 | 2025-01-07T01:20:00Z | 2025-09-04T23:20:00Z |

## Paired results

Point metrics are in points; bias = prediction minus final outcome. Positive MAE improvement favors the candidate. The predeclared gate is >=2% lower pooled MAE and no higher pooled RMSE on identical eligible games. Yearly rows describe variation; the pooled gate alone decides eligibility.

### Margin — PASS

| Period | n | MAE baseline / candidate | RMSE baseline / candidate | Bias baseline / candidate | MAE improvement | Paired ΔMAE 95% CI |
|---|---:|---:|---:|---:|---:|---|
| Pooled | 1359 | 10.5427 / 10.2120 | 13.4803 / 13.1832 | -0.4808 / -0.4428 | 3.14% | [-0.4711, -0.1876] |
| 2021 | 272 | 11.2591 / 11.0415 | 14.2161 / 13.9877 | -0.0260 / -0.0077 | 1.93% | [-0.4142, -0.0250] |
| 2022 | 271 | 9.6190 / 9.0606 | 12.3130 / 11.8458 | -0.4605 / -0.6192 | 5.81% | [-0.8809, -0.2540] |
| 2023 | 272 | 10.4447 / 10.3341 | 13.5558 / 13.4757 | -1.1364 / -1.0196 | 1.06% | [-0.3551, +0.1187] |
| 2024 | 272 | 10.5342 / 10.2225 | 13.6760 / 13.3375 | -0.3109 / -0.2262 | 2.96% | [-0.5629, -0.0757] |
| 2025 | 272 | 10.8531 / 10.3971 | 13.5638 / 13.1680 | -0.4701 / -0.3418 | 4.20% | [-0.9447, +0.0370] |

| Period | 80% coverage baseline / candidate | Mean width baseline / candidate |
|---|---:|---:|
| Pooled | 79.84% / 79.76% | 34.8371 / 34.0828 |
| 2021 | 76.84% / 77.21% | 35.4895 / 34.0370 |
| 2022 | 87.45% / 87.45% | 37.6781 / 37.2278 |
| 2023 | 75.00% / 74.26% | 30.5527 / 30.2806 |
| 2024 | 83.46% / 80.51% | 36.9512 / 34.7895 |
| 2025 | 76.47% / 79.41% | 33.5246 / 34.0907 |

| Pooled metric | Baseline 95% CI | Candidate 95% CI | Candidate−baseline 95% CI |
|---|---|---|---|
| mae | [10.09032, 10.98413] | [9.75438, 10.64542] | [-0.47113, -0.18765] |
| rmse | [12.92531, 14.01818] | [12.62501, 13.70453] | [-0.43739, -0.16749] |
| bias | [-1.18359, 0.23550] | [-1.12992, 0.25315] | [-0.09748, 0.17130] |
| coverage | [0.77574, 0.82133] | [0.77474, 0.82031] | [-0.01243, 0.01099] |
| width | [34.32037, 35.35842] | [33.63469, 34.53401] | [-0.95509, -0.56171] |

### Total — FAIL

| Period | n | MAE baseline / candidate | RMSE baseline / candidate | Bias baseline / candidate | MAE improvement | Paired ΔMAE 95% CI |
|---|---:|---:|---:|---:|---:|---|
| Pooled | 1359 | 10.6429 / 10.5795 | 13.4704 / 13.3876 | +0.3182 / -0.2850 | 0.60% | [-0.1437, +0.0153] |
| 2021 | 272 | 11.2693 / 11.0592 | 13.8865 / 13.6780 | +1.4618 / +0.4884 | 1.86% | [-0.4256, -0.0142] |
| 2022 | 271 | 10.7609 / 10.7857 | 13.5469 / 13.5555 | +0.9873 / +0.5012 | -0.23% | [-0.0997, +0.1469] |
| 2023 | 272 | 10.4892 / 10.4217 | 13.4205 / 13.3128 | +0.0580 / -0.0813 | 0.64% | [-0.2519, +0.1036] |
| 2024 | 272 | 9.9647 / 9.9689 | 12.9688 / 12.9622 | -1.2007 / -1.7375 | -0.04% | [-0.1733, +0.1698] |
| 2025 | 272 | 10.7308 / 10.6629 | 13.5131 / 13.4188 | +0.2870 / -0.5929 | 0.63% | [-0.2237, +0.1004] |

| Period | 80% coverage baseline / candidate | Mean width baseline / candidate |
|---|---:|---:|
| Pooled | 80.21% / 80.79% | 34.1960 / 34.2106 |
| 2021 | 81.99% / 84.19% | 36.6449 / 37.7940 |
| 2022 | 80.44% / 80.07% | 35.2770 / 34.1238 |
| 2023 | 81.99% / 79.78% | 34.9673 / 33.7656 |
| 2024 | 81.99% / 83.46% | 32.5778 / 33.6428 |
| 2025 | 74.63% / 76.47% | 31.5168 / 31.7266 |

| Pooled metric | Baseline 95% CI | Candidate 95% CI | Candidate−baseline 95% CI |
|---|---|---|---|
| mae | [10.25161, 11.02623] | [10.20072, 10.95244] | [-0.14368, 0.01535] |
| rmse | [13.00663, 13.91693] | [12.94970, 13.81048] | [-0.15798, -0.00790] |
| bias | [-0.39378, 1.00120] | [-0.99650, 0.36716] | [-0.68384, -0.52827] |
| coverage | [0.78250, 0.82151] | [0.78897, 0.82660] | [-0.00220, 0.01474] |
| width | [33.78667, 34.58507] | [33.82066, 34.62407] | [-0.19170, 0.23586] |

All uncertainty uses 2,000 paired whole season/week block bootstrap draws, seed 20261010. Both predictions and outcomes remain together within each NFL week. Pooled 95% confidence intervals describe conditional benchmark uncertainty; week-resampling does not remove cross-season dependence or distribution shift. Coverage intervals above are fractions. Per-season confidence intervals for every metric/coverage/width are also in `evaluation.json`.

80% intervals use the absolute-residual finite-sample order statistic on earlier calibration data only, separately for each model/target. These are symmetric empirical marginal intervals, not calibrated cover/over probabilities. Neither gate nor historical error implies betting value. Total misses the 2% gate despite slightly lower RMSE and stays research-only.

## Prospective archive and sheet

First cohort contains **23/100** distinct eligible games. First archive: `2026_05_PHI_JAX`, generated `2026-10-10T18:54:16.466987Z`, cutoff `2026-10-10T18:54:14.476131Z`, archived `2026-10-10T18:54:16.467388Z`, kickoff `2026-10-11T13:30:00Z`; archive ID `445bdb8ac7641824a83857b6693a5c375bfe54a5152cd288073e6c4610471672`.

Every saved record has archived_at < kickoff and model_frozen_at <= cutoff <= generated_at. All first 23 records preserve passing-margin eligibility and explicitly exclude failed-total candidate scoring. Latest grading has zero finalized eligible games; all are prospective pending. Original forecasts are selected once, never replaced or reconstructed for started games. Corrections create new hashed scorecards.

Current sheet has 47 rows: 23 usable upcoming forecasts and 24 explicit `KICKOFF_UNVERIFIED` states. CSV: `~/nfl-predictor-live-data/points-v1.2/forecast-sheet.csv`. Local preview: http://127.0.0.1:8511/experimental (desktop/mobile verified). Existing 8510 viewer and worker were not restarted; this is an independent preview, not a production deployment.

Every archived source SHA identifies a compact exact input projection, including all original schedule/outcome fields used by the model and parent-view SHA. Live pace is one actual public capture; missing PBP has an explicit minimum-observation exclusion. The frozen historical input manifests retain all 22 schedule/PBP hashes. Immutable source snapshots precede publication. Raw bytes and forecast/model/data artifacts remain outside Git.

No compatible timestamped market quotes were supplied; market comparison is **UNAVAILABLE** for all 23 forecasts. Optional quote ingestion retains sportsbook/provider, target/side, line, odds, market and retrieval timestamps, with both <=cutoff and <kickoff. Untimestamped closing values cannot qualify. There is no cover/over probability or profitability claim. Subsequent value evaluation must address vig, pushes, availability and costs.

## Verification and limitations

- 29 focused points/viewer tests passed: strict temporal cutoff/current-game exclusion, season boundaries, neutral venue/sign, missing scores/pace, training-only scaling, calibration cutoffs, paired populations/week groups, RMSE gate, concurrent cohort limits, deadlines, immutability/tampering, source-before-archive, null kickoff, corrections and failed-target exclusion.
- 197 existing official forecast/contracts/runtime/scoring/shadow tests passed.
- Full suite: 1,381 passed, 4 failed, 1 skipped. The same 4 failures reproduced in a scratch HEAD checkout with entry-state unrelated code/config/fixture edits and no milestone files. The skipped source test requires unavailable real captures. No unrelated fix was attempted.
- Changed points Python/tests Ruff and JS syntax checks pass; `git diff --check` passes. Independent review found no remaining blocker.
- Desktop 1440×1000 and mobile 390×844: 47 cards / 23 forecast panels, no horizontal overflow; mobile panels stack. Reload works; actual unavailable states render; synthetic stale state renders; no JS console errors. Screenshots inspected privately under `/tmp/nfl-points-{desktop,mobile}.png`.

Exact unrelated failure output:
```text
FAILED tests/deploy/test_workflows.py:347: manifest SHA d84ef7a4522608997477c0a81ad2f5c8037841af9b87f3867d6a77bae400cccd != 0b75142d0891dbf825eb39df3d28597244515bd4f8bad7d1fdedfe03f13947ab
FAILED tests/test_config.py:20: assert 'nfl-predictor-v2' == 'nfl-predictor'
FAILED tests/workflows/test_dispatch.py:566 [dispatch.due, forecast.due]: same manifest SHA mismatch
SKIPPED tests/test_season_sources.py:97: real captures unavailable
```

Adjacent findings, left unchanged: `deploy/private-data-repo/config/data_repo.toml:17` pins the old manifest SHA; `tests/test_config.py:20` assumes checkout basename; `ops/week1_live.py:58` allocates a whole temporary payload before checking whether an immutable object exists, causing two ENOSPC attempts with the 234 MB full view. The frozen points snapshot projection avoids that allocation size without editing the shared helper. Available disk space remains limited; earlier technical versions produced no prospective archives.

Remaining limits: historical availability is reconstructed; source revisions may differ from original pregame inputs; team/season drift and omitted injuries/QB/weather persist; sparse/current PBP may exclude future games; manual standalone collection must continue until 100 eligible games and requires this Mac awake/online. No scheduling/service deployment is included. The requested UI rules file was absent, so existing styles were reused. 11,457 original forecast/model/source-version/fantasy bundle files verified byte-identical. Official Elo/model-policy/live-config bytes match entry; production worker PID793 and viewer PID789 retain their October3 start times, with a HEALTHY worker heartbeat. No merge, deploy, promotion, production worker restart or fantasy change occurred.

## Reproducible commands

Run from `~/Desktop/nfl-predictor-v2` on the committed branch with original private input hashes intact. Existing freezes/evaluations are reused; changes to code/config/input hashes fail closed and require a separate version. Never use an as-of clock to reconstruct forecasts.

```sh
PYTHONPATH=ops:src .venv/bin/python ops/season_points_experiment.py freeze
PYTHONPATH=ops:src .venv/bin/python ops/season_points_experiment.py evaluate
# Initial actual pace capture; reuse its original receipt for this milestone:
PYTHONPATH=ops:src .venv/bin/python ops/season_points_experiment.py capture-pace
PYTHONPATH=ops:src .venv/bin/python ops/season_points_experiment.py forecast
PYTHONPATH=ops:src .venv/bin/python ops/season_points_experiment.py grade
# Standalone preview only, leaving worker and 8510 viewer intact:
PYTHONPATH=ops:src .venv/bin/python ops/week1_viewer.py --port 8511
PYTHONPATH=ops:src .venv/bin/python -m pytest -q -p no:cacheprovider tests/test_season_points.py tests/test_season_points_experiment.py tests/test_week1_viewer.py
PYTHONPATH=ops:src .venv/bin/python -m pytest -q -p no:cacheprovider tests/contracts tests/workflows/test_forecast.py tests/test_season_live.py tests/test_season_scoring.py tests/test_season_shadow.py
PYTHONPATH=ops:src .venv/bin/python -m pytest -q -p no:cacheprovider
```

Ridge and Arrow-read APIs were verified against [scikit-learn Ridge](https://scikit-learn.org/stable/modules/generated/sklearn.linear_model.Ridge.html) and [Polars read_ipc](https://docs.pola.rs/api/python/stable/reference/api/polars.read_ipc.html).

## Artifact SHA-256

Private root `~/nfl-predictor-live-data/points-v1.2`; all hashes below are raw file bytes, not semantic IDs.

| Artifact | SHA-256 |
|---|---|
| declaration.json | `1ecfdbe9453b1ccf2e70b52fcc12d5c178e1869b9626f11f7ff61ffe9fb95a8b` |
| selection.json | `2f682ac9212042dbf843825f5ea208c8f9fe9c410622ff2494f5d64e3061f35c` |
| model.json | `8c1b0f194a86f2a838cf94ddc79f5c8f64fef9a5e469510aad590a7882b9991d` |
| evaluation.json | `a457368651bad1b4d67ea442ba77b4334421386953596bcb94bbbd351bbf6198` |
| feature-rows.json | `6212a81f0167e710d7c3f8993b739400ae029367d194e441bc11b832a76ecadb` |
| margin-paired-predictions.json | `5579ffe0572a8b08804b48d141dacc84a2c255ac105bc4864a480f89a6735505` |
| total-paired-predictions.json | `a5a158c612dba5ff6564b736b21c03a120279d4268e62448a8cc7aa7940a7454` |
| view.json | `cf1afcdb46c78b3415e75af937e7a90421a54943795a780b8ff8ae4f410ecd1d` |
| forecast-sheet.csv | `e529ed334e2666409678d59222dbee12531154a2e1a03b9ebb4096dd7fb6952d` |
| pace-receipt.json | `7e003bb0337fed9a11767a5abb112512e3258d38b563c8c9321f2f9146d017c6` |
| grade.json | `8cfa01472ce9bb1ce0cd38344de7664ec8496e40da047b36bb9285a9037ddec9` |

Original pace raw capture: `8acfb4cafc4d3ab86190734e70f10032e4a0485127e38d98e3a270c3480bc301`.

Archive manifest SHA hashes a canonical filename→raw-file-hash mapping for all 23 initial forecasts.
`7112a59027cdc0bb07110fdde3d0d710b0a14ee6743c8919e6410cf3267f9dce`.

Historical input hashes:

| Original input | SHA-256 |
|---|---|
| pbp/season=2015/data.arrow | `6307e71a753aa4ac4bd2a176075cca021d460790272962e570df8b8fe7293431` |
| pbp/season=2016/data.arrow | `fc3276a1b92f1e35f97ea5ba23acc10e401968cb309acfb78d9951023737ea6b` |
| pbp/season=2017/data.arrow | `5b56b7415490358e5daf2b9b69e2b5e19a6b6d9f8392f999cb9db63bc83c8ce1` |
| pbp/season=2018/data.arrow | `f7731ab00edc0a1821e9186d7ed5a91aa4215d729fa2e1bcf1a7b2ca174f3019` |
| pbp/season=2019/data.arrow | `d53a9ed7bda58a44b636e73d6af77c1814f52b4cb800cd1edeae62aaa5952342` |
| pbp/season=2020/data.arrow | `95a1f5a5e10b18b84b07159584ec277ea61a89c2d22c43b53f949a825d7ea676` |
| pbp/season=2021/data.arrow | `590c26435e27fb32826ba91ad433e3fa50931b7c956c40f3036230ee187fb944` |
| pbp/season=2022/data.arrow | `1a55ddac6f273dbc17e62dc6d42d76af62ed8c5b5ca7b1379c81aa4a40d542b7` |
| pbp/season=2023/data.arrow | `d01eb791a75e69eafb9f777052410782a1a9053cc37bbc50325638e9d502109b` |
| pbp/season=2024/data.arrow | `a8371dc558140b9127d2f2c8da8cfe0f269d32a666482755c78d9e2a6a54ba0b` |
| pbp/season=2025/data.arrow | `1b7b1dbda5847b521e13192e19340f3698891925e81db06cc329bcde107b3746` |
| schedules/season=2015/data.arrow | `0002c7a07e302cd26f46a443a17f0247bc2fea54393c7ae0f04ad1f97a653a8e` |
| schedules/season=2016/data.arrow | `94e9ecd941c575e2437a5e0e206086d9a367dc379a16ea9bd72c22a71945820d` |
| schedules/season=2017/data.arrow | `900a6a3318e61018e31619da52dd717a2f5ef22e063923adab140a3c611fdff8` |
| schedules/season=2018/data.arrow | `8b8e22729f7241838bd082a615e821cdbbffb7aa04b1b813e87342591a9c4af2` |
| schedules/season=2019/data.arrow | `1f1db5803b237252e1a77942764cfa77fab8afb1487a39682a7a28137685c59f` |
| schedules/season=2020/data.arrow | `37a27dbeb3091a093951ec4b4ad2bea4085975ee625dfd40b48ec3bd4dcfd66b` |
| schedules/season=2021/data.arrow | `d877227644d5ea7ce2c0ce03405d32c4bebd20756aecac2f6f6ff8f25902f557` |
| schedules/season=2022/data.arrow | `7be1132a7e6f8715cb976c61e2e90c39f55c0629626927bbcbb87a2e0747bdef` |
| schedules/season=2023/data.arrow | `c7c84b8910f8b6456e58ec165c2eb17f9803421525792099ffe684bfcefeb1d4` |
| schedules/season=2024/data.arrow | `4c1a3ce1aae6ec613ce5ba568697b6893886d4b2172e806d82fe161575703ef6` |
| schedules/season=2025/data.arrow | `773af19fe0da3ba77fe48b6654547bada7b3703d2dc11c067ce95dc4054f2f38` |
