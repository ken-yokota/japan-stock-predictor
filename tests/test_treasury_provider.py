from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal

import httpx
import pytest

from data.providers.base import MarketDataProvider, ProviderResponseError
from data.providers.treasury import (
    EARLY_FIRST_OBSERVED_FLAG,
    PUBLISHED_SCHEDULE_ESTIMATE_FLAG,
    TreasuryProvider,
)
from data.schemas import (
    AvailabilityMethod,
    DataInterval,
    DataQuality,
    FetchRequest,
    MarketBar,
)
from data.treasury_features import (
    OBSERVATION_LAG_FLAG,
    TREASURY_SPREAD_SYMBOL,
    build_treasury_features,
)


def _entry(day: date, fields: Mapping[str, str | None]) -> str:
    properties = [
        f'<d:NEW_DATE m:type="Edm.DateTime">{day.isoformat()}T00:00:00</d:NEW_DATE>'
    ]
    for field, value in fields.items():
        if value is None:
            properties.append(f'<d:{field} m:null="true" />')
        else:
            properties.append(f'<d:{field} m:type="Edm.Double">{value}</d:{field}>')
    return "".join(
        (
            "<entry>",
            "<updated>2099-01-01T00:00:00Z</updated>",
            '<content type="application/xml"><m:properties>',
            *properties,
            "</m:properties></content>",
            "</entry>",
        )
    )


def _feed(*entries: str) -> bytes:
    return "".join(
        (
            '<?xml version="1.0" encoding="utf-8"?>',
            '<feed xmlns="http://www.w3.org/2005/Atom" ',
            'xmlns:d="http://schemas.microsoft.com/ado/2007/08/dataservices" ',
            'xmlns:m="http://schemas.microsoft.com/ado/2007/08/dataservices/metadata">',
            "<updated>2099-01-01T00:00:00Z</updated>",
            *entries,
            "</feed>",
        )
    ).encode()


def _provider(
    handler: Callable[[httpx.Request], httpx.Response],
    *,
    now: datetime,
    max_retries: int = 2,
    backoff_seconds: float = 1.0,
    sleeper: Callable[[float], None] = lambda _: None,
) -> TreasuryProvider:
    client = httpx.Client(
        transport=httpx.MockTransport(handler),
        base_url="https://treasury.test",
    )
    return TreasuryProvider(
        client=client,
        clock=lambda: now,
        max_retries=max_retries,
        backoff_seconds=backoff_seconds,
        sleeper=sleeper,
    )


def test_named_tenors_ignore_updated_unknown_and_missing_fields() -> None:
    observed_at = datetime(2026, 8, 8, 0, 0, tzinfo=UTC)

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/interest-rates/pages/xml")
        assert request.url.params["data"] == "daily_treasury_yield_curve"
        assert request.url.params["field_tdr_date_value"] == "2026"
        return httpx.Response(
            200,
            content=_feed(
                # Entries and properties are deliberately out of order.
                _entry(
                    date(2026, 8, 7),
                    {
                        "BC_30YEAR": "4.42",
                        "BC_5YEAR": "3.80",  # unknown/unrequested tenor
                        "BC_2YEAR": "3.51",
                        "BC_10YEAR": "4.16",
                    },
                ),
                _entry(
                    date(2026, 8, 6),
                    {
                        "BC_30YEAR": None,
                        "BC_2YEAR": "3.50",
                        # Missing 10Y is a valid partial observation.
                    },
                ),
            ),
        )

    rows = _provider(handler, now=observed_at).fetch_treasury_yield_bars(2026)

    assert [(row.market_date, row.canonical_symbol, row.close) for row in rows] == [
        (date(2026, 8, 6), "us_2y_yield", Decimal("3.50")),
        (date(2026, 8, 7), "us_2y_yield", Decimal("3.51")),
        (date(2026, 8, 7), "us_10y_yield", Decimal("4.16")),
        (date(2026, 8, 7), "us_30y_yield", Decimal("4.42")),
    ]
    latest = rows[-1]
    assert latest.timestamp == datetime(2026, 8, 7, 19, 30, tzinfo=UTC)
    assert latest.available_timestamp == datetime(2026, 8, 7, 22, 0, tzinfo=UTC)
    assert latest.first_observed_at == observed_at
    assert latest.source_timestamp is None
    assert latest.availability_method is AvailabilityMethod.PUBLISHED_SCHEDULE
    assert PUBLISHED_SCHEDULE_ESTIMATE_FLAG in latest.quality_flags
    assert latest.data_quality is DataQuality.OFFICIAL
    assert latest.is_realtime is False
    assert latest.is_delayed is False


def test_common_provider_contract_fetches_one_tenor_and_healthchecks() -> None:
    calls = 0

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(
            200,
            content=_feed(
                _entry(
                    date(2026, 8, 7),
                    {"BC_2YEAR": "3.51", "BC_10YEAR": "4.16"},
                )
            ),
        )

    provider = _provider(handler, now=datetime(2026, 8, 8, 0, 0, tzinfo=UTC))
    assert isinstance(provider, MarketDataProvider)
    rows = provider.fetch_eod(
        FetchRequest(
            canonical_symbol="us_2y_yield",
            provider_symbol="TREASURY:BC_2YEAR",
            market="US_TREASURY",
            market_timezone="America/New_York",
            market_close="18:00",
            availability_lag_minutes=0,
            start_date=date(2026, 8, 7),
            end_date=date(2026, 8, 7),
        )
    )
    assert [(row.canonical_symbol, row.close) for row in rows] == [
        ("us_2y_yield", Decimal("3.51"))
    ]
    assert provider.healthcheck().ok is True
    assert calls == 2


def test_observation_before_1800_eastern_uses_first_observed() -> None:
    observed_at = datetime(2026, 8, 7, 21, 0, tzinfo=UTC)  # 17:00 EDT
    provider = _provider(
        lambda _: httpx.Response(
            200,
            content=_feed(_entry(date(2026, 8, 7), {"BC_10YEAR": "4.16"})),
        ),
        now=observed_at,
    )

    row = provider.fetch_treasury_yield_bars(2026)[0]

    assert row.timestamp == datetime(2026, 8, 7, 19, 30, tzinfo=UTC)
    assert row.available_timestamp == observed_at
    assert row.availability_method is AvailabilityMethod.FIRST_OBSERVED
    assert EARLY_FIRST_OBSERVED_FLAG in row.quality_flags


def test_date_range_crosses_years_and_returns_sorted_rows() -> None:
    requested_years: list[int] = []
    payloads = {
        2025: _feed(
            _entry(date(2025, 12, 31), {"BC_2YEAR": "3.40"}),
            _entry(date(2025, 12, 30), {"BC_2YEAR": "3.39"}),
        ),
        2026: _feed(
            _entry(date(2026, 1, 5), {"BC_2YEAR": "3.43"}),
            _entry(date(2026, 1, 2), {"BC_2YEAR": "3.41"}),
        ),
    }

    def handler(request: httpx.Request) -> httpx.Response:
        year = int(request.url.params["field_tdr_date_value"])
        requested_years.append(year)
        return httpx.Response(200, content=payloads[year])

    rows = _provider(
        handler,
        now=datetime(2026, 1, 6, 0, 0, tzinfo=UTC),
    ).fetch_treasury_yield_bars_for_range(
        date(2025, 12, 31),
        date(2026, 1, 2),
        tenor_symbols={"2Y": "two_year"},
    )

    assert requested_years == [2025, 2026]
    assert [(row.market_date, row.canonical_symbol) for row in rows] == [
        (date(2025, 12, 31), "two_year"),
        (date(2026, 1, 2), "two_year"),
    ]
    # 18:00 EST is 23:00 UTC; ZoneInfo handles the winter offset.
    assert rows[-1].available_timestamp == datetime(2026, 1, 2, 23, 0, tzinfo=UTC)


def test_retry_uses_injected_sleeper() -> None:
    statuses = iter((503, 429, 200))
    delays: list[float] = []

    def handler(_: httpx.Request) -> httpx.Response:
        status = next(statuses)
        if status == 200:
            return httpx.Response(200, content=_feed())
        return httpx.Response(status)

    provider = _provider(
        handler,
        now=datetime(2026, 1, 6, 0, 0, tzinfo=UTC),
        max_retries=2,
        backoff_seconds=0.25,
        sleeper=delays.append,
    )

    assert provider.fetch_treasury_yield_bars(2026) == []
    assert delays == [0.25, 0.5]


def test_malformed_known_rate_is_rejected() -> None:
    provider = _provider(
        lambda _: httpx.Response(
            200,
            content=_feed(_entry(date(2026, 1, 2), {"BC_2YEAR": "not-a-number"})),
        ),
        now=datetime(2026, 1, 3, 0, 0, tzinfo=UTC),
    )

    with pytest.raises(ProviderResponseError, match="BC_2YEAR"):
        provider.fetch_treasury_yield_bars(2026)


def _yield_bar(
    tenor: str,
    day: date,
    value: str,
    *,
    availability_offset: int,
) -> MarketBar:
    event_at = datetime.combine(day, time(20, 0), tzinfo=UTC)
    available_at = event_at + timedelta(hours=availability_offset)
    observed_at = available_at + timedelta(minutes=5)
    return MarketBar(
        canonical_symbol=f"us_{tenor.lower()}_yield",
        provider_symbol=f"TREASURY:BC_{tenor[:-1]}YEAR",
        provider="us_treasury",
        market="US_TREASURY",
        market_timezone="America/New_York",
        market_date=day,
        timestamp=event_at,
        source_timestamp=None,
        available_timestamp=available_at,
        first_observed_at=observed_at,
        retrieved_at=observed_at,
        interval=DataInterval.EOD,
        availability_method=AvailabilityMethod.PUBLISHED_SCHEDULE,
        data_quality=DataQuality.OFFICIAL,
        is_realtime=False,
        is_delayed=False,
        close=Decimal(value),
        currency="PERCENT",
        raw_hash=f"{tenor}-{day.isoformat()}",
        quality_flags=("official_us_treasury",),
    )


def test_derived_spread_and_changes_use_observation_lags_and_max_availability() -> None:
    days = (
        date(2025, 12, 29),
        date(2025, 12, 30),
        date(2025, 12, 31),
        date(2026, 1, 2),
        date(2026, 1, 5),  # three calendar days, one observation after Jan 2
        date(2026, 1, 6),
    )
    values = {
        "2Y": ("4.00", "4.10", "4.20", "4.30", "4.40", "4.50"),
        "10Y": ("3.00", "3.10", "3.20", "3.30", "3.40", "3.60"),
        "30Y": ("4.50", "4.51", "4.52", "4.53", "4.54", "4.55"),
    }
    bars = [
        _yield_bar(
            tenor,
            day,
            values[tenor][index],
            availability_offset={"2Y": 1, "10Y": 2, "30Y": 3}[tenor],
        )
        for index, day in enumerate(days)
        for tenor in ("30Y", "10Y", "2Y")
    ]

    features = build_treasury_features(reversed(bars))
    by_key = {(row.canonical_symbol, row.market_date): row for row in features}

    assert len(features) == 33  # 6 spreads + 3 tenors * (5 + 3 + 1) changes
    spread = by_key[(TREASURY_SPREAD_SYMBOL, date(2026, 1, 6))]
    assert spread.close == Decimal("-0.90")
    assert spread.available_timestamp == datetime(2026, 1, 6, 22, 0, tzinfo=UTC)
    assert spread.data_quality is DataQuality.OFFICIAL
    assert spread.provider == "internal"
    assert spread.is_realtime is False
    assert spread.is_delayed is False

    one_observation = by_key[("us_2y_yield_change_1d", date(2026, 1, 5))]
    assert one_observation.close == Decimal("0.10")
    assert OBSERVATION_LAG_FLAG in one_observation.quality_flags
    assert "treasury_observation_lag_1d" in one_observation.quality_flags

    three_observations = by_key[("us_2y_yield_change_3d", date(2026, 1, 5))]
    assert three_observations.close == Decimal("0.30")

    five_observations = by_key[("us_30y_yield_change_5d", date(2026, 1, 6))]
    assert five_observations.close == Decimal("0.05")
