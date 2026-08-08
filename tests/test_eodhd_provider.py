from __future__ import annotations

from datetime import UTC, date, datetime

import httpx
import pytest

from data.providers.base import (
    ProviderEntitlementError,
    ProviderFetchError,
    ProviderResponseError,
)
from data.providers.eodhd import EODHDFreeProvider, EodhdProvider
from data.schemas import AvailabilityMethod, DataQuality, FetchRequest

UTC = UTC
NOW = datetime(2026, 8, 8, 0, 0, tzinfo=UTC)


def provider_for(handler, **kwargs) -> EodhdProvider:
    transport = httpx.MockTransport(handler)
    client = httpx.Client(transport=transport, base_url="https://eodhd.test/api")
    return EodhdProvider(
        "super-secret-token",
        client=client,
        clock=lambda: NOW,
        sleeper=lambda _: None,
        **kwargs,
    )


def test_fetch_eod_normalizes_availability_and_ohlcv() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/eod/SPY.US"
        assert request.url.params["api_token"] == "super-secret-token"
        return httpx.Response(
            200,
            json=[
                {
                    "date": "2026-08-07",
                    "open": 100,
                    "high": 103,
                    "low": 99,
                    "close": 102,
                    "adjusted_close": 102,
                    "volume": 1234,
                }
            ],
        )

    provider = provider_for(handler)
    rows = provider.fetch_eod(
        FetchRequest(
            canonical_symbol="SPY",
            provider_symbol="SPY.US",
            market="US",
            market_timezone="America/New_York",
            market_close="16:00",
            availability_lag_minutes=15,
            start_date=date(2026, 8, 7),
            end_date=date(2026, 8, 7),
            currency="USD",
        )
    )
    assert len(rows) == 1
    assert rows[0].timestamp == datetime(2026, 8, 7, 20, 0, tzinfo=UTC)
    assert rows[0].available_timestamp == datetime(2026, 8, 7, 20, 15, tzinfo=UTC)
    assert rows[0].availability_method is AvailabilityMethod.PROVIDER_SLA_ESTIMATE
    assert rows[0].data_quality is DataQuality.EOD_CONFIRMED
    assert rows[0].raw_hash is not None


def test_live_quote_uses_first_observed_not_source_timestamp() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "code": "JPY.FOREX",
                "timestamp": int(datetime(2026, 8, 7, 23, 59, tzinfo=UTC).timestamp()),
                "open": 147,
                "high": 148,
                "low": 146,
                "close": 147.5,
                "volume": 0,
            },
        )

    row = provider_for(handler).fetch_live(
        canonical_symbol="USDJPY",
        provider_symbol="JPY.FOREX",
        market="FOREX",
        market_timezone="UTC",
    )
    assert row.source_timestamp < row.available_timestamp
    assert row.available_timestamp == NOW
    assert row.availability_method is AvailabilityMethod.FIRST_OBSERVED
    assert row.data_quality is DataQuality.DELAYED


def test_retry_429_and_5xx_then_success() -> None:
    statuses = iter([429, 503, 200])
    delays: list[float] = []

    def handler(_: httpx.Request) -> httpx.Response:
        status = next(statuses)
        return httpx.Response(status, json=[])

    provider = provider_for(handler, max_retries=2)
    provider._sleeper = delays.append  # test-only injected clock boundary
    assert provider.list_exchanges() == []
    assert delays == [1.0, 2.0]


def test_timeout_is_retried_and_error_is_sanitized() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        raise httpx.ReadTimeout("contains super-secret-token", request=request)

    provider = provider_for(handler, max_retries=1)
    with pytest.raises(ProviderFetchError) as captured:
        provider.list_exchanges()
    assert calls == 2
    assert "super-secret-token" not in str(captured.value)
    assert "super-secret-token" not in repr(provider)


def test_non_retryable_404_only_runs_once() -> None:
    calls = 0

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(404, json={"error": "missing"})

    with pytest.raises(ProviderFetchError):
        provider_for(handler, max_retries=3).list_exchanges()
    assert calls == 1


def test_malformed_payload_is_rejected() -> None:
    provider = provider_for(lambda _: httpx.Response(200, json={"not": "a list"}))
    with pytest.raises(ProviderResponseError):
        provider.list_exchanges()


def test_symbol_resolution_uses_metadata_and_does_not_guess_suffix() -> None:
    requested_paths: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested_paths.append(request.url.path)
        if request.url.path.endswith("/exchanges-list/"):
            return httpx.Response(
                200,
                json=[
                    {
                        "Code": "US",
                        "CountryISO2": "US",
                        "OperatingMIC": "XNAS,XNYS",
                    }
                ],
            )
        return httpx.Response(
            200,
            json=[
                {
                    "Code": "AAPL",
                    "Name": "Apple Inc",
                    "Exchange": "US",
                    "Currency": "USD",
                }
            ],
        )

    resolution = provider_for(handler).resolve_symbol(
        canonical_symbol="AAPL", country_iso="US", exchange_mic="XNAS"
    )
    assert resolution is not None
    assert resolution.provider_symbol == "AAPL.US"
    assert requested_paths[-1].endswith("/exchange-symbol-list/US")


def test_unlisted_exchange_returns_none_without_inventing_symbol() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/exchanges-list/")
        return httpx.Response(
            200,
            json=[{"Code": "US", "CountryISO2": "US", "OperatingMIC": "XNYS"}],
        )

    assert (
        provider_for(handler).resolve_symbol(
            canonical_symbol="9101", country_iso="JP", exchange_mic="XTKS"
        )
        is None
    )


def test_free_plan_rejects_history_older_than_one_year_before_network() -> None:
    calls = 0

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, json=[])

    client = httpx.Client(
        transport=httpx.MockTransport(handler), base_url="https://eodhd.test/api"
    )
    provider = EODHDFreeProvider(
        "secret",
        client=client,
        clock=lambda: NOW,
        sleeper=lambda _: None,
    )
    with pytest.raises(ProviderEntitlementError, match="one year"):
        provider.fetch_eod(
            FetchRequest(
                canonical_symbol="SPY",
                provider_symbol="SPY.US",
                market="US",
                market_timezone="America/New_York",
                market_close="16:00",
                availability_lag_minutes=15,
                start_date=date(2025, 8, 6),
                end_date=date(2026, 8, 7),
            )
        )
    assert calls == 0


def test_free_plan_enforces_small_per_run_call_budget() -> None:
    client = httpx.Client(
        transport=httpx.MockTransport(lambda _: httpx.Response(200, json=[])),
        base_url="https://eodhd.test/api",
    )
    provider = EODHDFreeProvider(
        "secret",
        client=client,
        clock=lambda: NOW,
        sleeper=lambda _: None,
        max_calls_per_run=1,
    )
    assert provider.list_exchanges() == []
    with pytest.raises(ProviderEntitlementError, match="budget"):
        provider.list_symbols("US")
    assert provider.calls_used == 1


def test_free_plan_does_not_claim_generic_live_entitlement() -> None:
    client = httpx.Client(
        transport=httpx.MockTransport(lambda _: httpx.Response(200, json={})),
        base_url="https://eodhd.test/api",
    )
    provider = EODHDFreeProvider("secret", client=client, clock=lambda: NOW)
    with pytest.raises(ProviderEntitlementError):
        provider.fetch_live(
            canonical_symbol="usdjpy",
            provider_symbol="JPY.FOREX",
            market="FOREX",
            market_timezone="UTC",
        )
