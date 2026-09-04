from __future__ import annotations

import fcntl
import json
import os
import re
import tempfile
import threading
from collections.abc import Callable, Iterable, Mapping
from dataclasses import asdict, dataclass, replace
from datetime import datetime
from pathlib import Path
from typing import Literal, Protocol, TypeVar
from uuid import uuid4


class BudgetExceeded(RuntimeError):
    pass


ReservationPriority = Literal["required_origin", "optional_close"]
ReservationStatus = Literal["open", "consumed"]


def _validate_month(month: str) -> None:
    if not isinstance(month, str) or re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", month) is None:
        raise ValueError("budget month must use YYYY-MM")


def _validate_nonnegative_integer(value: object, name: str, *, maximum: int | None = None) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{name} must be an integer")
    if value < 0 or (maximum is not None and value > maximum):
        raise ValueError(f"{name} is outside its allowed bounds")
    return value


@dataclass(frozen=True)
class Reservation:
    reservation_id: str
    month: str
    request_id: str
    credits: int
    priority: ReservationPriority = "required_origin"
    status: ReservationStatus = "open"

    def __post_init__(self) -> None:
        if not isinstance(self.reservation_id, str) or not self.reservation_id.strip():
            raise ValueError("reservation ID must be non-blank")
        _validate_month(self.month)
        if not isinstance(self.request_id, str) or not self.request_id.strip():
            raise ValueError("request ID must be non-blank")
        credits = _validate_nonnegative_integer(self.credits, "reservation credits")
        if credits < 1:
            raise ValueError("reservation credits must be positive")
        if self.priority not in {"required_origin", "optional_close"}:
            raise ValueError("reservation priority is invalid")
        if self.status not in {"open", "consumed"}:
            raise ValueError("reservation status is invalid")


@dataclass(frozen=True)
class BudgetState:
    month: str | None
    authoritative_used: int
    authoritative_remaining: int | None
    reservations: tuple[Reservation, ...]

    def __post_init__(self) -> None:
        used = _validate_nonnegative_integer(self.authoritative_used, "authoritative used")
        if self.authoritative_remaining is not None:
            _validate_nonnegative_integer(self.authoritative_remaining, "authoritative remaining")
        if not isinstance(self.reservations, tuple):
            raise TypeError("reservations must be an immutable tuple")
        if any(not isinstance(item, Reservation) for item in self.reservations):
            raise TypeError("budget reservations must be Reservation records")
        if self.month is None:
            if used != 0 or self.authoritative_remaining is not None or self.reservations:
                raise ValueError("unscoped budget state must be empty")
            return
        _validate_month(self.month)
        if any(item.month != self.month for item in self.reservations):
            raise ValueError("reservation month must match budget state month")
        reservation_ids = [item.reservation_id for item in self.reservations]
        request_ids = [item.request_id for item in self.reservations]
        if len(reservation_ids) != len(set(reservation_ids)):
            raise ValueError("reservation IDs must be unique")
        if len(request_ids) != len(set(request_ids)):
            raise ValueError("reservation request IDs must be unique")
        if used < self.consumed_local:
            raise ValueError("authoritative usage cannot be below locally consumed reservations")

    @classmethod
    def empty(cls) -> BudgetState:
        return cls(None, 0, None, ())

    @property
    def open_reserved(self) -> int:
        return sum(item.credits for item in self.reservations if item.status == "open")

    @property
    def consumed_local(self) -> int:
        return sum(item.credits for item in self.reservations if item.status == "consumed")

    @property
    def projected_used(self) -> int:
        return self.authoritative_used + self.open_reserved

    def for_month(self, month: str) -> BudgetState:
        _validate_month(month)
        if self.month is None:
            return replace(self, month=month)
        if self.month != month:
            raise ValueError("budget repository is scoped to one month")
        return self

    def reservation_for(self, request_id: str) -> Reservation | None:
        matches = [item for item in self.reservations if item.request_id == request_id]
        return matches[0] if len(matches) == 1 else None

    def reservation_by_id(self, reservation_id: str) -> Reservation:
        matches = [item for item in self.reservations if item.reservation_id == reservation_id]
        if len(matches) != 1:
            raise KeyError(reservation_id)
        return matches[0]

    def new_reservation(
        self,
        month: str,
        request_id: str,
        credits: int,
        priority: ReservationPriority,
    ) -> Reservation:
        return Reservation(uuid4().hex, month, request_id, credits, priority)

    def with_reservation(self, reservation: Reservation) -> BudgetState:
        return replace(self, reservations=(*self.reservations, reservation))

    def _mark_consumed(self, reservation_id: str) -> tuple[tuple[Reservation, ...], Reservation]:
        reservation = self.reservation_by_id(reservation_id)
        changed = tuple(
            replace(item, status="consumed") if item.reservation_id == reservation_id else item
            for item in self.reservations
        )
        return changed, reservation

    def consume_full_reservation(self, reservation_id: str) -> BudgetState:
        reservation = self.reservation_by_id(reservation_id)
        if reservation.status == "consumed":
            return self
        changed, _ = self._mark_consumed(reservation_id)
        return replace(
            self,
            reservations=changed,
            authoritative_used=self.authoritative_used + reservation.credits,
        )

    def consume_with_authoritative_usage(
        self, reservation_id: str, used: int, remaining: int, last: int
    ) -> BudgetState:
        _validate_nonnegative_integer(used, "authoritative used")
        _validate_nonnegative_integer(remaining, "authoritative remaining")
        _validate_nonnegative_integer(last, "last request usage")
        reservation = self.reservation_by_id(reservation_id)
        changed = self.reservations
        conservative_used = self.authoritative_used
        if reservation.status == "open":
            changed, _ = self._mark_consumed(reservation_id)
            conservative_used += max(reservation.credits, last)
        consumed_floor = sum(item.credits for item in changed if item.status == "consumed")
        return replace(
            self,
            reservations=changed,
            authoritative_used=max(
                self.authoritative_used,
                used,
                consumed_floor,
                conservative_used,
            ),
            authoritative_remaining=remaining,
        )


T = TypeVar("T")
Mutation = Callable[[BudgetState], tuple[BudgetState, T]]


class BudgetRepository(Protocol):
    def transact(self, mutation: Mutation[T]) -> T: ...


class InMemoryBudgetRepository:
    def __init__(self, state: BudgetState | None = None) -> None:
        self._state = state or BudgetState.empty()
        self._lock = threading.Lock()

    def transact(self, mutation: Mutation[T]) -> T:
        with self._lock:
            state, result = mutation(self._state)
            self._state = state
            return result


class FileLockBudgetRepository:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.lock_path = path.with_suffix(path.suffix + ".lock")

    def transact(self, mutation: Mutation[T]) -> T:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.lock_path.open("a+b") as lock:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
            state = _read_state(self.path)
            changed, result = mutation(state)
            _write_state(self.path, changed)
            fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
            return result


class CompareAndSwapStore(Protocol):
    def read(self) -> tuple[str, BudgetState]: ...

    def compare_and_swap(self, expected_revision: str, state: BudgetState) -> bool: ...


class CompareAndSwapBudgetRepository:
    """Local adapter for a worker-supplied Git blob CAS; it performs no remote operations."""

    def __init__(self, store: CompareAndSwapStore, maximum_attempts: int = 20) -> None:
        self.store = store
        self.maximum_attempts = maximum_attempts

    def transact(self, mutation: Mutation[T]) -> T:
        for _ in range(self.maximum_attempts):
            revision, current = self.store.read()
            changed, result = mutation(current)
            if self.store.compare_and_swap(revision, changed):
                return result
        raise RuntimeError("budget compare-and-swap retry limit exceeded")


class OddsBudget:
    def __init__(self, repository: BudgetRepository, hard_stop: int) -> None:
        if isinstance(hard_stop, bool) or not isinstance(hard_stop, int):
            raise TypeError("hard stop must be an integer")
        if hard_stop < 1:
            raise ValueError("hard stop must be positive")
        if hard_stop > 400:
            raise ValueError("hard stop cannot exceed 400 credits")
        self.repository = repository
        self.hard_stop = hard_stop

    def reserve(self, month: str, request_id: str, credits: int) -> Reservation:
        return self.reserve_required(month, request_id, credits)

    def reserve_required(self, month: str, request_id: str, credits: int) -> Reservation:
        return self._reserve(month, request_id, credits, "required_origin", 0)

    def reserve_optional_close(
        self,
        month: str,
        request_id: str,
        credits: int,
        required_credits_remaining: int,
    ) -> Reservation:
        _validate_nonnegative_integer(
            required_credits_remaining, "required credits remaining", maximum=400
        )
        return self._reserve(
            month,
            request_id,
            credits,
            "optional_close",
            required_credits_remaining,
        )

    def _reserve(
        self,
        month: str,
        request_id: str,
        credits: int,
        priority: ReservationPriority,
        required_buffer: int,
    ) -> Reservation:
        if isinstance(credits, bool) or not isinstance(credits, int):
            raise TypeError("credits must be an integer")
        if credits < 1:
            raise ValueError("credits must be positive")

        def mutation(raw_state: BudgetState) -> tuple[BudgetState, Reservation]:
            state = raw_state.for_month(month)
            prior = state.reservation_for(request_id)
            if prior is not None:
                if prior.credits != credits or prior.month != month or prior.priority != priority:
                    raise ValueError("request ID was reused for a different reservation")
                return state, prior
            if state.projected_used + credits + required_buffer > self.hard_stop:
                raise BudgetExceeded(month)
            reservation = state.new_reservation(month, request_id, credits, priority)
            return state.with_reservation(reservation), reservation

        return self.repository.transact(mutation)

    def finalize_uncertain(self, reservation_id: str) -> None:
        self.repository.transact(
            lambda state: (state.consume_full_reservation(reservation_id), None)
        )

    def finalize_success(
        self,
        reservation_id: str,
        month: str,
        used: int,
        remaining: int,
        last: int,
    ) -> None:
        _validate_nonnegative_integer(last, "last request usage")

        def mutation(raw_state: BudgetState) -> tuple[BudgetState, None]:
            state = raw_state.for_month(month)
            reservation = state.reservation_by_id(reservation_id)
            if reservation.month != month:
                raise ValueError("reservation month does not match successful response")
            return (
                state.consume_with_authoritative_usage(reservation_id, used, remaining, last),
                None,
            )

        self.repository.transact(mutation)

    def record_authoritative_usage(self, month: str, used: int, remaining: int) -> None:
        _validate_nonnegative_integer(used, "authoritative used")
        _validate_nonnegative_integer(remaining, "authoritative remaining")

        def mutation(raw_state: BudgetState) -> tuple[BudgetState, None]:
            state = raw_state.for_month(month)
            floor = max(state.authoritative_used, state.consumed_local, used)
            return replace(
                state,
                authoritative_used=floor,
                authoritative_remaining=remaining,
            ), None

        self.repository.transact(mutation)

    def state(self, month: str) -> BudgetState:
        return self.repository.transact(
            lambda state: (state.for_month(month), state.for_month(month))
        )


def project_worst_case(
    schedule: Iterable[datetime], *, include_close: bool, schedule_change_calls: int = 0
) -> int:
    if isinstance(schedule_change_calls, bool) or not isinstance(schedule_change_calls, int):
        raise TypeError("schedule change calls must be an integer")
    if schedule_change_calls < 0:
        raise ValueError("schedule change calls cannot be negative")
    clusters = len(set(schedule))
    required_origins_with_retry = clusters * 2 * 2
    optional_close = clusters if include_close else 0
    return required_origins_with_retry + optional_close + schedule_change_calls


def _read_state(path: Path) -> BudgetState:
    if not path.exists():
        return BudgetState.empty()
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, Mapping) or set(raw) != {
        "month",
        "authoritative_used",
        "authoritative_remaining",
        "reservations",
    }:
        raise ValueError("budget state has invalid fields")
    reservations = raw["reservations"]
    if not isinstance(reservations, list):
        raise TypeError("budget reservations must be a list")
    return BudgetState(
        month=raw["month"],
        authoritative_used=raw["authoritative_used"],
        authoritative_remaining=raw["authoritative_remaining"],
        reservations=tuple(
            Reservation(**item) if isinstance(item, dict) else _invalid_reservation()
            for item in reservations
        ),
    )


def _invalid_reservation() -> Reservation:
    raise ValueError("budget reservation must be an object")


def _write_state(path: Path, state: BudgetState) -> None:
    descriptor, temporary_name = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(asdict(state), handle, sort_keys=True, separators=(",", ":"))
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_name, path)
        directory_descriptor = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory_descriptor)
        finally:
            os.close(directory_descriptor)
    finally:
        if os.path.exists(temporary_name):
            os.unlink(temporary_name)
