# Frozen expected-points experiment — bundle v1

This bundle preserves the experiment completed October 1, 2026. Training: 2019–2022;
validation: 2023; consumed final holdout: 2024–2025. 2026 was excluded from fitting.
No refit, evaluation or gate change is part of preservation.

## Restore

Use this repository at the preservation commit or a compatible checkout, with its
isolated fantasy dependencies installed (`fantasy/requirements.txt`). From the root:

```sh
(cd artifacts/fantasy/expected-points/v1 && shasum -a 256 -c SHA256SUMS)
.venv-fantasy/bin/python -m fantasy.restore
```

For an isolated restoration test, pass `--cache-dir /tmp/fantasy-restored-v1`.
The command checks all bundle bytes, repository method/config hashes, and the existing
API's sealed-model integrity and saved-report acceptance checks BEFORE writing.
It restores only model.json, freeze.json, validation.json and holdout.json beneath
<cache-dir>/expected-points. Identical existing files are left untouched; different or
partial existing artifacts are refused. It does not download data, fit, calculate
predictions, bootstrap, reevaluate holdout games, or start any server/worker.
Receipt files remain in this versioned bundle and embedded in the sealed reports.
A changed method/config requires the compatible experiment code; do not rewrite the
seal or adjust acceptance gates to make restoration pass.

The restored model can pass API integrity checks without raw historical data.
Current-season UI rows require a separately captured local snapshot; restoration does
not create one. Reuse the existing fantasy preview at http://127.0.0.1:8520/fantasy.

## Integrity and provenance

SHA256SUMS hashes exact FILE BYTES, including manifest.json. The manifest lists raw
file hashes/sizes and repository compatibility hashes; sealed_model_sha256 and
sealed_validation_sha256 instead identify canonical JSON objects using the original
fingerprint function. These are different hash conventions, not mismatched artifacts.
Original JSON artifacts and all 14 public receipt files are copied byte-for-byte.
The source-byte hashes in receipts were verified against locally saved raw inputs at
packaging; the large raw datasets are deliberately absent. Public source files may
later be revised, so a future download is not guaranteed to match the receipts.
Retrieval timestamps are local captures; provider_modified_at is an HTTP modification
time, not a verified source publication time or historical as-of-game availability.

Included: numeric context-cell means/counts, frozen policy/decision, aggregate reports
and coverage/exclusions, public source receipts, scoring-policy copy, checksum manifest.
Excluded: raw datasets/player-game records, private data, secrets, environments, caches,
logs, current-season snapshots, browser watchlists and all NFL forecast archives.
Checksums detect changes relative to the committed bundle; Git supplies its versioned
identity. They are not an independent cryptographic signature.

Full scoring exclusions, evaluation tables and limitations are recorded in
../../../../docs/runbooks/fantasy-expected-points.md (repository-relative path).
