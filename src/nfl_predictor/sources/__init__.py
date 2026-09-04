"""Offline-capable source capture adapters."""

from .base import BuildIdentity, RawResponse, SourceAdapter
from .nflverse import NflverseAdapter
from .odds_api import OddsApiAdapter

__all__ = [
    "BuildIdentity",
    "NflverseAdapter",
    "OddsApiAdapter",
    "RawResponse",
    "SourceAdapter",
]
