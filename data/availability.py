"""Rules for conservative point-in-time availability timestamps."""

from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from data.schemas import AvailabilityMethod

UTC = UTC


class AvailabilityError(ValueError):
    """Raised when availability cannot be derived safely."""


def parse_market_time(value: str) -> time:
    """Parse a strict HH:MM or HH:MM:SS local market time."""

    for pattern in ("%H:%M", "%H:%M:%S"):
        try:
            return datetime.strptime(value, pattern).time()
        except ValueError:
            continue
    raise AvailabilityError(f"invalid market time: {value!r}")


def prediction_cutoff(
    prediction_date: date,
    *,
    cutoff_time: str = "08:30",
    timezone_name: str = "Asia/Tokyo",
) -> datetime:
    """Return the immutable prediction cutoff as a timezone-aware timestamp."""

    try:
        zone = ZoneInfo(timezone_name)
    except ZoneInfoNotFoundError as exc:
        raise AvailabilityError(f"unknown timezone: {timezone_name}") from exc
    return datetime.combine(prediction_date, parse_market_time(cutoff_time), zone)


def eod_availability(
    market_date: date,
    *,
    market_timezone: str,
    market_close: str,
    provider_lag_minutes: int,
    first_observed_at: datetime,
) -> tuple[datetime, datetime, AvailabilityMethod]:
    """Derive EOD event and availability timestamps conservatively.

    Historical EOD responses do not contain their original publication time.
    The provider SLA is therefore used unless this process observed the record
    earlier. The returned values are UTC.
    """

    if first_observed_at.tzinfo is None or first_observed_at.utcoffset() is None:
        raise AvailabilityError("first_observed_at must be timezone-aware")
    if provider_lag_minutes < 0:
        raise AvailabilityError("provider_lag_minutes must be non-negative")
    try:
        zone = ZoneInfo(market_timezone)
    except ZoneInfoNotFoundError as exc:
        raise AvailabilityError(f"unknown timezone: {market_timezone}") from exc

    event_at = datetime.combine(market_date, parse_market_time(market_close), zone)
    estimated_at = event_at + timedelta(minutes=provider_lag_minutes)
    observed_utc = first_observed_at.astimezone(UTC)
    estimated_utc = estimated_at.astimezone(UTC)
    event_utc = event_at.astimezone(UTC)
    if observed_utc < estimated_utc:
        return event_utc, observed_utc, AvailabilityMethod.FIRST_OBSERVED
    return event_utc, estimated_utc, AvailabilityMethod.PROVIDER_SLA_ESTIMATE


def live_availability(retrieved_at: datetime) -> tuple[datetime, AvailabilityMethod]:
    """Use first observation, never a quote's older source timestamp, for live data."""

    if retrieved_at.tzinfo is None or retrieved_at.utcoffset() is None:
        raise AvailabilityError("retrieved_at must be timezone-aware")
    return retrieved_at.astimezone(UTC), AvailabilityMethod.FIRST_OBSERVED
