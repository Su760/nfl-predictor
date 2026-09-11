# V2 challenger recovery

`ops/season_rebuild.py` rebuilds the exact 42-column V2 football feature vector from free
nflverse schedules and play-by-play. It writes raw captures, derived target-safe inputs,
feature rows, and evaluation reports only beneath the private root configured in
`configs/season_rebuild.toml`.

The captures are current historical reconstructions. They do not prove when the source rows
were first available, so every reconstructed fact and snapshot is Grade C. The report is
exploratory, non-confirmatory, and never promotion eligible. The command does not create a
champion, production registry, or deployable artifact.

Run incrementally; completed season captures are immutable and reused. Each capture invocation
downloads at most `capture_seasons_per_run` seasons so recovery can resume after interruption:

```console
uv run python ops/season_rebuild.py capture
uv run python ops/season_rebuild.py build
uv run python ops/season_rebuild.py evaluate
uv run python ops/season_rebuild.py evaluate-if-changed
```

`evaluate-if-changed` is safe for a periodic local scheduler: it performs no capture or network
access and never promotes a model. An unchanged dataset, evaluator, lock/model policy, and config
reuse the existing immutable evaluation. Any changed identity creates a new content-addressed
record under `evaluations/records`; `evaluations/index` maps the stable experiment signature to
that record. The configured report remains an atomically replaced latest-view compatibility file.
Builds likewise retain each JSONL dataset payload under `datasets/objects` before updating the
configured dataset view.

The final comparison uses identical 2025 regular-season games for the Elo baseline and the
EPA logistic challenger. Estimator fitting uses seasons through 2023, calibration uses 2024,
and 2025 remains untouched until final evaluation. Calibrator-family selection uses only
completed earlier outer folds. Ties are modeled separately and all headline scores are
three-way log loss, multiclass Brier score, and straight-up accuracy.

For each target, historical game and play inputs are limited to games on earlier calendar days.
This conservative reconstruction rule excludes every same-day game, including earlier kickoffs,
because the current capture cannot prove its historical final publication time. That missing
publication proof is another reason the evidence remains Grade C.

The production venue table begins in 2026 and cannot represent historical provider stadium IDs.
For reconstruction only, surface and roof come from each captured historical schedule row and
altitude comes from the explicit stadium-ID map in the rebuild config. Neutral-site and unmapped
venue rows are excluded, recorded in the coverage report, and never imputed. The historical map
is itself reconstructed evidence and does not raise the dataset above Grade C.

Target selections are compact JSON manifests with per-target feature-row checkpoints. Each binds its parent raw hashes, eligible game
IDs, transformation schema, and the exact in-memory Arrow payload hash consumed by the existing
normalizer. The build does not duplicate growing play-by-play prefixes on disk.

For a bounded recovery probe, pass `--max-games 1` to `build` (or set `max_games` in a private
config). Such a partial dataset cannot run the final holdout evaluation unless it still includes
all required estimator, calibration, and holdout seasons.

Promotion requires separately sourced archive evidence with real publication timestamps or
prospective Grade A forecasts meeting `evaluation_policy_v1.toml`. Do not relabel these files
or edit their timestamps to satisfy that gate.

## Executed evaluation (2026-09-11)

The private full rebuild produced 2,593 exact-42 rows (46 unsupported-venue exclusions).
The 2025 final holdout contains 265 matched games. Elo log loss 0.661164 / Brier 0.452788
beat EPA logistic log loss 0.694553 / Brier 0.484184. The challenger is rejected.
Capture-time provenance remains Grade C; no registry entry or production artifact was created.

Local `com.su760.nfl-candidate-evaluation` launchd job runs `evaluate-if-changed` at login and every `evaluation_check_seconds` (7,200 seconds). It reads the existing private dataset only; no downloads, paid calls, retraining on new weekly outcomes, registry writes or automatic promotion. Logs are private under the research data root. An unchanged experiment is verified and reused. New data/code/config causes one new archived evaluation; changing the final holdout requires an explicit research decision.
