from __future__ import annotations

import re
from datetime import datetime
from typing import Literal, Self

from pydantic import model_validator

from .common import UtcModel

AttemptTrigger = Literal["public_dispatch", "private_schedule", "manual", "fixture"]
AttemptStatus = Literal["STARTED", "COMPLETE", "FAILED"]


class RunAttempt(UtcModel):
    attempt_id: str
    origin_run_id: str
    retry_of_attempt_id: str | None
    trigger: AttemptTrigger
    started_at_utc: datetime
    ended_at_utc: datetime | None
    status: AttemptStatus
    safe_error_category: str | None

    @model_validator(mode="after")
    def require_terminal_shape_and_chronology(self) -> Self:
        if not self.attempt_id.strip() or not self.origin_run_id.strip():
            raise ValueError("attempt identifiers must be non-blank")
        if self.retry_of_attempt_id == self.attempt_id:
            raise ValueError("an attempt cannot retry itself")
        if self.retry_of_attempt_id is not None and not self.retry_of_attempt_id.strip():
            raise ValueError("retry attempt identifier must be non-blank")
        if self.status == "STARTED":
            if self.ended_at_utc is not None or self.safe_error_category is not None:
                raise ValueError("started attempts cannot have terminal fields")
            return self
        if self.ended_at_utc is None or self.ended_at_utc < self.started_at_utc:
            raise ValueError("terminal attempts require an end at or after the start")
        if self.status == "COMPLETE" and self.safe_error_category is not None:
            raise ValueError("complete attempts cannot carry an error category")
        if self.status == "FAILED" and (
            self.safe_error_category is None
            or re.fullmatch(r"[A-Z][A-Z0-9_]{0,63}", self.safe_error_category) is None
        ):
            raise ValueError("failed attempts require a safe error category")
        return self
