# Receiving baseline v1 — experimental

One retrospective comparison; no repeated test-set tuning. See protocol.md for the
criteria recorded before results, policy.toml for all parameters, freeze.json for
method/protocol identity and timestamp. calibrated-model.json preserves the priors
and interval radii sealed BEFORE benchmark; model.json adds only gate decisions.
benchmark.json contains aggregate paired errors, coverage, missing outcomes, intervals
and public source receipts. Raw inputs, player-game benchmark records, environments,
private data are excluded from Git. The first genuine prospective archive is copied
byte-for-byte here as first-prospective.json; later working archives remain local.

Compatible experiment source is versioned with this directory. No 2026 fitting or
interval calibration. These 2024–2025 seasons were already inspected and current
corrected files lack historical publication-time receipts; this is a retrospective
event-chronology benchmark. Fixed gates failed. Never call it untouched/prospective
validation or regenerate this experiment to tune against its consumed benchmark.

Current forecast generation (manual, dedicated cache and no forecast worker):

```sh
.venv-fantasy/bin/python -m fantasy.receiving_data
```

This fetches only public receiving stats/team stats/schedule and archives actual
pre-kickoff versions under .fantasy-cache/receiving/forecasts/. Receipt capture,
generation cutoff and immutable write time precede captured kickoff. Method/model
hashes identify versions; latest.json only points to the newest saved archive.
--reuse-saved retains original source receipt timestamps and is visibly stale when
old; it never claims a fresh download. API reload is read-only. Later generation
refreshes numeric outcomes for comparison with preserved browser-local calls.
Historical benchmark horizon is T24; live cutoffs vary and are shown per row.
No injury model, confirmed availability, future roster, route, TD or fantasy-point
forecasts. Later local archive files require backups; the initial pre-kickoff receipt is
preserved here and checksummed. Neither raw datasets nor actual-outcome rows are included.

SHA256SUMS verifies exact bundle bytes. freeze protocol_sha256 is canonical JSON of
the original protocol text; model_sha values use canonical JSON. Source receipt
sha256 values identify raw public source bytes. Do not confuse these conventions.
