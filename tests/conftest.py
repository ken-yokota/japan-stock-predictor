"""Shared test fixtures."""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal

import pytest

from data.schemas import AvailabilityMethod, DataInterval, DataQuality, MarketBar

UTC = UTC


@pytest.fixture
def make_bar():
    def factory(
        *,
        canonical_symbol: str = "SPY",
        provider: str = "eodhd",
        provider_symbol: str | None = None,
        market_date: date = date(2026, 8, 7),
        timestamp: datetime = datetime(2026, 8, 7, 20, 0, tzinfo=UTC),
        available_timestamp: datetime = datetime(2026, 8, 7, 20, 15, tzinfo=UTC),
        first_observed_at: datetime = datetime(2026, 8, 8, 0, 0, tzinfo=UTC),
        retrieved_at: datetime = datetime(2026, 8, 8, 0, 0, tzinfo=UTC),
        raw_hash: str = "a" * 64,
        interval: DataInterval = DataInterval.EOD,
        data_quality: DataQuality = DataQuality.FREE_UNVERIFIED,
        is_realtime: bool = False,
        is_delayed: bool = False,
    ) -> MarketBar:
        return MarketBar(
            canonical_symbol=canonical_symbol,
            provider_symbol=provider_symbol or f"{canonical_symbol}.US",
            provider=provider,
            market="US",
            market_timezone="America/New_York",
            market_date=market_date,
            timestamp=timestamp,
            available_timestamp=available_timestamp,
            first_observed_at=first_observed_at,
            retrieved_at=retrieved_at,
            interval=interval,
            availability_method=AvailabilityMethod.PROVIDER_SLA_ESTIMATE,
            data_quality=data_quality,
            is_realtime=is_realtime,
            is_delayed=is_delayed,
            open=Decimal("100"),
            high=Decimal("103"),
            low=Decimal("99"),
            close=Decimal("102"),
            volume=1_000,
            raw_hash=raw_hash,
        )

    return factory
