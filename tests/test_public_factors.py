"""Public data normalization preserves source publication and observation lag."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

import httpx
import pytest

from data.availability import prediction_cutoff
from data.providers.base import (
    MarketDataProvider,
    ProviderFetchError,
    ProviderResponseError,
)
from data.providers.public_factors import (
    FRED_CSV_URL,
    FRED_SYMBOLS,
    JGB_SYMBOLS,
    MOF_CURRENT_URL,
    MOF_HISTORY_URL,
    PublicFactorsProvider,
    fred_estimated_available_at,
    latest_expected_observation,
    mof_available_at,
    parse_fred_csv,
    parse_mof_csv,
)
from data.schemas import AvailabilityMethod, DataInterval, FetchRequest

NOW = datetime(2026, 10, 16, 0, tzinfo=UTC)
JST = ZoneInfo("Asia/Tokyo")
CENTRAL = ZoneInfo("America/Chicago")


def _mof(*rows: str, encoding: str = "cp932") -> bytes:
    return (
        "国債金利情報,,,,(単位 : %)\n"
        "基準日,20年,1年,10年,2年\n"
        + "\n".join(rows)
        + "\n,,,,\n※最新のcsvデータを取得してください。,,,,\n"
    ).encode(encoding)


def _fred(*rows: str, symbol: str = "BAMLC0A0CM") -> bytes:
    return (f"observation_date,{symbol}\n" + "\n".join(rows) + "\n").encode()


def _request(
    canonical: str = "jgb_2y_yield",
    *,
    start: date = date(2026, 9, 1),
    end: date = date(2026, 10, 15),
    symbol: str | None = None,
) -> FetchRequest:
    return FetchRequest(
        canonical_symbol=canonical,
        provider_symbol=symbol or {**JGB_SYMBOLS, **FRED_SYMBOLS}[canonical],
        market="JP_JGB" if canonical.startswith("jgb_") else "US_CREDIT",
        market_timezone="Asia/Tokyo",
        # A generic ingestion request cannot override publication timing.
        market_close="00:00",
        availability_lag_minutes=0,
        start_date=start,
        end_date=end,
    )


def _provider(
    handler: Callable[[httpx.Request], httpx.Response],
    *,
    now: datetime = NOW,
    max_retries: int = 0,
    sleeper: Callable[[float], None] = lambda _: None,
) -> PublicFactorsProvider:
    return PublicFactorsProvider(
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        clock=lambda: now,
        max_retries=max_retries,
        sleeper=sleeper,
    )


@pytest.mark.parametrize("encoding", ["cp932", "utf-8-sig"])
def test_mof_parses_named_columns_eras_negative_rates_and_missing_values(
    encoding: str,
) -> None:
    payload = _mof(
        "S64.1.6,4.2,1.1,3.2,2.1",
        "H31.4.26,0.4,0.1,0.2,-0.15",
        "R1.5.7,0.5,0.1,0.3,-",
        "R8.10.14,3.849,1.66,3.015,1.929",
        encoding=encoding,
    )
    two_year = parse_mof_csv(payload, "2Y")
    assert two_year[date(1989, 1, 6)] == Decimal("2.1")
    assert two_year[date(2019, 4, 26)] == Decimal("-0.15")
    assert date(2019, 5, 7) not in two_year
    assert parse_mof_csv(payload, "10Y")[date(2026, 10, 14)] == Decimal("3.015")
    assert parse_mof_csv(payload, "20Y")[date(2026, 10, 14)] == Decimal("3.849")


@pytest.mark.parametrize(
    "payload",
    [
        b"<html>unavailable</html>",
        "基準日,1年\nR8.10.14,1.0\n".encode(),
        _mof("R8.10.14,3.0"),
        _mof("R1.1.1,3.0,1.0,2.0,1.0"),
        _mof("H31.5.1,3.0,1.0,2.0,1.0"),
        _mof("R8.2.30,3.0,1.0,2.0,1.0"),
        _mof("R8.10.14,3.0,1.0,2.0,NaN"),
        _mof("R8.10.14,3.0,1.0,2.0,Infinity"),
        _mof("R8.10.14,3.0,1.0,2.0,bad"),
        _mof("R8.10.14,3.0,1.0,2.0,1.0", "R8.10.14,3.0,1.0,2.0,1.1"),
    ],
)
def test_mof_rejects_malformed_values_instead_of_fabricating_rates(
    payload: bytes,
) -> None:
    with pytest.raises(ProviderResponseError):
        parse_mof_csv(payload, "2Y")


@pytest.mark.parametrize(
    ("observation", "publication"),
    [
        (date(2026, 10, 14), datetime(2026, 10, 15, 9, 30, tzinfo=JST)),
        # Monday October 12 is a Japanese holiday.
        (date(2026, 10, 9), datetime(2026, 10, 13, 9, 30, tzinfo=JST)),
        (date(2026, 12, 30), datetime(2027, 1, 4, 9, 30, tzinfo=JST)),
    ],
)
def test_mof_publication_uses_next_japanese_business_day(
    observation: date, publication: datetime
) -> None:
    assert mof_available_at(observation) == publication


@pytest.mark.parametrize("canonical", list(JGB_SYMBOLS))
def test_mof_fetch_merges_archive_and_current_month_and_keeps_publication_time(
    canonical: str,
) -> None:
    requested = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested.append(str(request.url))
        payload = (
            _mof("R8.9.30,3.0,1.0,2.0,0.5", "R8.10.1,3.0,1.0,2.0,0.6")
            if str(request.url) == MOF_HISTORY_URL
            else _mof("R8.10.1,3.1,1.0,2.1,0.61", "R8.10.14,3.2,1.0,2.2,0.63")
        )
        return httpx.Response(200, content=payload)

    provider = _provider(handler)
    assert isinstance(provider, MarketDataProvider)
    bars = provider.fetch_eod(_request(canonical, start=date(2026, 10, 1)))
    assert requested == [MOF_HISTORY_URL, MOF_CURRENT_URL]
    assert [bar.market_date for bar in bars] == [date(2026, 10, 1), date(2026, 10, 14)]
    expected_current = {
        "jgb_2y_yield": "0.61",
        "jgb_10y_yield": "2.1",
        "jgb_20y_yield": "3.1",
    }
    assert bars[0].close == Decimal(expected_current[canonical])
    bar = bars[-1]
    assert bar.provider == "public_factors"
    assert bar.provider_symbol == JGB_SYMBOLS[canonical]
    assert bar.available_timestamp == datetime(2026, 10, 15, 9, 30, tzinfo=JST)
    assert bar.timestamp == datetime(2026, 10, 14, 15, tzinfo=JST)
    assert bar.first_observed_at == bar.retrieved_at == NOW
    assert bar.availability_method is AvailabilityMethod.PUBLISHED_SCHEDULE
    assert bar.interval is DataInterval.EOD
    assert bar.currency == "PERCENT"
    assert bar.open is bar.adjusted_close is bar.source_timestamp is None
    assert "historical_vintage_unverified" in bar.quality_flags


def test_mof_early_response_does_not_admit_unpublished_yesterday_at_0830() -> None:
    payload = _mof("R8.10.13,3,1,2,0.6", "R8.10.14,3,1,2,0.7")
    provider = _provider(
        lambda _: httpx.Response(200, content=payload),
        now=prediction_cutoff(date(2026, 10, 15)),
    )
    bars = provider.fetch_eod(_request())
    assert [bar.market_date for bar in bars] == [date(2026, 10, 13)]


def test_fred_parses_missing_holidays_and_month_end_weekend_observations() -> None:
    payload = _fred("2026-05-29,0.9", "2026-05-30,.", "2026-05-31,0.91", "2026-06-01,")
    assert parse_fred_csv(payload, "BAMLC0A0CM") == {
        date(2026, 5, 29): Decimal("0.9"),
        date(2026, 5, 31): Decimal("0.91"),
    }


@pytest.mark.parametrize(
    "payload",
    [
        b"<html>blocked</html>",
        b"observation_date,OTHER\n2026-10-13,0.8\n",
        b"observation_date,BAMLC0A0CM\n2026-10-13\n",
        _fred("2026-02-30,0.8"),
        _fred("2026-10-13,NaN"),
        _fred("2026-10-13,Infinity"),
        _fred("2026-10-13,not a number"),
        _fred("2026-10-13,0.8", "2026-10-13,0.9"),
    ],
)
def test_fred_rejects_wrong_series_and_corrupt_values(payload: bytes) -> None:
    with pytest.raises(ProviderResponseError):
        parse_fred_csv(payload, "BAMLC0A0CM")


@pytest.mark.parametrize(
    ("observation", "available"),
    [
        (date(2026, 10, 13), datetime(2026, 10, 15, 0, tzinfo=CENTRAL)),
        # Columbus Day is skipped in this conservative publication estimate.
        (date(2026, 10, 9), datetime(2026, 10, 14, 0, tzinfo=CENTRAL)),
        (date(2026, 12, 24), datetime(2026, 12, 29, 0, tzinfo=CENTRAL)),
        (date(2026, 3, 6), datetime(2026, 3, 10, 0, tzinfo=CENTRAL)),
    ],
)
def test_fred_lag_respects_federal_holidays_weekends_and_dst(
    observation: date, available: datetime
) -> None:
    assert fred_estimated_available_at(observation) == available


@pytest.mark.parametrize("canonical", list(FRED_SYMBOLS))
def test_fred_uses_lag_for_history_and_actual_observation_for_newest(
    canonical: str,
) -> None:
    symbol = FRED_SYMBOLS[canonical]
    requested = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested.append(request)
        return httpx.Response(
            200, content=_fred("2026-10-09,0.83", "2026-10-13,0.84", symbol=symbol)
        )

    bars = _provider(handler).fetch_eod(_request(canonical))
    assert str(requested[0].url).startswith(FRED_CSV_URL)
    assert requested[0].url.params["id"] == symbol
    assert "cosd" not in requested[0].url.params
    assert "coed" not in requested[0].url.params
    historical, newest = bars
    assert historical.available_timestamp == fred_estimated_available_at(
        date(2026, 10, 9)
    )
    assert historical.availability_method is AvailabilityMethod.PROVIDER_SLA_ESTIMATE
    assert (
        "estimated_two_us_federal_business_day_release_lag" in historical.quality_flags
    )
    assert newest.available_timestamp == newest.first_observed_at == NOW
    assert newest.availability_method is AvailabilityMethod.FIRST_OBSERVED
    assert newest.timestamp < newest.available_timestamp
    assert newest.currency == "PERCENT"
    assert "historical_vintage_unverified" in newest.quality_flags
    assert "ice_internal_use_only" in newest.quality_flags
    assert all(bar.provider == "public_factors" for bar in bars)


def test_historical_range_end_does_not_turn_its_last_row_into_freds_latest() -> None:
    provider = _provider(
        lambda _: httpx.Response(200, content=_fred("2026-10-09,0.8", "2026-10-13,0.9"))
    )
    bars = provider.fetch_eod(_request("us_ig_credit_oas", end=date(2026, 10, 9)))
    assert len(bars) == 1
    assert bars[0].available_timestamp == fred_estimated_available_at(date(2026, 10, 9))


def test_fetch_before_release_estimate_records_actual_observation() -> None:
    observed = datetime(2026, 10, 14, 0, tzinfo=UTC)
    provider = _provider(
        lambda _: httpx.Response(
            200, content=_fred("2026-10-12,0.8", "2026-10-13,0.9")
        ),
        now=observed,
    )
    bars = provider.fetch_eod(_request("us_ig_credit_oas"))
    assert all(bar.available_timestamp == observed for bar in bars)
    assert all(
        bar.availability_method is AvailabilityMethod.FIRST_OBSERVED for bar in bars
    )


@pytest.mark.parametrize(
    ("symbol", "cutoff", "expected"),
    [
        ("MOF:2Y", datetime(2026, 10, 16, 8, 30, tzinfo=JST), date(2026, 10, 14)),
        (
            "jgb_10y_yield",
            datetime(2026, 10, 16, 9, 30, tzinfo=JST),
            date(2026, 10, 15),
        ),
        ("jgb_20y_yield", datetime(2026, 10, 13, 8, 30, tzinfo=JST), date(2026, 10, 8)),
        ("BAMLC0A0CM", datetime(2026, 10, 16, 8, 30, tzinfo=JST), date(2026, 10, 13)),
        (
            "us_hy_credit_oas",
            datetime(2026, 10, 16, 14, tzinfo=JST),
            date(2026, 10, 14),
        ),
    ],
)
def test_expected_latest_observation_uses_publication_calendar(
    symbol: str, cutoff: datetime, expected: date
) -> None:
    assert latest_expected_observation(cutoff, symbol) == expected


def test_freshness_rejects_unknown_symbol_and_naive_cutoff() -> None:
    with pytest.raises(ValueError, match="unsupported"):
        latest_expected_observation(NOW, "UNKNOWN")
    with pytest.raises(ValueError, match="timezone-aware"):
        latest_expected_observation(NOW.replace(tzinfo=None), "MOF:2Y")


def test_retry_honors_retry_after_without_leaking_upstream_body() -> None:
    attempts = []
    sleeps = []

    def handler(request: httpx.Request) -> httpx.Response:
        attempts.append(request)
        if len(attempts) == 1:
            return httpx.Response(429, headers={"Retry-After": "2"}, text="private")
        return httpx.Response(200, content=_fred("2026-10-13,0.8"))

    provider = _provider(handler, max_retries=1, sleeper=sleeps.append)
    assert len(provider.fetch_eod(_request("us_ig_credit_oas"))) == 1
    assert len(attempts) == 2
    assert sleeps == [2.0]
    denied = _provider(lambda _: httpx.Response(403, text="private token details"))
    with pytest.raises(ProviderFetchError, match="HTTP 403") as error:
        denied.fetch_eod(_request("us_ig_credit_oas"))
    assert "private" not in str(error.value)


def test_timeout_retries_are_bounded_and_sanitized() -> None:
    attempts = []

    def handler(request: httpx.Request) -> httpx.Response:
        attempts.append(request)
        raise httpx.ReadTimeout("private upstream text", request=request)

    with pytest.raises(ProviderFetchError, match="network or timeout"):
        _provider(handler, max_retries=2).fetch_eod(_request("us_ig_credit_oas"))
    assert len(attempts) == 3


@pytest.mark.parametrize(
    ("canonical", "symbol"),
    [
        ("jgb_2y_yield", "MOF:10Y"),
        ("us_ig_credit_oas", "BAMLH0A0HYM2"),
        ("unknown", "UNKNOWN"),
    ],
)
def test_unverified_symbol_mapping_is_rejected_before_http(
    canonical: str, symbol: str
) -> None:
    def unexpected(_: httpx.Request) -> httpx.Response:
        raise AssertionError("invalid mapping must never be fetched")

    with pytest.raises(ValueError):
        _provider(unexpected).fetch_eod(_request(canonical, symbol=symbol))


def test_hash_identifies_values_not_fetch_time_and_external_client_is_not_closed() -> (
    None
):
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=_fred("2026-10-13,0.8"))
    provider = _provider(handler)
    first = provider.fetch_eod(_request("us_ig_credit_oas"))[0]
    later = _provider(handler, now=NOW + timedelta(days=1)).fetch_eod(
        _request("us_ig_credit_oas")
    )[0]
    assert first.raw_hash == later.raw_hash
    assert first.first_observed_at != later.first_observed_at
    provider.close()
    assert not provider._client.is_closed


def test_healthcheck_checks_both_feeds_and_detects_html_error_pages() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        payload = (
            _mof("R8.10.14,3,1,2,0.8")
            if str(request.url) == MOF_CURRENT_URL
            else _fred("2026-10-13,0.8")
        )
        return httpx.Response(200, content=payload)

    assert _provider(handler).healthcheck().ok
    failed = _provider(lambda _: httpx.Response(200, text="<html>Error</html>"))
    assert not failed.healthcheck().ok


def test_owned_client_preserves_the_fred_verified_library_user_agent() -> None:
    # The live source timed out with our custom application User-Agent, while
    # the identical URL using httpx's default completed in under one second.
    provider = PublicFactorsProvider()
    assert provider._client.headers["User-Agent"].startswith("python-httpx/")
    provider.close()
    assert provider._client.is_closed
