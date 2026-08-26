"""Cutoff and freshness gates for operational market snapshots."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum

from data.schemas import DataQuality, MarketBar


class FreshnessStatus(StrEnum):
    FRESH = "FRESH"
    STALE = "STALE"
    AFTER_CUTOFF = "AFTER_CUTOFF"
    FUTURE_TIMESTAMP = "FUTURE_TIMESTAMP"
    QUALITY_REJECTED = "QUALITY_REJECTED"
    MISSING = "MISSING"


class SelectionRole(StrEnum):
    PRIMARY = "PRIMARY"
    FALLBACK = "FALLBACK"
    NONE = "NONE"


@dataclass(frozen=True, slots=True)
class FreshnessAssessment:
    status: FreshnessStatus
    cutoff_at: datetime
    market_timestamp: datetime | None
    age: timedelta | None
    reason: str

    @property
    def usable(self) -> bool:
        return self.status is FreshnessStatus.FRESH


def _minutes(value: timedelta) -> str:
    """A duration a person can compare against a configured limit."""

    return f"{value.total_seconds() / 60:.1f}分"


def assess_snapshot(
    row: MarketBar | None,
    *,
    cutoff_at: datetime,
    max_age: timedelta,
    acceptable_qualities: frozenset[DataQuality] = frozenset(
        {DataQuality.DELAYED, DataQuality.FREE_UNVERIFIED, DataQuality.OFFICIAL}
    ),
) -> FreshnessAssessment:
    """Fail closed when an observation was late, future-dated, or stale."""

    if cutoff_at.tzinfo is None or cutoff_at.utcoffset() is None:
        raise ValueError("cutoff_at must be timezone-aware")
    if max_age < timedelta(0):
        raise ValueError("max_age must be non-negative")
    if row is None:
        return FreshnessAssessment(
            FreshnessStatus.MISSING, cutoff_at, None, None, "provider returned no row"
        )
    if row.data_quality not in acceptable_qualities:
        return FreshnessAssessment(
            FreshnessStatus.QUALITY_REJECTED,
            cutoff_at,
            row.market_timestamp,
            None,
            f"quality {row.data_quality.value} is not accepted",
        )
    if (
        row.available_timestamp > cutoff_at
        or row.first_observed_at > cutoff_at
        or row.retrieved_at > cutoff_at
    ):
        return FreshnessAssessment(
            FreshnessStatus.AFTER_CUTOFF,
            cutoff_at,
            row.market_timestamp,
            None,
            "observation was not fully retrieved by the immutable cutoff",
        )
    if row.market_timestamp > cutoff_at:
        return FreshnessAssessment(
            FreshnessStatus.FUTURE_TIMESTAMP,
            cutoff_at,
            row.market_timestamp,
            None,
            "provider timestamp is after the prediction cutoff",
        )
    age = cutoff_at - row.market_timestamp
    if age > max_age:
        # The measured age, not just the limit it broke. Seven series were
        # failing this gate every morning and the record said only "exceeds
        # 0:10:00", which cannot tell you whether the limit is slightly tight
        # or the data is hours old -- and so cannot tell you what to change.
        return FreshnessAssessment(
            FreshnessStatus.STALE,
            cutoff_at,
            row.market_timestamp,
            age,
            f"snapshot age {_minutes(age)} exceeds {_minutes(max_age)}"
            f" (bar {row.market_timestamp.isoformat()}, cutoff"
            f" {cutoff_at.isoformat()})",
        )
    return FreshnessAssessment(
        FreshnessStatus.FRESH,
        cutoff_at,
        row.market_timestamp,
        age,
        f"snapshot passed PIT and freshness gates (age {_minutes(age)}"
        f" within {_minutes(max_age)})",
    )
