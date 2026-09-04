from datetime import datetime
from typing import Literal

from pydantic import model_validator

from .common import UtcModel
from .enums import Origin


class BettingDecision(UtcModel):
    decision_id: str
    prediction_id: str
    canonical_event_id: str
    origin: Origin
    side: Literal["home", "away"]
    quote_id: str | None
    candidate_id: str | None
    evaluated_at_utc: datetime
    policy_version: str
    status: Literal["candidate", "rejected"]
    reason_codes: list[str]

    @model_validator(mode="after")
    def require_candidate_links(self) -> "BettingDecision":
        linked = self.quote_id is not None and self.candidate_id is not None
        if (self.status == "candidate") != linked:
            raise ValueError("only accepted decisions link quote and candidate")
        if self.status == "candidate" and self.reason_codes:
            raise ValueError("accepted candidate cannot contain rejection reasons")
        if self.status == "rejected" and not self.reason_codes:
            raise ValueError("rejection requires at least one reason code")
        return self
