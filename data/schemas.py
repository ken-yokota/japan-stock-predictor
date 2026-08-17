"""Provider-neutral data contracts used by ingestion and alignment."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any


class AvailabilityMethod(StrEnum):
    """Evidence used to determine when a value could first be consumed."""

    FIRST_OBSERVED = "first_observed"
    PROVIDER_TIMESTAMP = "provider_timestamp"
    PROVIDER_SLA_ESTIMATE = "provider_sla_estimate"
    PUBLISHED_SCHEDULE = "published_schedule"


class DataInterval(StrEnum):
    """Supported normalized bar/snapshot intervals."""

    EOD = "eod"
    LIVE_SNAPSHOT = "live_snapshot"
    ONE_MINUTE = "1m"
    FIVE_MINUTES = "5m"
    ONE_HOUR = "1h"


class DataQuality(StrEnum):
    """Provenance/validation quality of one raw observation.

    Freshness and primary/fallback selection are intentionally represented by
    separate types.  An official value can still be stale and a delayed value
    can still be usable within its configured maximum age.
    """

    OFFICIAL = "OFFICIAL"
    EOD_CONFIRMED = "EOD_CONFIRMED"
    FREE_UNVERIFIED = "FREE_UNVERIFIED"
    DELAYED = "DELAYED"
    MISSING = "MISSING"


def _require_aware(value: datetime, field_name: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} must be timezone-aware")


@dataclass(frozen=True, slots=True)
class MarketBar:
    """A point-in-time-aware normalized OHLCV record.

    ``timestamp`` is the economic event/bar time. ``available_timestamp`` is
    the only timestamp that may be used for as-of feature selection.
    """

    canonical_symbol: str
    provider_symbol: str
    provider: str
    market: str
    market_timezone: str
    market_date: date
    timestamp: datetime
    available_timestamp: datetime
    first_observed_at: datetime
    retrieved_at: datetime
    interval: DataInterval
    availability_method: AvailabilityMethod
    close: Decimal
    data_quality: DataQuality = DataQuality.FREE_UNVERIFIED
    is_realtime: bool = False
    is_delayed: bool = False
    source_timestamp: datetime | None = None
    open: Decimal | None = None
    high: Decimal | None = None
    low: Decimal | None = None
    adjusted_close: Decimal | None = None
    volume: int | None = None
    currency: str | None = None
    raw_hash: str | None = None
    quality_flags: tuple[str, ...] = field(default_factory=tuple)

    def _drop_untrustworthy_bounds(self) -> None:
        """Discard a high or low the bar's own values contradict.

        Yahoo's FX daily bars are not internally consistent. Measured on
        2026-08-17 over 390 sessions: `Open` equals `Close` exactly on 199 of
        them for JPY=X and 193 for EURJPY=X and AUDJPY=X - about half - so the
        published open is frequently a copy of the close rather than a session
        open. On roughly 20 sessions per series the high or low then sits inside
        the pair by a median of 1e-4 relative, a hundred times a four-decimal
        rounding tick, because the range is measured over a window that does not
        match the copied open.

        Raising here discarded the whole row, and with it the close - the only
        value in the bar that is trustworthy and the one every indicator feature
        is built from. That is why usdjpy, eurjpy and audjpy produced no
        features at all and all 22 tickers reported DEGRADED.

        So the untrustworthy bound is dropped and the row is kept. Absent is the
        honest state: it is not fabricated, and an imputer fitted inside the
        training fold can handle it, whereas a bound silently widened to fit
        would assert a range that was never observed. `open` and `close` are
        never modified - one of them is the prediction target.
        """

        present = [
            v for v in (self.open, self.high, self.low, self.close) if v is not None
        ]
        if not present:
            return
        dropped: list[str] = []
        if self.high is not None and self.high < max(present):
            object.__setattr__(self, "high", None)
            dropped.append("high")
        remaining = [
            v for v in (self.open, self.high, self.low, self.close) if v is not None
        ]
        if self.low is not None and remaining and self.low > min(remaining):
            object.__setattr__(self, "low", None)
            dropped.append("low")
        if dropped:
            object.__setattr__(
                self,
                "quality_flags",
                (*self.quality_flags, f"ohlc_bound_dropped_{'_'.join(dropped)}"),
            )

    def __post_init__(self) -> None:
        for field_name in (
            "timestamp",
            "available_timestamp",
            "first_observed_at",
            "retrieved_at",
        ):
            _require_aware(getattr(self, field_name), field_name)
        if self.source_timestamp is not None:
            _require_aware(self.source_timestamp, "source_timestamp")
        if not self.canonical_symbol.strip() or not self.provider_symbol.strip():
            raise ValueError("symbols must not be blank")
        self._drop_untrustworthy_bounds()
        present = [
            v for v in (self.open, self.high, self.low, self.close) if v is not None
        ]
        if self.high is not None and present and self.high < max(present):
            raise ValueError("high is below another OHLC value")
        if self.low is not None and present and self.low > min(present):
            raise ValueError("low is above another OHLC value")
        if self.volume is not None and self.volume < 0:
            raise ValueError("volume must be non-negative")
        if self.data_quality is DataQuality.MISSING:
            raise ValueError("missing observations must not be stored as MarketBar")
        if self.timestamp > self.available_timestamp:
            raise ValueError("market timestamp cannot be after availability")
        if self.available_timestamp > self.first_observed_at:
            raise ValueError("available_timestamp cannot be after first observation")
        if self.first_observed_at > self.retrieved_at:
            raise ValueError("first observation cannot be after retrieval")
        if (
            self.source_timestamp is not None
            and self.source_timestamp > self.retrieved_at
        ):
            raise ValueError("source timestamp cannot be after retrieval")

    @property
    def market_timestamp(self) -> datetime:
        """Explicit business name for the economic event/bar timestamp."""

        return self.timestamp


@dataclass(frozen=True, slots=True)
class FetchRequest:
    """Provider-neutral historical fetch request."""

    canonical_symbol: str
    provider_symbol: str
    market: str
    market_timezone: str
    market_close: str
    availability_lag_minutes: int
    start_date: date
    end_date: date
    currency: str | None = None

    def __post_init__(self) -> None:
        if self.start_date > self.end_date:
            raise ValueError("start_date must not be after end_date")
        if self.availability_lag_minutes < 0:
            raise ValueError("availability_lag_minutes must be non-negative")


@dataclass(frozen=True, slots=True)
class SnapshotRequest:
    """Provider-neutral request for the newest observation available now."""

    canonical_symbol: str
    provider_symbol: str
    market: str
    market_timezone: str
    currency: str | None = None

    def __post_init__(self) -> None:
        if not self.canonical_symbol.strip() or not self.provider_symbol.strip():
            raise ValueError("symbols must not be blank")


@dataclass(frozen=True, slots=True)
class SessionOpenRequest:
    """Request the first intraday bar of one verified market session."""

    canonical_symbol: str
    provider_symbol: str
    market: str
    market_timezone: str
    session_date: date
    session_open: str
    currency: str | None = None

    def __post_init__(self) -> None:
        if not self.canonical_symbol.strip() or not self.provider_symbol.strip():
            raise ValueError("symbols must not be blank")
        parts = self.session_open.split(":")
        if len(parts) != 2:
            raise ValueError("session_open must use HH:MM")
        try:
            hour, minute = (int(part) for part in parts)
        except ValueError as exc:
            raise ValueError("session_open must use HH:MM") from exc
        if not 0 <= hour <= 23 or not 0 <= minute <= 59:
            raise ValueError("session_open must be a valid wall-clock time")


@dataclass(frozen=True, slots=True)
class ProviderHealth:
    """Sanitized provider health result."""

    provider: str
    ok: bool
    checked_at: datetime
    message: str


JsonObject = dict[str, Any]
