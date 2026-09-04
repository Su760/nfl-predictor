from __future__ import annotations

import os
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from hashlib import sha256
from pathlib import Path
from threading import Lock
from typing import Protocol

EVENT_TYPE = "nfl_forecast_due"
DISPATCH_FIELDS = (
    "event_type",
    "source_repository",
    "source_ref",
    "cluster_id",
    "code_sha",
    "nonce",
    "schedule_manifest_sha",
)
REQUIRED_JOB_CATEGORIES = frozenset(
    {
        "private_due_ticks",
        "full_forecast_workers",
        "settlement_jobs",
        "correction_jobs",
        "retry_allowance",
    }
)

_REPOSITORY = re.compile(
    r"^[A-Za-z0-9](?:[A-Za-z0-9_.-]{0,37}[A-Za-z0-9])?/"
    r"[A-Za-z0-9](?:[A-Za-z0-9_.-]{0,98}[A-Za-z0-9])?$"
)
_BRANCH = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9._/-]{0,98}[A-Za-z0-9])?$")
_CLUSTER = re.compile(r"^(?:T72|T60):\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:Z|\+00:00)$")
_CODE_SHA = re.compile(r"^[0-9a-f]{40}$")
_MANIFEST_SHA = re.compile(r"^[0-9a-f]{64}$")
_NONCE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{15,95}$")


def _valid_cluster_id(value: str) -> bool:
    if not _CLUSTER.fullmatch(value):
        return False
    timestamp = value.split(":", 1)[1]
    if timestamp.endswith("Z"):
        timestamp = timestamp[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(timestamp)
    except ValueError:
        return False
    return parsed.tzinfo is not None and parsed.utcoffset() == timedelta(0)


def _valid_branch(value: str) -> bool:
    return bool(_BRANCH.fullmatch(value) and ".." not in value and not value.endswith(".lock"))


class DispatchRejected(ValueError):
    """Raised when an external dispatch does not match the closed contract."""


class PaidUsageRejected(ValueError):
    """Raised when zero-dollar deployment would project billable usage."""


class NonceStore(Protocol):
    def consume(self, nonce: str) -> bool:
        """Atomically return true only for the first observation of a nonce."""


@dataclass(frozen=True)
class DispatchPolicy:
    source_repository: str
    default_branch: str
    approved_code_shas: frozenset[str]
    approved_schedule_manifest_shas: frozenset[str]
    allowed_cluster_ids: frozenset[str]

    def __post_init__(self) -> None:
        if not _REPOSITORY.fullmatch(self.source_repository):
            raise ValueError("source repository must be an exact owner/repository name")
        if not _valid_branch(self.default_branch):
            raise ValueError("default branch contains disallowed characters")
        if not self.approved_code_shas or any(
            not _CODE_SHA.fullmatch(value) for value in self.approved_code_shas
        ):
            raise ValueError("approved code SHAs must be nonempty lowercase 40-character SHAs")
        if not self.approved_schedule_manifest_shas or any(
            not _MANIFEST_SHA.fullmatch(value) for value in self.approved_schedule_manifest_shas
        ):
            raise ValueError(
                "approved schedule manifest SHAs must be nonempty lowercase SHA-256 values"
            )
        if not self.allowed_cluster_ids or any(
            not _valid_cluster_id(value) for value in self.allowed_cluster_ids
        ):
            raise ValueError("allowed cluster IDs must be an explicit nonempty allowlist")

    @classmethod
    def from_environment(cls, environment: Mapping[str, str]) -> DispatchPolicy:
        required = {
            "NFL_PUBLIC_REPOSITORY",
            "NFL_PUBLIC_DEFAULT_BRANCH",
            "NFL_APPROVED_CODE_SHA",
            "NFL_APPROVED_SCHEDULE_MANIFEST_SHA",
            "NFL_APPROVED_CLUSTER_IDS",
        }
        missing = sorted(name for name in required if not environment.get(name))
        if missing:
            raise DispatchRejected(
                "dispatch policy environment is incomplete: " + ", ".join(missing)
            )
        return cls(
            source_repository=environment["NFL_PUBLIC_REPOSITORY"],
            default_branch=environment["NFL_PUBLIC_DEFAULT_BRANCH"],
            approved_code_shas=frozenset({environment["NFL_APPROVED_CODE_SHA"]}),
            approved_schedule_manifest_shas=frozenset(
                {environment["NFL_APPROVED_SCHEDULE_MANIFEST_SHA"]}
            ),
            allowed_cluster_ids=frozenset(
                item for item in environment["NFL_APPROVED_CLUSTER_IDS"].split(",") if item
            ),
        )


@dataclass(frozen=True)
class DispatchEnvelope:
    event_type: str
    source_repository: str
    source_ref: str
    cluster_id: str
    code_sha: str
    nonce: str
    schedule_manifest_sha: str

    def as_payload(self) -> dict[str, str]:
        return {field_name: getattr(self, field_name) for field_name in DISPATCH_FIELDS}


@dataclass(frozen=True)
class DispatchAuthorization:
    target_repository: str
    credential: str = field(repr=False)

    def __post_init__(self) -> None:
        if not _REPOSITORY.fullmatch(self.target_repository):
            raise ValueError("target repository must be an exact owner/repository name")
        if not isinstance(self.credential, str) or not self.credential.strip():
            raise ValueError("dispatch credential is required")


class InMemoryNonceStore:
    def __init__(self) -> None:
        self._seen: set[str] = set()
        self._lock = Lock()

    def consume(self, nonce: str) -> bool:
        with self._lock:
            if nonce in self._seen:
                return False
            self._seen.add(nonce)
            return True


class FileNonceStore:
    def __init__(self, root: Path) -> None:
        self.root = root

    def consume(self, nonce: str) -> bool:
        self.root.mkdir(parents=True, exist_ok=True)
        digest = sha256(nonce.encode("utf-8")).hexdigest()
        destination = self.root / f"{digest}.nonce"
        flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        try:
            descriptor = os.open(destination, flags, 0o600)
        except FileExistsError:
            return False
        try:
            with os.fdopen(descriptor, "w", encoding="ascii") as handle:
                handle.write(f"{digest}\n")
                handle.flush()
                os.fsync(handle.fileno())
        except BaseException:
            destination.unlink(missing_ok=True)
            raise
        return True


def _envelope_from_payload(payload: Mapping[str, object]) -> DispatchEnvelope:
    if set(payload) != set(DISPATCH_FIELDS):
        missing = sorted(set(DISPATCH_FIELDS) - set(payload))
        extra_count = len(set(payload) - set(DISPATCH_FIELDS))
        details = []
        if missing:
            details.append("missing=" + ",".join(missing))
        if extra_count:
            details.append(f"extra_field_count={extra_count}")
        raise DispatchRejected("dispatch schema mismatch: " + " ".join(details))
    if any(not isinstance(payload[field_name], str) for field_name in DISPATCH_FIELDS):
        raise DispatchRejected("every dispatch field must be a string")
    values = {field_name: str(payload[field_name]) for field_name in DISPATCH_FIELDS}
    if values["event_type"] != EVENT_TYPE:
        raise DispatchRejected("dispatch event type is not allowed")
    if not _REPOSITORY.fullmatch(values["source_repository"]):
        raise DispatchRejected("dispatch source repository is malformed")
    source_ref = values["source_ref"]
    if not source_ref.startswith("refs/heads/") or not _valid_branch(
        source_ref.removeprefix("refs/heads/")
    ):
        raise DispatchRejected("dispatch source ref is malformed")
    if not _valid_cluster_id(values["cluster_id"]):
        raise DispatchRejected("dispatch cluster ID is malformed")
    if not _CODE_SHA.fullmatch(values["code_sha"]):
        raise DispatchRejected("dispatch code SHA is malformed")
    if not _NONCE.fullmatch(values["nonce"]):
        raise DispatchRejected("dispatch nonce contains disallowed characters or length")
    if not _MANIFEST_SHA.fullmatch(values["schedule_manifest_sha"]):
        raise DispatchRejected("dispatch schedule manifest SHA is malformed")
    return DispatchEnvelope(**values)


_PROCESS_NONCES = InMemoryNonceStore()


def validate_dispatch(
    payload: Mapping[str, object],
    *,
    policy: DispatchPolicy | None = None,
    nonce_store: NonceStore | None = None,
    environment: Mapping[str, str] | None = None,
) -> DispatchEnvelope:
    envelope = _envelope_from_payload(payload)
    active_policy = policy
    if active_policy is None:
        active_policy = DispatchPolicy.from_environment(
            os.environ if environment is None else environment
        )
    if envelope.source_repository != active_policy.source_repository:
        raise DispatchRejected("dispatch source repository is not authorized")
    expected_ref = f"refs/heads/{active_policy.default_branch}"
    if envelope.source_ref != expected_ref:
        raise DispatchRejected("dispatch source ref is not the authorized default branch")
    if envelope.code_sha not in active_policy.approved_code_shas:
        raise DispatchRejected("dispatch code SHA is not approved")
    if envelope.schedule_manifest_sha not in active_policy.approved_schedule_manifest_shas:
        raise DispatchRejected("dispatch schedule manifest SHA is not approved")
    if envelope.cluster_id not in active_policy.allowed_cluster_ids:
        raise DispatchRejected("dispatch cluster ID is not approved")
    if not (nonce_store or _PROCESS_NONCES).consume(envelope.nonce):
        raise DispatchRejected("dispatch nonce replay rejected")
    return envelope


def build_dispatch_payload(
    *,
    source_repository: str,
    default_branch: str,
    cluster_id: str,
    code_sha: str,
    nonce: str,
    schedule_manifest_sha: str,
) -> dict[str, str]:
    payload = {
        "event_type": EVENT_TYPE,
        "source_repository": source_repository,
        "source_ref": f"refs/heads/{default_branch}",
        "cluster_id": cluster_id,
        "code_sha": code_sha,
        "nonce": nonce,
        "schedule_manifest_sha": schedule_manifest_sha,
    }
    return _envelope_from_payload(payload).as_payload()


def repository_dispatch_request(payload: Mapping[str, object]) -> dict[str, object]:
    envelope = _envelope_from_payload(payload)
    return {"event_type": EVENT_TYPE, "client_payload": envelope.as_payload()}


@dataclass(frozen=True)
class JobProjection:
    category: str
    count: int
    seconds_per_job: int

    def __post_init__(self) -> None:
        if self.category not in REQUIRED_JOB_CATEGORIES:
            raise ValueError("job projection category is not allowed")
        if isinstance(self.count, bool) or not isinstance(self.count, int):
            raise TypeError("job projection count must be an integer")
        if isinstance(self.seconds_per_job, bool) or not isinstance(self.seconds_per_job, int):
            raise TypeError("job runtime seconds must be an integer")
        if self.count < 0 or self.seconds_per_job <= 0:
            raise ValueError("job count must be nonnegative and runtime must be positive")

    @property
    def github_billed_minutes(self) -> int:
        rounded_minutes = (self.seconds_per_job + 59) // 60
        return self.count * rounded_minutes


@dataclass(frozen=True)
class PrivateUsagePlan:
    jobs: tuple[JobProjection, ...]
    github_billed_minutes: int
    projected_storage_bytes: int
    verified_included_actions_minutes_remaining: int
    verified_included_storage_bytes_remaining: int
    projected_paid_actions_minutes: int
    projected_paid_storage_bytes: int
    zero_dollar_mode: bool


def _nonnegative_integer(value: int, field_name: str) -> None:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{field_name} must be an integer")
    if value < 0:
        raise ValueError(f"{field_name} cannot be negative")


def plan_private_usage(
    jobs: Sequence[JobProjection],
    *,
    projected_storage_bytes: int,
    verified_included_actions_minutes_remaining: int,
    verified_included_storage_bytes_remaining: int,
    zero_dollar_mode: bool,
) -> PrivateUsagePlan:
    for value, field_name in (
        (projected_storage_bytes, "projected storage bytes"),
        (
            verified_included_actions_minutes_remaining,
            "verified included Actions minutes",
        ),
        (verified_included_storage_bytes_remaining, "verified included storage bytes"),
    ):
        _nonnegative_integer(value, field_name)
    if not isinstance(zero_dollar_mode, bool):
        raise TypeError("zero_dollar_mode must be a boolean")
    job_tuple = tuple(jobs)
    categories = [job.category for job in job_tuple]
    if len(set(categories)) != len(categories) or set(categories) != REQUIRED_JOB_CATEGORIES:
        raise ValueError("usage plan must contain each required job category exactly once")
    billed_minutes = sum(job.github_billed_minutes for job in job_tuple)
    paid_actions = max(0, billed_minutes - verified_included_actions_minutes_remaining)
    paid_storage = max(0, projected_storage_bytes - verified_included_storage_bytes_remaining)
    if zero_dollar_mode and paid_actions:
        raise PaidUsageRejected(
            f"zero-dollar deployment projects {paid_actions} paid Actions minute(s)"
        )
    if zero_dollar_mode and paid_storage:
        raise PaidUsageRejected(
            f"zero-dollar deployment projects {paid_storage} paid storage byte(s)"
        )
    return PrivateUsagePlan(
        jobs=job_tuple,
        github_billed_minutes=billed_minutes,
        projected_storage_bytes=projected_storage_bytes,
        verified_included_actions_minutes_remaining=(verified_included_actions_minutes_remaining),
        verified_included_storage_bytes_remaining=verified_included_storage_bytes_remaining,
        projected_paid_actions_minutes=paid_actions,
        projected_paid_storage_bytes=paid_storage,
        zero_dollar_mode=zero_dollar_mode,
    )
