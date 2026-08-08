from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any

import pandas as pd
import pytest

from data.providers.base import ProviderFetchError, ProviderResponseError
from data.providers.yahoo import YahooFinanceProvider
from data.schemas import (
    AvailabilityMethod,
    DataInterval,
    DataQuality,
    FetchRequest,
    SessionOpenRequest,
    SnapshotRequest,
)

NOW = datetime(2026, 8, 10, 23, 29, tzinfo=UTC)


class FakeBackend:
    def __init__(
        self,
        frame: pd.DataFrame,
        *,
        failures: int = 0,
        search_rows: list[dict[str, Any]] | None = None,
    ) -> None:
        self.frame = frame
        self.failures = failures
        self.search_rows = search_rows or []
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def history(self, symbol: str, **kwargs: Any) -> pd.DataFrame:
        self.calls.append((symbol, kwargs))
        if self.failures:
            self.failures -= 1
            raise TimeoutError("temporary upstream failure")
        return self.frame

    def search(self, query: str, *, max_results: int) -> list[dict[str, Any]]:
        del query, max_results
        return self.search_rows


def daily_frame() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "Open": [3000.0],
            "High": [3050.0],
            "Low": [2980.0],
            "Close": [3025.0],
            "Adj Close": [3010.0],
            "Volume": [1_000_000],
        },
        index=pd.DatetimeIndex(["2026-08-10"], tz="Asia/Tokyo"),
    )


def test_fetch_eod_uses_explicit_unadjusted_history_and_end_exclusive() -> None:
    backend = FakeBackend(daily_frame())
    provider = YahooFinanceProvider(
        backend=backend, clock=lambda: NOW, sleeper=lambda _: None
    )
    rows = provider.fetch_eod(
        FetchRequest(
            canonical_symbol="7203",
            provider_symbol="7203.T",
            market="JP",
            market_timezone="Asia/Tokyo",
            market_close="15:30",
            availability_lag_minutes=20,
            start_date=date(2026, 8, 10),
            end_date=date(2026, 8, 10),
            currency="JPY",
        )
    )

    assert len(rows) == 1
    row = rows[0]
    assert row.provider == "yahoo_finance"
    assert row.data_quality is DataQuality.FREE_UNVERIFIED
    assert row.adjusted_close is not None
    assert row.market_timestamp == datetime(2026, 8, 10, 6, 30, tzinfo=UTC)
    assert row.availability_method is AvailabilityMethod.PROVIDER_SLA_ESTIMATE
    _, kwargs = backend.calls[0]
    assert kwargs["auto_adjust"] is False
    assert kwargs["repair"] is False
    assert kwargs["end"] == "2026-08-11"


def test_snapshot_preserves_market_and_retrieval_timestamps() -> None:
    frame = pd.DataFrame(
        {
            "Open": [147.0],
            "High": [147.2],
            "Low": [146.9],
            "Close": [147.1],
            "Volume": [0],
        },
        index=pd.DatetimeIndex([datetime(2026, 8, 10, 23, 20, tzinfo=UTC)]),
    )
    row = YahooFinanceProvider(
        backend=FakeBackend(frame), clock=lambda: NOW, sleeper=lambda _: None
    ).fetch_snapshot(
        SnapshotRequest(
            canonical_symbol="usdjpy",
            provider_symbol="JPY=X",
            market="FOREX",
            market_timezone="UTC",
        )
    )
    assert row.market_timestamp == datetime(2026, 8, 10, 23, 20, tzinfo=UTC)
    assert row.available_timestamp == NOW
    assert row.retrieved_at == NOW
    assert row.data_quality is DataQuality.DELAYED
    assert row.is_delayed is True
    assert row.is_realtime is False


def test_snapshot_rejects_future_provider_timestamp() -> None:
    frame = pd.DataFrame(
        {"Close": [147.1]},
        index=pd.DatetimeIndex([datetime(2026, 8, 10, 23, 30, tzinfo=UTC)]),
    )
    provider = YahooFinanceProvider(
        backend=FakeBackend(frame), clock=lambda: NOW, sleeper=lambda _: None
    )
    with pytest.raises(ProviderResponseError):
        provider.fetch_snapshot(SnapshotRequest("usdjpy", "JPY=X", "FOREX", "UTC"))


def test_session_open_uses_first_regular_minute_with_observed_availability() -> None:
    observed = datetime(2026, 8, 11, 0, 2, tzinfo=UTC)
    frame = pd.DataFrame(
        {
            "Open": [3000.0, 3010.0],
            "High": [3012.0, 3015.0],
            "Low": [2995.0, 3005.0],
            "Close": [3010.0, 3012.0],
            "Volume": [50_000, 20_000],
        },
        index=pd.DatetimeIndex(
            [
                datetime(2026, 8, 11, 9, 0),
                datetime(2026, 8, 11, 9, 1),
            ],
            tz="Asia/Tokyo",
        ),
    )
    backend = FakeBackend(frame)
    row = YahooFinanceProvider(
        backend=backend,
        clock=lambda: observed,
        sleeper=lambda _: None,
    ).fetch_session_open(
        SessionOpenRequest(
            canonical_symbol="7203",
            provider_symbol="7203.T",
            market="JP",
            market_timezone="Asia/Tokyo",
            session_date=date(2026, 8, 11),
            session_open="09:00",
            currency="JPY",
        )
    )

    assert row.interval is DataInterval.ONE_MINUTE
    assert row.open == 3000
    assert row.market_timestamp == datetime(2026, 8, 11, 0, 0, tzinfo=UTC)
    assert row.available_timestamp == observed
    assert row.first_observed_at == observed
    _, kwargs = backend.calls[0]
    assert kwargs["interval"] == "1m"
    assert kwargs["prepost"] is False


def test_history_retries_and_returns_sanitized_typed_error() -> None:
    provider = YahooFinanceProvider(
        backend=FakeBackend(daily_frame(), failures=3),
        max_retries=1,
        clock=lambda: NOW,
        sleeper=lambda _: None,
    )
    with pytest.raises(ProviderFetchError, match="TimeoutError"):
        provider.fetch_eod(
            FetchRequest(
                canonical_symbol="7203",
                provider_symbol="7203.T",
                market="JP",
                market_timezone="Asia/Tokyo",
                market_close="15:30",
                availability_lag_minutes=20,
                start_date=date(2026, 8, 10),
                end_date=date(2026, 8, 10),
            )
        )


def test_resolve_symbol_uses_search_metadata_without_suffix_inference() -> None:
    backend = FakeBackend(
        daily_frame(),
        search_rows=[
            {
                "symbol": "7203.T",
                "exchange": "JPX",
                "shortname": "Toyota Motor Corporation",
                "currency": "JPY",
            }
        ],
    )
    result = YahooFinanceProvider(
        backend=backend, clock=lambda: NOW, sleeper=lambda _: None
    ).resolve_symbol(canonical_symbol="7203", country_iso="JP", exchange_mic="XTKS")
    assert result is not None
    assert result.provider_symbol == "7203.T"
