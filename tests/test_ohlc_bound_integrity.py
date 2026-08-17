"""A bar's untrustworthy bound must not cost us its close.

Yahoo's FX daily bars are internally inconsistent. Measured 2026-08-17 over 390
sessions: `Open` equals `Close` exactly on 199 of them for JPY=X, so the
published open is often a copy of the close rather than a session open, and on
about 20 sessions per series the high or low then sits inside that pair by a
median of 1e-4 relative - a hundred times a four-decimal rounding tick.

Raising on that discarded the whole row including the close, which is the only
value the indicator features are built from. usdjpy, eurjpy and audjpy therefore
produced no features at all and all 22 tickers reported DEGRADED every morning.

These pin the fix and, more importantly, its limits: the close survives, the
traded values are never rewritten, and a bound that cannot be trusted becomes
absent rather than being widened into a range nobody observed.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from data.schemas import (
    AvailabilityMethod,
    DataInterval,
    DataQuality,
    MarketBar,
)

WHEN = datetime(2026, 8, 14, 12, 0, tzinfo=UTC)


def _bar(**overrides: object) -> MarketBar:
    payload: dict[str, object] = {
        "canonical_symbol": "usdjpy",
        "provider_symbol": "JPY=X",
        "provider": "yahoo_finance",
        "market": "FOREX",
        "market_timezone": "Europe/London",
        "market_date": WHEN.date(),
        "timestamp": WHEN,
        "available_timestamp": WHEN,
        "first_observed_at": WHEN,
        "retrieved_at": WHEN,
        "interval": DataInterval.EOD,
        "availability_method": AvailabilityMethod.PUBLISHED_SCHEDULE,
        "close": Decimal("151.220001"),
        "data_quality": DataQuality.FREE_UNVERIFIED,
        "is_realtime": False,
        "is_delayed": False,
    }
    payload.update(overrides)
    return MarketBar(**payload)  # type: ignore[arg-type]


def test_the_close_survives_a_high_that_contradicts_it() -> None:
    """The exact 2025-02-20 JPY=X row, which used to be discarded whole."""

    bar = _bar(
        open=Decimal("151.220001"),
        high=Decimal("151.209000"),
        low=Decimal("149.492004"),
        close=Decimal("151.220001"),
    )
    assert bar.close == Decimal("151.220001")
    assert bar.high is None
    assert bar.low == Decimal("149.492004")


def test_a_low_above_the_traded_values_is_dropped() -> None:
    """The 2025-02-21 shape: low prints a hair above open and close."""

    bar = _bar(
        open=Decimal("149.408005"),
        high=Decimal("150.735001"),
        low=Decimal("149.410995"),
        close=Decimal("149.408005"),
    )
    assert bar.low is None
    assert bar.high == Decimal("150.735001")
    assert bar.close == Decimal("149.408005")


def test_the_traded_values_are_never_rewritten() -> None:
    """One of them is the prediction target; neither may be adjusted to fit."""

    opened, closed = Decimal("151.220001"), Decimal("151.220001")
    bar = _bar(
        open=opened,
        high=Decimal("151.209000"),
        low=Decimal("149.492004"),
        close=closed,
    )
    assert bar.open == opened
    assert bar.close == closed


def test_a_bound_is_dropped_not_widened() -> None:
    """Widening would assert a range that was never observed."""

    bar = _bar(
        open=Decimal("151.220001"),
        high=Decimal("151.209000"),
        low=Decimal("149.492004"),
        close=Decimal("151.220001"),
    )
    # Absent, not silently moved up to the open/close level.
    assert bar.high is None


def test_a_consistent_bar_is_untouched() -> None:
    """The ordinary case must not acquire a flag or lose a field."""

    bar = _bar(
        open=Decimal("150.0"),
        high=Decimal("151.0"),
        low=Decimal("149.0"),
        close=Decimal("150.5"),
    )
    assert (bar.open, bar.high, bar.low, bar.close) == (
        Decimal("150.0"),
        Decimal("151.0"),
        Decimal("149.0"),
        Decimal("150.5"),
    )
    assert not any("ohlc_bound_dropped" in flag for flag in bar.quality_flags)


def test_the_drop_is_recorded_on_the_row() -> None:
    """A silently repaired row is indistinguishable from a clean one."""

    bar = _bar(
        open=Decimal("151.220001"),
        high=Decimal("151.209000"),
        low=Decimal("149.492004"),
        close=Decimal("151.220001"),
    )
    assert any(flag.startswith("ohlc_bound_dropped") for flag in bar.quality_flags)


def test_both_bounds_can_be_dropped_together() -> None:
    bar = _bar(
        open=Decimal("150.0"),
        high=Decimal("149.9"),
        low=Decimal("150.1"),
        close=Decimal("150.0"),
    )
    assert bar.high is None
    assert bar.low is None
    assert bar.close == Decimal("150.0")


def test_a_bar_with_only_a_close_is_accepted() -> None:
    bar = _bar()
    assert bar.close == Decimal("151.220001")
    assert bar.high is None and bar.low is None and bar.open is None
