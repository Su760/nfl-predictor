# Task 12B Task 3 Market-Payload Interface Repair Plan

**Goal:** Restore the already-approved Task 2 → Task 3 contract so the production market layer receives the exact immutable normalized quotes needed to build an auditable comparator and candidate decisions.

**Root cause:** `OptionalOddsCapture.capture` stores `MoneylineQuote` objects in `CaptureBundle.records` but replaces them in `CaptureBundle.payload` with only quote IDs and rejection codes. `ForecastWorkflow._evaluate_optional_market` forwards only `market.payload`, so Task 3 cannot access prices, book keys, timestamps, selections, source-event identity, or capture lineage.

**Scope:** This repairs the existing approved interface without adding a provider, repository, callback, workflow change, or new product behavior.

## Locked files

- Modify `src/nfl_predictor/runtime/capture.py`
- Modify `tests/runtime/test_capture.py`

### Task 1: Restore the normalized quote payload boundary

- [x] Add a regression proving a successful optional odds capture returns the exact immutable `tuple[MoneylineQuote, ...]` as `CaptureBundle.payload`, while the records still contain the manifest and the same quotes and no credential/provider-secret detail is exposed.
- [x] Run the focused regression and confirm RED because the current payload is an ID/rejection dictionary.
- [x] Change only the successful `OptionalOddsCapture` return boundary to expose the normalized quote tuple; preserve durable manifest/quote persistence, budget ordering/finalization, safe exception behavior, and record lineage.
- [x] Run `tests/runtime/test_capture.py`, the Task 2 amendment matrix, scoped Ruff, mypy, diff checks, and the full offline suite.
- [x] Obtain an independent review with zero Critical/Important findings; fix only within the two locked files.
- [x] Commit the reviewed repair, push only `codex/nfl-predictor-v2`, and resume the original four-file Task 3 implementation.

## Acceptance behavior

`ForecastWorkflow` passes `CaptureBundle.payload` unchanged to `MarketLayer.evaluate`; after this repair, that payload is the exact normalized quote tuple from the current capture attempt. The Task 3 market layer can therefore build its comparator and both audited decisions without global lookup, replay ambiguity, or another dependency.

All existing authorization gates remain: no live provider calls, secrets, deployment, paid usage, wagers, repository creation, Actions enablement, or push to `main`.
