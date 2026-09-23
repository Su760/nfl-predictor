# Football insights and historical evaluation — September 16, 2026

Implemented on the V2 branch. Production Elo, its policy, shadow coefficients, and all historical forecasts are unchanged. No wagering, paid API, subscription, audio/video, deployment, or automatic promotion was added. The delivery report records the final commit.

## Dashboard and operation

Running dashboard: http://127.0.0.1:8510/ . Existing LaunchAgent `com.su760.nfl-week1-viewer` was restarted and verified.

- `/`: saved probabilities plus a two-sided, margin-removed market comparison, observation/prediction times, source and model-input disclosure. Team names link to team detail.
- `/rankings`: genuine production Elo ratings, weekly rank/rating movement, sortable offensive/defensive EPA, sample counts and before-week selection.
- `/teams/BUF` (or another team code): season-to-date versus last four completed games; offensive/defensive EPA, neutral and early-down dropback rates, explosive rate, samples, recent scores and next matchup.
- `/performance`: existing issued-forecast scorecards remain separate from reconstructed historical results; season/cutoff/model filters, matched baselines, coverage, proper scores, calibration counts/Wilson intervals, paired experiment intervals and provenance.
- Game details retain original forecasts, revision reasons and postgame reviews; measured model inputs are distinguished from descriptive tendencies.

If the existing viewer is stopped, launch from the V2 worktree:

```sh
cd /path/to/nfl-predictor-v2/nfl-predictor
.venv/bin/python ops/week1_viewer.py
```

Do not start another process while port8510 is occupied. Rankings/results/market receipts follow the existing live worker. PBP refresh is separate and explicit; no hidden network call occurs when a page loads:

```sh
curl -fLsS --max-time 60 https://github.com/nflverse/nflverse-data/releases/download/pbp/play_by_play_2026.parquet -o /tmp/nfl-insights-pbp-2026.parquet
.venv/bin/python ops/season_insights.py --capture-pbp /tmp/nfl-insights-pbp-2026.parquet
```

The capture is content-addressed under `~/nfl-predictor-live-data/insights/raw`, with an immutable receipt. Current capture:16 games, captured2026-09-16T18:50:11.800041Z, SHA256 `e7567f9931b02ddafca608ff91ca8f6443a864a1a0d18aef4abe3f4fc49562bc`. Unknown provider publication time is explicit. Later missing PBP is not synthesized; compare games-with-PBP against completed games. One-game samples are flagged.

Information organization was inspired by [nfelo](https://www.nfeloapp.com/), retaining our existing design and explanations. No performance comparison with nfelo is claimed. Public PBP access follows [nflverse's documented dataset](https://nflreadr.nflverse.com/reference/load_pbp.html).

## Historical protocol and actual results

Frozen final run: `7efc6e5292677068bd023cc588a238266844f23ef306c86ff20c691d6eca9b19`, declared2026-09-16T19:20:34.392054Z. Private artifacts: `insights/declarations/<run>.json`, `evaluations/<run>.json`, `predictions/<run>.json`; `evaluation.json` is the dashboard pointer. Reproduce with `.venv/bin/python ops/season_evaluation.py`. Repeated successful runs reproduced all6284 stored model/game/horizon rows exactly; output versions retain earlier reports.

Existing machinery reused: `season_probability` Elo replay and proper scores, `season_rebuild` cached corrected football features and chronological fold, `EpaLogisticModel` training-only StandardScaler/logistic pipeline, and sigmoid calibration. Fixed parameters come from existing policies. No new ensemble, margin model, market-informed challenger, hyperparameter search or production model was introduced.

For each evaluated season T, estimator seasons end T−2; calibration uses T−1 non-ties; results from T are scored after predictions. Expanding training is compared with the last three estimator seasons; calibration period is identical. Four existing EPA feature differences are offense, defense, passing and rushing; variants remove only the separate passing/rushing indicator, so aggregate offensive EPA still includes those plays. Tie probabilities use earlier seasons only. Every paired comparison uses identical game IDs and cutoffs. Games, not revisions, are counted.

All2016–2025 seasons have already influenced development. 2021–2024 are reused validation/research and2025 is a previously inspected benchmark. None is an untouched historical holdout. Future predictions after the declaration can provide prospective evidence; already observed Week1 cannot.

The cached reconstruction selected preceding calendar days, not true prediction horizons (`ops/season_rebuild.py:480`); the new evaluator audits every selected game's lineage and rejects availability at/after kickoff minus horizon. Historical final availability uses the existing conservative kickoff+24h proxy. It cannot prove the historical publication time of corrected PBP or its EPA provider model. Consequently all results are grade-C reconstructed research, never predictions claimed to have been issued live.

### T−60m: matched sample

985/1359 source-finalized games covered across2021–2025;374 excluded:348 violate the conservative cutoff and26 have no cached feature row. Each model has the same985 games,982 non-ties and3 ties. The2022 frozen source contains271 finalized games; other years272. Coverage is selective, so these are not full-schedule estimates.

| Model | Correct / non-ties | Accuracy | Brier (0–2) | Log loss |
|---|---:|---:|---:|---:|
| Production / simple Elo |622/982|63.34%|0.449683|0.655371|
| Existing football EPA |612/982|62.32%|0.460724|0.669221|
| EPA without passing indicator |610/982|62.12%|0.462287|0.670788|
| EPA without rushing indicator |625/982|63.65%|0.459486|0.667707|
| EPA, recent3 estimator seasons |612/982|62.32%|0.462058|0.670283|

Brier and log loss include all three outcomes. Accuracy excludes ties; missing predictions do not count as wrong. Lower proper scores are better. Production is already the simple football-only Elo baseline; do not count it twice as independent evidence. No production market input is used.

| Season | Matched / source games | Elo correct / non-ties | Elo Brier | Elo log loss | EPA correct / non-ties | EPA Brier | EPA log loss |
|---|---:|---:|---:|---:|---:|---:|---:|
|2021|204/272|118/203|0.471238|0.686222|128/203|0.458752|0.674778|
|2022|203/271|127/201|0.462209|0.692735|121/201|0.474917|0.709207|
|2023|193/272|118/193|0.457755|0.655910|125/193|0.456217|0.652169|
|2024|194/272|133/194|0.431380|0.623507|119/194|0.446694|0.640979|
|2025|191/272|126/191|0.423780|0.614529|119/191|0.466548|0.666705|

The dashboard also reports larger, separate full-schedule Elo coverage:1359 games, log loss0.663456 atT60. It is not mixed into the985-game matched comparison.

### T−72h

Full-schedule Elo:848/1355 non-ties correct (62.58%),1359 scored,4 ties, Brier0.456766, log loss0.663496. Coverage1359/1359 finalized source games.

Football comparison unavailable: only two cached rows survive per recent2022–2025 season, with one-class calibration in some years. The attempted fit failed explicitly (`ValueError: sigmoid calibration requires both binary classes`). We did not weaken cutoffs or change calibration to force a result. Earlier failed declarations and explicit `run_status` failure records are retained; no partial scores were published as completed evaluations.

### Bounded experiment findings

Changes below are variant minus complete EPA, with95% paired season/week-cluster bootstrap intervals (1000 replicates, seed42). They are exploratory, without multiplicity correction.

| Experiment | Overall log-loss change |95% interval| Interpretation |
|---|---:|---|---|
|Remove separate passing EPA|+0.001567|[-0.000133, +0.003270]|Mixed; removal worse in3/5 seasons; benefit unsupported.|
|Remove separate rushing EPA|−0.001514|[-0.002996, +0.000055]|Small improvement in5/5 seasons, but interval includes zero; promising, inconclusive.|
|Recent3 versus expanding estimator history|+0.001061|[-0.001853, +0.004191]|Mixed; recent-only worse in3/5 seasons; no consistent benefit.|

Elo versus complete EPA: log-loss change−0.013850, interval[-0.030508,+0.001666]. Elo has better observed aggregate scores, but superiority is not established. No variant meets promotion criteria from these reconstructed comparisons. Existing QB shadows continue prospectively; target-game actual starters were not used as historical expected-starter evidence. Weather, injury and QB feature removals were not added because this EPA model does not use those inputs.

## Ratings, tendencies and market provenance

Week2 rankings reproduce all32 saved production ratings exactly (maximum absolute difference0). Week1 uses regressed previous-season ratings only; later weeks apply only known final results from earlier weeks. Result corrections observed after a selected historical as-of time are excluded. Positive rank change means upward movement; equal ratings are ordered by team code. No probability-to-spread conversion exists.

PBP filters: official pass/run plays, finite EPA, no kneels/spikes. Dropbacks include sacks and scrambles. Neutral situations use quarters1–3 and absolute score differential≤7; early down means1st/2nd. Explosive means≥20 yards on a dropback or≥10 on a run. Rates show their play denominators; recent is last4 completed games. Higher offensive EPA and lower defensive EPA allowed are better; descriptive pass rates have no universal better direction. EPA is unadjusted for opponent. Corrected current PBP describes earlier completed games, not their as-issued pregame inputs.

Audited290 archived ESPN scoreboard receipts. For each saved selected forecast, choose the latest compatible paired quote captured at/before publication within the existing7200-second freshness limit. Selection never examines the game outcome or disagreement size. Both sides must come from one provider, one response object and the same moneyline quote field; provider aliases are normalized using the existing configuration. Raw hashes are verified. Current later observations, if used as fallback, are explicitly labeled later than the forecast; post-kickoff captures are rejected.

Current coverage:14/16 Week1 games,16/16 Week2. NE@SEA has no production pregame forecast; SF@LA's selected legacy forecast predates the available ESPN capture archive. Example BUF@HOU: price captured2026-09-13T16:55:49.698092Z; forecast published16:55:53.027070Z. These recovered observations are descriptive comparisons, not a retrospective live paper-betting record.

American odds convert to raw implied probability: +a →100/(a+100); −a →a/(a+100). Normalize both sides by their sum to remove margin proportionally. Compare to model home probability conditional on no tie; retain unconditional model win/tie probabilities separately. Source/provider, offered prices, capture time and prediction time are displayed. Independent provider update timestamps and contract/settlement terms are unknown; the comparison is explicitly not an executable guaranteed edge. There are no audited2016–2025 paired pre-cutoff moneylines, so the historical market-only baseline and market-informed challenger remain unavailable. Closing odds are not substituted.

## Verification and remaining work

-36 focused tests pass, including cutoff equality/unknown history, training/calibration/test separation, future-label invariance, duplicate revision rejection, sample/cutoff matching, ties, odds conversion/pairing/timing/provider aliases, outcome corrections, ranking preservation, tendency denominators, TBD matchups and immutable archived-price selection.
-Desktop1440px and mobile390px: actual route navigation, sorting, before-week filters, season/horizon/calibration filters, market timing, no horizontal document overflow or JavaScript errors, and insights503 fallback verified. Rendered screenshots inspected.
-Changed-file Ruff, JavaScript syntax and diff checks pass; mypy reports no issues in81 source files.
-13,380 production/shadow forecast files and receipts match the prior manifest byte-for-byte. Weekly live score remains9/15 correct,15/16coverage; new views do not rescore or rewrite predictions.
-Full suite:1243 passed,42 failed,1 skipped. The42 inherited failures exactly match the independently tested baseline failure names. Example: `tests/runtime/test_services.py:852`: `AssertionError: assert 'MISSED' == 'FAILED'`. The shared fixed kickoff at `tests/workflows/test_forecast.py:50` is now past, while durable publication uses real filesystem ctime at `src/nfl_predictor/workflows/forecast.py:1946`. This adjacent fixture-clock repair is not included. One optional captured-source test is skipped when its external fixture is absent. No full-green claim.

Single next milestone: rebuild native T72/T60 football feature snapshots from eligible completed-game inputs, with explicit historical availability proxies/receipts, to recover the374 excluded T60 games and make T72 football calibration possible. Freeze the same bounded ablations before evaluating recovered coverage; keep future prospective shadows independent and production unchanged until established promotion criteria are met.

Final read-only audit: all2593 cached rows match the frozen schedule kickoff/results and PBP versus schedule selection IDs. Viewer health OK; worker HEALTHY/source FRESH/paid usage0. Source check2026-09-16T18:00:29.158200Z, next20:00:29.158200Z. Final independent-baseline failure-name comparison is exact.

## Verified reporting update — September 21, 2026

The dashboard now separates the legacy T−60m subset (985/1,359 games covered, 622/982 correct,
63.34%, Brier 0.449683, log loss 0.655371, three ties, 374 exclusions) from cutoff-native full
coverage (1,359/1,359 games, 848/1,355 correct, 62.58%, four ties, zero feature exclusions).
Full-coverage T−60m Brier/log loss are 0.456735/0.663456; T−72h are 0.456766/0.663496.
Cutoff-native Weeks 1–2 contain 160 games, 159 non-ties and one tie at each horizon. Production Elo
is 95/159 (59.75%, 95% Wilson interval 51.98–67.05%), Brier 0.477906, log loss 0.696866.
The complete identical-game model table is in `2026-09-16-cutoff-native.md` and the dashboard.

Live issued forecasts remain a separate scorecard and are never regenerated for scoring. As of
`2026-09-21T23:42:54Z`, with `2026_02_NYG_LA` pending:

|Week|Scorecard|Correct / settled|Saved coverage|Brier|Log loss|
|---|---|---:|---:|---:|---:|
|1|Original official|9/15|15/16|0.470477|0.666764|
|1|T−72h|1/2|2/16|0.537477|0.735309|
|1|T−60m|3/6|6/16|0.550107|0.756665|
|1|FINAL|9/14|14/16|0.457547|0.653653|
|2|Official|9/15|16/16, one pending|0.512843|0.713838|
|2|T−72h|9/15|16/16, one pending|0.512843|0.713838|
|2|T−60m|9/15|16/16, one pending|0.512843|0.713838|
|2|FINAL|9/15|15/16; pending game had no FINAL forecast|0.512843|0.713838|

Accuracy excludes ties; proper scores include them. Pending games are excluded from settled metrics.
The reconstruction still assumes historical results became available at kickoff+24h because original
publication receipts do not exist; corrected PBP/provider EPA may include later revisions. No closing
odds, final-season target statistics, target-game QB/injury/weather data, or post-cutoff games enter
the four reconstructed EPA features. Production Elo and archived forecasts remain unchanged.

## Runtime receipt-clock repair and live audit — September 23, 2026

The inherited 42-test set had one verified causal chain, not 42 independent runtime defects. The
forecast fixtures use a fixed September 13 kickoff (`tests/workflows/test_forecast.py`), while a
new terminal receipt marker previously always read the host filesystem's real `st_ctime_ns`
(`src/nfl_predictor/workflows/forecast.py`). On September 23, an otherwise on-time fixture therefore
looked ten days late. The durable workflow correctly replaced its intended success or capture failure
with `MISSED`; outcome fixtures then imported that empty terminal graph, so predictions, quotes and
score inputs were absent. A diagnostic metadata reader tied to fixture time made all 239 pre-change
tests in the three affected modules pass, confirming there was no second cause in the saved
1 runtime / 22 forecast / 19 outcome failure fingerprint.

`DurableForecastRepository` now accepts a narrow receipt-marker stat reader. Its production default
is unchanged: read the linked marker's real filesystem `st_ctime_ns`, fail closed if it is unreadable
or invalid, and compare it with the live deadline. Fixed-date test builders inject metadata derived
from the same logical clock used for candidate durability and receipt durability. Focused regressions
accept an on-time receipt and reject a receipt one microsecond late. The existing real-link tests still
reject late ctime when mtime is backdated, reject coarse filesystem ticks crossing a fractional close,
and reject a receipt whose final durability observation crosses the deadline. Timestamp enforcement
and portable immutable publication evidence were not weakened.

Validation: the affected runtime/forecast/outcome modules pass 241 tests. The full suite passes
1,306 with one optional real-capture test skipped because its external fixture is unavailable; there
are no remaining failures. Focused Ruff passes.

Read-only live audit at 2026-09-23T07:19Z: the private worker is running under the documented
`caffeinate` LaunchAgent with a current healthy heartbeat. Its latest completed source/forecast cycle
is 2026-09-23T05:27:35Z and the next scheduled cycle is 07:27:35Z, so no manual refresh was required.
ESPN schedule/results, nflverse games/history, Week 3 injuries and depth data were captured in that
cycle. Official inactives remain explicitly `MISSING`: the two discovered Week 2 articles fail the
strict team/game parser and are not retroactively applied.

All 16 Week 2 games are now final. Saved official, T72, T60 and FINAL scorecards each have 16/16
coverage and 10/16 correct (62.5%); no completed prediction was regenerated. Week 3 has 16/16 saved
official forecasts, 1/16 completed T72 snapshots (ATL@GB), and 0/16 T60 or FINAL snapshots because
those windows are not yet due. The remaining T72 targets fall between September 24 and September 26.
No Week 3 cutoff is already missed. Production Elo, archived forecasts and all historical evaluation
artifacts remain unchanged.
