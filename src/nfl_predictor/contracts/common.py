from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, model_validator


class UtcModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    @model_validator(mode="after")
    def require_aware_datetimes(self) -> UtcModel:
        for name in type(self).model_fields:
            value = getattr(self, name)
            if not isinstance(value, datetime):
                continue
            offset = value.utcoffset()
            if value.tzinfo is None or offset is None:
                raise ValueError(f"{name} must be timezone-aware UTC")
            if offset.total_seconds() != 0:
                raise ValueError(f"{name} must be normalized to UTC")
        return self
