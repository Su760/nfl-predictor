from enum import StrEnum


class Origin(StrEnum):
    T72 = "T72"
    T60 = "T60"


class ProvenanceGrade(StrEnum):
    A = "A"
    B = "B"
    C = "C"


class RunStatus(StrEnum):
    COMPLETE = "COMPLETE"
    FOOTBALL_ONLY = "FOOTBALL_ONLY"
    MISSED = "MISSED"
    FAILED = "FAILED"


class PredictionStatus(StrEnum):
    COMPLETE = "COMPLETE"
    DEGRADED = "DEGRADED"


class SnapshotStatus(StrEnum):
    COMPLETE = "COMPLETE"
    DEGRADED = "DEGRADED"
    FAILED = "FAILED"
