from __future__ import annotations

import json
import os
import stat
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import UTC, datetime

import pytest

import nfl_predictor.markets.budget as budget_module
from nfl_predictor.markets.budget import (
    BudgetExceeded,
    BudgetState,
    CompareAndSwapBudgetRepository,
    FileLockBudgetRepository,
    InMemoryBudgetRepository,
    OddsBudget,
    Reservation,
    project_worst_case,
)


def test_uncertain_request_consumes_reservation_and_hard_stop_is_400() -> None:
    budget = OddsBudget(InMemoryBudgetRepository(), hard_stop=400)
    budget.record_authoritative_usage(month="2026-09", used=399, remaining=101)
    reservation = budget.reserve(month="2026-09", request_id="required-t72", credits=1)
    budget.finalize_uncertain(reservation.reservation_id)

    with pytest.raises(BudgetExceeded):
        budget.reserve(month="2026-09", request_id="another", credits=1)


def test_reservation_is_idempotent_but_conflicting_reuse_fails_closed() -> None:
    budget = OddsBudget(InMemoryBudgetRepository(), hard_stop=400)

    first = budget.reserve("2026-09", "same-request", 2)
    second = budget.reserve("2026-09", "same-request", 2)

    assert second == first
    with pytest.raises(ValueError, match="different reservation"):
        budget.reserve("2026-09", "same-request", 3)


def test_authoritative_reconciliation_moves_up_but_never_below_consumed_or_reserved() -> None:
    budget = OddsBudget(InMemoryBudgetRepository(), hard_stop=400)
    reservation = budget.reserve("2026-09", "request", 3)
    budget.finalize_uncertain(reservation.reservation_id)
    budget.record_authoritative_usage("2026-09", used=1, remaining=499)
    budget.reserve("2026-09", "open", 2)

    state = budget.state("2026-09")

    assert state.authoritative_used == 3
    assert state.open_reserved == 2
    assert state.projected_used == 5


def test_file_lock_repository_serializes_concurrent_hard_stop_reservations(tmp_path) -> None:
    path = tmp_path / "budget.json"

    def reserve(index: int) -> bool:
        budget = OddsBudget(FileLockBudgetRepository(path), hard_stop=10)
        try:
            budget.reserve("2026-09", f"request-{index}", 1)
        except BudgetExceeded:
            return False
        return True

    with ThreadPoolExecutor(max_workers=20) as pool:
        accepted = list(pool.map(reserve, range(20)))

    assert sum(accepted) == 10
    assert OddsBudget(FileLockBudgetRepository(path), 10).state("2026-09").open_reserved == 10


class _CasStore:
    def __init__(self) -> None:
        self.state = BudgetState.empty().for_month("2026-09")
        self.revision = "0"
        self.fail_first = True

    def read(self):
        return self.revision, self.state

    def compare_and_swap(self, expected_revision: str, state: BudgetState) -> bool:
        if self.fail_first:
            self.fail_first = False
            self.state = replace(self.state, authoritative_used=1)
            self.revision = "1"
            return False
        if expected_revision != self.revision:
            return False
        self.state = state
        self.revision = str(int(self.revision) + 1)
        return True


def test_compare_and_swap_adapter_retries_against_latest_local_state() -> None:
    store = _CasStore()
    budget = OddsBudget(CompareAndSwapBudgetRepository(store), hard_stop=3)

    reservation = budget.reserve("2026-09", "request", 2)

    assert reservation.credits == 2
    assert store.state.projected_used == 3


def test_worst_case_groups_due_time_clusters_and_includes_retries_close_and_changes() -> None:
    same = datetime(2026, 9, 1, 12, tzinfo=UTC)
    later = datetime(2026, 9, 8, 12, tzinfo=UTC)

    assert (
        project_worst_case((same, same, later), include_close=True, schedule_change_calls=3) == 13
    )
    assert project_worst_case((same, same, later), include_close=False) == 8


def test_budget_above_absolute_400_credit_ceiling_is_rejected() -> None:
    with pytest.raises(ValueError, match="400"):
        OddsBudget(InMemoryBudgetRepository(), hard_stop=401)


def test_finalize_success_consumes_without_double_counting_headers_and_is_idempotent() -> None:
    budget = OddsBudget(InMemoryBudgetRepository(), hard_stop=400)
    budget.record_authoritative_usage("2026-09", used=399, remaining=101)
    reservation = budget.reserve_required("2026-09", "t60", 1)

    budget.finalize_success(reservation.reservation_id, "2026-09", used=400, remaining=100, last=1)
    budget.finalize_success(reservation.reservation_id, "2026-09", used=400, remaining=100, last=1)
    state = budget.state("2026-09")

    assert state.authoritative_used == 400
    assert state.authoritative_remaining == 100
    assert state.open_reserved == 0
    assert state.reservations[0].status == "consumed"


def test_finalize_success_conservatively_consumes_cost_when_usage_header_is_stale() -> None:
    budget = OddsBudget(InMemoryBudgetRepository(), hard_stop=400)
    budget.record_authoritative_usage("2026-09", used=399, remaining=101)
    reservation = budget.reserve_required("2026-09", "request-400", 1)

    budget.finalize_success(reservation.reservation_id, "2026-09", used=399, remaining=101, last=1)
    budget.finalize_success(reservation.reservation_id, "2026-09", used=399, remaining=101, last=1)

    state = budget.state("2026-09")
    assert state.authoritative_used == 400
    assert state.reservation_by_id(reservation.reservation_id).status == "consumed"
    with pytest.raises(BudgetExceeded):
        budget.reserve_required("2026-09", "must-stay-blocked", 1)


def test_finalize_success_uses_provider_last_charge_when_it_exceeds_reservation() -> None:
    budget = OddsBudget(InMemoryBudgetRepository(), hard_stop=400)
    budget.record_authoritative_usage("2026-09", used=100, remaining=400)
    reservation = budget.reserve_required("2026-09", "underestimated", 1)

    budget.finalize_success(
        reservation.reservation_id,
        "2026-09",
        used=100,
        remaining=397,
        last=3,
    )
    budget.finalize_success(
        reservation.reservation_id,
        "2026-09",
        used=100,
        remaining=397,
        last=3,
    )

    assert budget.state("2026-09").authoritative_used == 103


def test_optional_close_cannot_consume_last_credit_reserved_for_required_origin() -> None:
    budget = OddsBudget(InMemoryBudgetRepository(), hard_stop=400)
    budget.record_authoritative_usage("2026-09", used=399, remaining=101)

    with pytest.raises(BudgetExceeded):
        budget.reserve_optional_close("2026-09", "close", credits=1, required_credits_remaining=1)

    required = budget.reserve_required("2026-09", "t60", credits=1)
    assert required.priority == "required_origin"


def test_success_finalize_works_through_cas_and_file_lock_repositories(tmp_path) -> None:
    cas_store = _CasStore()
    cas_store.fail_first = False
    cas_budget = OddsBudget(CompareAndSwapBudgetRepository(cas_store), hard_stop=10)
    cas_reservation = cas_budget.reserve_required("2026-09", "cas", 1)
    cas_budget.finalize_success(
        cas_reservation.reservation_id, "2026-09", used=2, remaining=498, last=1
    )

    file_budget = OddsBudget(FileLockBudgetRepository(tmp_path / "budget.json"), hard_stop=10)
    file_reservation = file_budget.reserve_required("2026-09", "file", 1)
    file_budget.finalize_success(
        file_reservation.reservation_id, "2026-09", used=1, remaining=499, last=1
    )

    assert cas_budget.state("2026-09").authoritative_used == 2
    assert file_budget.state("2026-09").authoritative_used == 1


def test_success_overrun_at_401_is_durable_and_permanently_blocks_file_reservations(
    tmp_path,
) -> None:
    state_path = tmp_path / "budget.json"
    budget = OddsBudget(FileLockBudgetRepository(state_path), hard_stop=400)
    budget.record_authoritative_usage("2026-09", used=399, remaining=101)
    reservation = budget.reserve_required("2026-09", "request-400", 1)

    budget.finalize_success(reservation.reservation_id, "2026-09", used=401, remaining=99, last=1)
    reloaded = OddsBudget(FileLockBudgetRepository(state_path), hard_stop=400)

    assert reloaded.state("2026-09").authoritative_used == 401
    assert reloaded.state("2026-09").reservations[0].status == "consumed"
    with pytest.raises(BudgetExceeded):
        reloaded.reserve_required("2026-09", "blocked-required", 1)
    with pytest.raises(BudgetExceeded):
        reloaded.reserve_optional_close("2026-09", "blocked-close", 1, 0)


def test_header_overrun_at_500_preserves_open_reservation_through_cas_and_blocks_more() -> None:
    store = _CasStore()
    store.fail_first = False
    budget = OddsBudget(CompareAndSwapBudgetRepository(store), hard_stop=400)
    open_reservation = budget.reserve_required("2026-09", "already-open", 1)

    budget.record_authoritative_usage("2026-09", used=500, remaining=0)
    reloaded = OddsBudget(CompareAndSwapBudgetRepository(store), hard_stop=400)
    state = reloaded.state("2026-09")

    assert state.authoritative_used == 500
    assert state.reservation_by_id(open_reservation.reservation_id).status == "open"
    with pytest.raises(BudgetExceeded):
        reloaded.reserve_required("2026-09", "blocked-required", 1)
    with pytest.raises(BudgetExceeded):
        reloaded.reserve_optional_close("2026-09", "blocked-close", 1, 0)


@pytest.mark.parametrize(
    "values",
    [
        ("bad", 0, None, ()),
        ("2026-09", -1, None, ()),
        (
            "2026-09",
            0,
            None,
            (
                Reservation("same", "2026-09", "one", 1),
                Reservation("same", "2026-09", "two", 1),
            ),
        ),
        (
            "2026-09",
            0,
            None,
            (Reservation("one", "2026-10", "request", 1),),
        ),
    ],
)
def test_budget_state_rejects_corrupt_invariants(values: tuple[object, ...]) -> None:
    with pytest.raises(ValueError):
        BudgetState(*values)  # type: ignore[arg-type]


def test_budget_state_rejects_authoritative_usage_below_consumed_local_floor() -> None:
    consumed = Reservation(
        "reservation-1",
        "2026-09",
        "request-1",
        2,
        status="consumed",
    )

    with pytest.raises(ValueError, match="consumed"):
        BudgetState("2026-09", 1, 499, (consumed,))


def test_file_repository_rejects_corrupt_loaded_state(tmp_path) -> None:
    state_path = tmp_path / "budget.json"
    state_path.write_text(
        json.dumps(
            {
                "month": "2026-09",
                "authoritative_used": -1,
                "authoritative_remaining": 500,
                "reservations": [],
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError):
        OddsBudget(FileLockBudgetRepository(state_path), 400).state("2026-09")


def test_file_repository_fsyncs_parent_directory_after_atomic_replace(
    tmp_path, monkeypatch
) -> None:
    fsynced_modes: list[int] = []
    real_fsync = os.fsync

    def recording_fsync(file_descriptor: int) -> None:
        fsynced_modes.append(os.fstat(file_descriptor).st_mode)
        real_fsync(file_descriptor)

    monkeypatch.setattr(budget_module.os, "fsync", recording_fsync)
    OddsBudget(FileLockBudgetRepository(tmp_path / "budget.json"), 10).reserve_required(
        "2026-09", "durable", 1
    )

    assert any(stat.S_ISDIR(mode) for mode in fsynced_modes)


@pytest.mark.parametrize("credits", [0, -1, True])
def test_reserve_rejects_non_positive_integer_credits(credits: object) -> None:
    budget = OddsBudget(InMemoryBudgetRepository(), hard_stop=400)
    with pytest.raises((TypeError, ValueError), match="credits"):
        budget.reserve("2026-09", "request", credits)  # type: ignore[arg-type]
