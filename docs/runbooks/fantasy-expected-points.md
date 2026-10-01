# Retrospective opportunity points — frozen evaluation protocol

Design approved October 1, 2026. This protocol and `configs/fantasy_points.toml`
are recorded before any model comparison. No historical holdout outcome has been
evaluated for this method at protocol creation. The old NFL game-outcome experiments
are separate; these corrected historical files are not prospective evidence.

## Splits and one fixed method

- Fit only 2019–2022 regular seasons, WR/RB/TE player-games.
- Use 2023 for validation/model decisions. The prespecified method has no hyperparameter
  search. Retain it unchanged if evaluated; no validation or holdout refit.
- Final holdout: 2024–2025 regular seasons, pooled for performance gates, with coverage
  reported separately for each season and position. Do not access it through the evaluator
  until the validation report, method/config hash, model hash and decision are sealed.
- Never fit, select or tune on 2026. Use observed 2026 context only for retrospective display.

Candidate: separate position/opportunity-type cells, field position at-or-inside
5/10/20/50/beyond-50, and target-depth bins below 0, [0,10), [10,20), 20+ yards.
Each cell's mean scoring outcome is shrunk toward its position/type training mean
with exactly 100 prior opportunities: `(n * cell_mean + 100 * parent_mean)/(n + 100)`.
Unseen cells use the parent mean. Missing context never goes to a zero bucket.
No player identity, catch result, gained yards, TD, EPA, scoring outcome or future
information enters the predictor. Catches/yards/TDs are labels only. Target depth is
observed after the target, so this is a retrospective valuation, not a pregame forecast.

Comparator: historical mean points per target and per carry, separately by position,
learned from exactly the same eligible training player-games and opportunities.
Both models score exactly the same eligible validation/holdout player-games; all their
opportunities are summed. No averaging of per-game percentage shares is involved.

## Supported scoring and reconciliation

All formats award 0.1 per credited rushing/receiving yard and 6 per credited rushing/
receiving touchdown. Reception values: standard 0, half-PPR 0.5, PPR 1.
Negative yardage remains negative. Excluded: turnover penalties, bonuses, passing,
two-point conversions, kicking, defense, return points and fumble-recovery TDs.
These are supported rushing/receiving components, not complete league fantasy totals.
Require all five box-score components, including receptions, in all formats.

Quality counts exclude deleted plays, `no_play`, non-offensive play types and two-point
tries. Eligible offensive types: pass, run, qb_kneel, qb_spike. An identified receiver
with a credited pass attempt is a target; an identified rusher with a rush attempt is
a carry. Require `play_deleted=0` and `two_point_attempt=0`, not absent flags.
Require an END GAME marker and unique non-null play IDs for the game.

Evaluation eligibility requires complete player IDs, team and position; numeric
nonnegative integer target/carry counts; exact play-to-box target/carry counts; known
field position in [0,100] on every opportunity; known target depth and a depth sum
matching box-score receiving air yards. Require all five scoring labels to reconcile
with the box score. Receptions and rushing/receiving TDs must be nonnegative integers.
The receiver gets the credited receiving yards and the rusher the credited rushing
yards; TD attribution requires the scoring player ID. Explicitly incomplete passes
have zero receiving yards/receptions; missing completion status is unavailable.

This rule applies to every season and every player, including laterals. Do not maintain
named-player exclusions. A lateral mismatch can leave descriptive quality and actual
box-score points available but makes the entire player-game ineligible for BOTH models.
This eligibility uses outcome reconciliation for attribution quality, not prediction.
It creates a selection limitation, which the coverage reports expose.

Report one primary exclusion per row, in this order: incomplete/duplicate PBP, missing
box counts, opportunity-count reconciliation, missing context, target-depth reconciliation,
missing scoring components, invalid scoring components, missing play outcomes, outcome
reconciliation. Counts add to excluded totals. Position is the source player-game label.
Missing player-games are not synthesized; the coverage denominator is every regular-season
WR/RB/TE box-score player-game, including verified zero-opportunity rows. Report both
denominator and exclusions by season and position, including training seasons.

## Numerical display gates, fixed before the holdout

All must pass, otherwise the method remains research and only descriptive features ship:

1. At least **80%** eligible box-score player-games in **each evaluation season** (2023,
   2024 and 2025). Report position coverage; this coverage floor is overall per season.
2. For **each** of standard, half-PPR and PPR, context-model pooled player-game RMSE
   must be **at least 2% lower** than comparator RMSE in **both** validation and holdout.
3. For each format in both periods, pooled MAE must be **no higher** than comparator MAE.
4. For each WR/RB/TE position and each format in both periods, context RMSE must be
   **no more than 5% higher** than comparator RMSE.

Report player-game MAE, RMSE and signed bias **prediction minus actual**, overall and
by position/scoring. Every pair uses identical rows. Report 95% paired game-cluster
bootstrap intervals for comparator MSE minus context MSE (500 draws, seed 1701);
positive values favor context. These intervals describe uncertainty and are not another
selection gate. Correlated player outcomes within a game stay together in each draw.

The original one-time protocol used `fantasy.evaluate validate` and then
`fantasy.evaluate holdout` after inspection of the seal. Both stages are completed;
**do not run them to restore this experiment or reevaluate its consumed holdout**.
The original command guards refuse completed evaluations and reject changed seals.
Raw bytes and local working artifacts remain under ignored `.fantasy-cache/`.
The small public artifacts are now preserved byte-for-byte in
[`artifacts/fantasy/expected-points/v1`](../../artifacts/fantasy/expected-points/v1/README.md).
Use `.venv-fantasy/bin/python -m fantasy.restore` to restore the original four JSON
files and verify the existing API integrity checks against saved aggregate reports.
No fitting, raw-game evaluation, bootstrap, source refresh or gate changes occur.
No forecasting runtime or archives are used.

## Validation decision recorded before opening holdout

Sealed at **2026-10-01T05:46:35.507874+00:00**. Training: 20,076 eligible player-games.
Validation: 5,131 / 5,138 player-games (99.86%); seven outcome-attribution exclusions.
All prespecified validation gates passed. Decision: retain the original bins,
100-opportunity shrinkage and fitted 2019–2022 model unchanged. No model search or refit.

| Scoring  | Baseline MAE / RMSE / bias | Context MAE / RMSE / bias |
| -------- | -------------------------- | ------------------------- |
| standard | 2.5590 / 3.8822 / 0.1949   | 2.3080 / 3.6393 / 0.0416  |
| half_ppr | 2.6799 / 4.0637 / 0.1852   | 2.4840 / 3.8644 / 0.0449  |
| ppr      | 2.8307 / 4.2845 / 0.1755   | 2.6810 / 4.1208 / 0.0482  |

Sealed method SHA-256: `44c612e6cd4a9c84146c34af563fe6400af4d1061069dbc6967255ad8a89baae`.

Sealed model SHA-256: `d07cbf47110ebd0f2fe58cf82fe08552ef4eab2989996705bca9b1d365a13da1`.

## Final holdout result and display decision

Evaluated once at **2026-10-01T05:48:01.286972+00:00**, using the sealed model above.
All gates passed; retrospective expected points are enabled for fully reconciled
2026 windows. No post-holdout parameter change, model selection or refit occurred.
This holdout is now consumed and cannot be reused as untouched evidence for a new method.

| Period            | Scoring  | Position | Player-games | Baseline MAE / RMSE / bias | Context MAE / RMSE / bias |
| ----------------- | -------- | -------- | -----------: | -------------------------- | ------------------------- |
| 2023 validation   | standard | ALL      |         5131 | 2.5590 / 3.8822 / 0.1949   | 2.3080 / 3.6393 / 0.0416  |
| 2023 validation   | standard | RB       |         1471 | 2.8043 / 4.1769 / 0.3661   | 2.4961 / 3.8948 / 0.1773  |
| 2023 validation   | standard | TE       |         1186 | 1.9323 / 2.9481 / 0.2379   | 1.6268 / 2.6508 / -0.0629 |
| 2023 validation   | standard | WR       |         2474 | 2.7136 / 4.0886 / 0.0725   | 2.5228 / 3.8834 / 0.0111  |
| 2023 validation   | half_ppr | ALL      |         5131 | 2.6799 / 4.0637 / 0.1852   | 2.4840 / 3.8644 / 0.0449  |
| 2023 validation   | half_ppr | RB       |         1471 | 2.8337 / 4.2329 / 0.3575   | 2.5464 / 3.9645 / 0.1860  |
| 2023 validation   | half_ppr | TE       |         1186 | 2.0384 / 3.1219 / 0.1903   | 1.8197 / 2.8779 / -0.0830 |
| 2023 validation   | half_ppr | WR       |         2474 | 2.8960 / 4.3500 / 0.0803   | 2.7654 / 4.2018 / 0.0223  |
| 2023 validation   | ppr      | ALL      |         5131 | 2.8307 / 4.2845 / 0.1755   | 2.6810 / 4.1208 / 0.0482  |
| 2023 validation   | ppr      | RB       |         1471 | 2.8802 / 4.3113 / 0.3489   | 2.6116 / 4.0569 / 0.1946  |
| 2023 validation   | ppr      | TE       |         1186 | 2.1854 / 3.3369 / 0.1427   | 2.0370 / 3.1383 / -0.1030 |
| 2023 validation   | ppr      | WR       |         2474 | 3.1107 / 4.6564 / 0.0881   | 3.0310 / 4.5509 / 0.0335  |
| 2024–2025 holdout | standard | ALL      |        10531 | 2.4822 / 3.8428 / 0.0079   | 2.2520 / 3.6414 / -0.0809 |
| 2024–2025 holdout | standard | RB       |         3091 | 2.7696 / 4.3293 / -0.0776  | 2.5067 / 4.1564 / -0.1529 |
| 2024–2025 holdout | standard | TE       |         2506 | 1.9892 / 3.0723 / 0.0987   | 1.6938 / 2.7901 / -0.1818 |
| 2024–2025 holdout | standard | WR       |         4934 | 2.5527 / 3.8708 / 0.0153   | 2.3761 / 3.6775 / 0.0154  |
| 2024–2025 holdout | half_ppr | ALL      |        10531 | 2.5951 / 4.0117 / -0.0090  | 2.4156 / 3.8473 / -0.0889 |
| 2024–2025 holdout | half_ppr | RB       |         3091 | 2.8047 / 4.3848 / -0.0965  | 2.5487 / 4.2155 / -0.1573 |
| 2024–2025 holdout | half_ppr | TE       |         2506 | 2.0832 / 3.2415 / 0.0348   | 1.8712 / 3.0115 / -0.2193 |
| 2024–2025 holdout | half_ppr | WR       |         4934 | 2.7238 / 4.1194 / 0.0237   | 2.6088 / 3.9816 / 0.0202  |
| 2024–2025 holdout | ppr      | ALL      |        10531 | 2.7371 / 4.2171 / -0.0258  | 2.5995 / 4.0819 / -0.0968 |
| 2024–2025 holdout | ppr      | RB       |         3091 | 2.8490 / 4.4572 / -0.1154  | 2.6047 / 4.2912 / -0.1618 |
| 2024–2025 holdout | ppr      | TE       |         2506 | 2.2151 / 3.4508 / -0.0292  | 2.0757 / 3.2640 / -0.2568 |
| 2024–2025 holdout | ppr      | WR       |         4934 | 2.9321 / 4.4117 / 0.0321   | 2.8622 / 4.3145 / 0.0251  |

MAE/RMSE are points per player-game. Bias is prediction minus actual. Holdout
RMSE reductions: standard 5.24%, half-PPR 4.10%, PPR 3.21%. Every reported position
also improved RMSE. The eligible sample includes verified zero-opportunity box-score
rows; these results are not a high-volume-starter-only evaluation.

### Coverage and primary exclusions

| Season | Position | Eligible / box-score rows | Coverage | Excluded by primary reason                                |
| ------ | -------- | ------------------------: | -------: | --------------------------------------------------------- |
| 2019   | ALL      |               4813 / 4822 |   99.81% | outcome reconciliation: 8; missing context: 1             |
| 2019   | RB       |               1443 / 1445 |   99.86% | outcome reconciliation: 2                                 |
| 2019   | TE       |               1120 / 1121 |   99.91% | outcome reconciliation: 1                                 |
| 2019   | WR       |               2250 / 2256 |   99.73% | outcome reconciliation: 5; missing context: 1             |
| 2020   | ALL      |               4912 / 4921 |   99.82% | missing context: 1; outcome reconciliation: 8             |
| 2020   | RB       |               1468 / 1472 |   99.73% | outcome reconciliation: 4                                 |
| 2020   | TE       |               1144 / 1144 |  100.00% | none                                                      |
| 2020   | WR       |               2300 / 2305 |   99.78% | missing context: 1; outcome reconciliation: 4             |
| 2021   | ALL      |               5194 / 5211 |   99.67% | outcome reconciliation: 16; missing context: 1            |
| 2021   | RB       |               1513 / 1521 |   99.47% | outcome reconciliation: 8                                 |
| 2021   | TE       |               1206 / 1207 |   99.92% | outcome reconciliation: 1                                 |
| 2021   | WR       |               2475 / 2483 |   99.68% | missing context: 1; outcome reconciliation: 7             |
| 2022   | ALL      |               5157 / 5175 |   99.65% | outcome reconciliation: 18                                |
| 2022   | RB       |               1575 / 1581 |   99.62% | outcome reconciliation: 6                                 |
| 2022   | TE       |               1205 / 1207 |   99.83% | outcome reconciliation: 2                                 |
| 2022   | WR       |               2377 / 2387 |   99.58% | outcome reconciliation: 10                                |
| 2023   | ALL      |               5131 / 5138 |   99.86% | outcome reconciliation: 7                                 |
| 2023   | RB       |               1471 / 1474 |   99.80% | outcome reconciliation: 3                                 |
| 2023   | TE       |               1186 / 1188 |   99.83% | outcome reconciliation: 2                                 |
| 2023   | WR       |               2474 / 2476 |   99.92% | outcome reconciliation: 2                                 |
| 2024   | ALL      |               5175 / 5200 |   99.52% | outcome reconciliation: 24; opportunity reconciliation: 1 |
| 2024   | RB       |               1524 / 1536 |   99.22% | outcome reconciliation: 11; opportunity reconciliation: 1 |
| 2024   | TE       |               1220 / 1223 |   99.75% | outcome reconciliation: 3                                 |
| 2024   | WR       |               2431 / 2441 |   99.59% | outcome reconciliation: 10                                |
| 2025   | ALL      |               5356 / 5373 |   99.68% | outcome reconciliation: 17                                |
| 2025   | RB       |               1567 / 1575 |   99.49% | outcome reconciliation: 8                                 |
| 2025   | TE       |               1286 / 1287 |   99.92% | outcome reconciliation: 1                                 |
| 2025   | WR       |               2503 / 2511 |   99.68% | outcome reconciliation: 8                                 |

Across 2019–2025, 102 player-games were excluded: 98 scoring-outcome attribution
mismatches, three missing-context rows and one opportunity-count mismatch. Training
excluded 53, validation seven, holdout 42. These are not all necessarily laterals;
the general reconciliation rule detects any unsupported attribution, with no named exceptions.

### Paired game-cluster uncertainty

95% bootstrap interval for baseline MSE minus context MSE, in squared points;
positive favors context. These are aggregate evaluation intervals, not individual-player
expected-point intervals.

| Period            | Standard       | Half-PPR       | PPR            |
| ----------------- | -------------- | -------------- | -------------- |
| 2023 validation   | 1.463 to 2.221 | 1.218 to 1.973 | 1.023 to 1.753 |
| 2024–2025 holdout | 1.263 to 1.784 | 1.050 to 1.561 | 0.889 to 1.399 |

Historical receipts record source URL, retrieval timestamp, provider file modification
and SHA-256. Inputs are current corrected public files, not preserved historical
as-of-game releases. Exact provider publication latency is unknown. Source dictionaries:
[nflverse play-by-play](https://raw.githubusercontent.com/nflverse/nflreadr/main/data-raw/dictionary_pbp.csv)
and [weekly player stats](https://nflreadr.nflverse.com/reference/load_player_stats.html).
