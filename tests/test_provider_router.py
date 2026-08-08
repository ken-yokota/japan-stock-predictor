from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

from data.alignment import ProviderMixingError, latest_available
from data.provider_router import (
    EodRouteCandidate,
    ProviderRouter,
    SnapshotRouteCandidate,
)
from data.providers.base import MarketDataProvider, ProviderFetchError
from data.schemas import (
    DataInterval,
    DataQuality,
    FetchRequest,
    MarketBar,
    ProviderHealth,
    SnapshotRequest,
)
from data.snapshot import FreshnessStatus, SelectionRole, assess_snapshot

UTC = UTC
CUTOFF = datetime(2026, 8, 10, 23, 30, tzinfo=UTC)


class StubProvider(MarketDataProvider):
    def __init__(
        self,
        name: str,
        *,
        rows: list[MarketBar] | None = None,
        snapshot: MarketBar | None = None,
        error: bool = False,
    ) -> None:
        self.name = name
        self.rows = rows or []
        self.snapshot = snapshot
        self.error = error
        self.eod_calls = 0
        self.snapshot_calls = 0

    def fetch_eod(self, request: FetchRequest) -> list[MarketBar]:
        del request
        self.eod_calls += 1
        if self.error:
            raise ProviderFetchError("temporary provider failure")
        return self.rows

    def fetch_snapshot(self, request: SnapshotRequest) -> MarketBar:
        del request
        self.snapshot_calls += 1
        if self.error:
            raise ProviderFetchError("temporary provider failure")
        if self.snapshot is None:
            raise ProviderFetchError("missing snapshot")
        return self.snapshot

    def healthcheck(self) -> ProviderHealth:
        return ProviderHealth(self.name, True, CUTOFF, "ok")

    def close(self) -> None:
        return None


def request(symbol: str = "SPY") -> FetchRequest:
    return FetchRequest(
        canonical_symbol="sp500",
        provider_symbol=symbol,
        market="US",
        market_timezone="America/New_York",
        market_close="16:00",
        availability_lag_minutes=30,
        start_date=date(2026, 8, 6),
        end_date=date(2026, 8, 7),
    )


def snapshot_request(symbol: str = "ES=F") -> SnapshotRequest:
    return SnapshotRequest("sp500_futures", symbol, "US_FUTURES", "America/New_York")


def test_primary_complete_series_prevents_fallback_call(make_bar) -> None:
    primary_rows = [
        make_bar(
            canonical_symbol="sp500",
            provider="yahoo_finance",
            provider_symbol="^GSPC",
            market_date=day,
            timestamp=datetime.combine(day, datetime.min.time(), tzinfo=UTC),
            available_timestamp=datetime.combine(
                day, datetime.min.time(), tzinfo=UTC
            ),
            raw_hash=character * 64,
        )
        for day, character in ((date(2026, 8, 6), "a"), (date(2026, 8, 7), "b"))
    ]
    primary = StubProvider("yahoo_finance", rows=primary_rows)
    fallback = StubProvider("eodhd", error=True)
    result = ProviderRouter(
        {"yahoo_finance": primary, "eodhd_free": fallback}
    ).fetch_eod_series(
        [
            EodRouteCandidate("yahoo_finance", request("^GSPC")),
            EodRouteCandidate("eodhd_free", request("SPY.US")),
        ],
        required_dates={date(2026, 8, 6), date(2026, 8, 7)},
        cutoff_at=CUTOFF,
    )
    assert result.selection_role is SelectionRole.PRIMARY
    assert len(result.rows) == 2
    assert fallback.eod_calls == 0


def test_incomplete_primary_uses_complete_fallback_without_patching(make_bar) -> None:
    primary = StubProvider(
        "yahoo_finance",
        rows=[
            make_bar(
                canonical_symbol="sp500",
                provider="yahoo_finance",
                provider_symbol="^GSPC",
                market_date=date(2026, 8, 7),
            )
        ],
    )
    fallback_rows = [
        make_bar(
            canonical_symbol="sp500",
            provider="eodhd",
            provider_symbol="SPY.US",
            market_date=day,
            timestamp=datetime.combine(day, datetime.min.time(), tzinfo=UTC),
            available_timestamp=datetime.combine(
                day, datetime.min.time(), tzinfo=UTC
            ),
            raw_hash=character * 64,
            data_quality=DataQuality.EOD_CONFIRMED,
        )
        for day, character in ((date(2026, 8, 6), "c"), (date(2026, 8, 7), "d"))
    ]
    result = ProviderRouter(
        {
            "yahoo_finance": primary,
            "eodhd_free": StubProvider("eodhd", rows=fallback_rows),
        }
    ).fetch_eod_series(
        [
            EodRouteCandidate("yahoo_finance", request("^GSPC")),
            EodRouteCandidate("eodhd_free", request("SPY.US")),
        ],
        required_dates={date(2026, 8, 6), date(2026, 8, 7)},
        cutoff_at=CUTOFF,
    )
    assert result.selection_role is SelectionRole.FALLBACK
    assert {row.provider for row in result.rows} == {"eodhd"}
    assert result.attempts[0].coverage == 0.5


def test_operational_eod_rejects_rows_retrieved_after_cutoff(make_bar) -> None:
    row = make_bar(
        canonical_symbol="sp500",
        provider="yahoo_finance",
        provider_symbol="^GSPC",
        market_date=date(2026, 8, 7),
        first_observed_at=CUTOFF - timedelta(microseconds=1),
        retrieved_at=CUTOFF + timedelta(microseconds=1),
    )
    result = ProviderRouter(
        {"yahoo_finance": StubProvider("yahoo_finance", rows=[row])}
    ).fetch_eod_series(
        [EodRouteCandidate("yahoo_finance", request("^GSPC"))],
        required_dates={date(2026, 8, 7)},
        cutoff_at=CUTOFF,
        operational_run=True,
    )
    assert result.rows == ()
    assert result.attempts[0].coverage == 0.0
    assert result.attempts[0].freshness_status is FreshnessStatus.STALE


def test_snapshot_after_cutoff_and_stale_are_rejected(make_bar) -> None:
    late = make_bar(
        canonical_symbol="sp500_futures",
        provider="yahoo_finance",
        provider_symbol="ES=F",
        timestamp=CUTOFF - timedelta(minutes=5),
        available_timestamp=CUTOFF + timedelta(microseconds=1),
        first_observed_at=CUTOFF + timedelta(microseconds=1),
        retrieved_at=CUTOFF + timedelta(microseconds=1),
        interval=DataInterval.LIVE_SNAPSHOT,
        data_quality=DataQuality.DELAYED,
        is_delayed=True,
    )
    assessment = assess_snapshot(
        late, cutoff_at=CUTOFF, max_age=timedelta(minutes=20)
    )
    assert assessment.status is FreshnessStatus.AFTER_CUTOFF

    stale = make_bar(
        canonical_symbol="sp500_futures",
        provider="yahoo_finance",
        provider_symbol="ES=F",
        timestamp=CUTOFF - timedelta(minutes=21),
        available_timestamp=CUTOFF - timedelta(minutes=1),
        first_observed_at=CUTOFF - timedelta(minutes=1),
        retrieved_at=CUTOFF - timedelta(minutes=1),
        interval=DataInterval.LIVE_SNAPSHOT,
        data_quality=DataQuality.DELAYED,
        is_delayed=True,
        raw_hash="e" * 64,
    )
    result = ProviderRouter(
        {"yahoo_finance": StubProvider("yahoo_finance", snapshot=stale)}
    ).fetch_snapshot(
        [SnapshotRouteCandidate("yahoo_finance", snapshot_request())],
        cutoff_at=CUTOFF,
        max_age=timedelta(minutes=20),
    )
    assert result.row is None
    assert result.selection_role is SelectionRole.NONE
    assert result.assessment.status is FreshnessStatus.STALE


def test_alignment_requires_explicit_provider_selection_for_mixed_rows(
    make_bar,
) -> None:
    yahoo = make_bar(provider="yahoo_finance", provider_symbol="SPY")
    eodhd = make_bar(provider="eodhd", provider_symbol="SPY.US", raw_hash="f" * 64)
    try:
        latest_available([yahoo, eodhd], CUTOFF)
    except ProviderMixingError:
        pass
    else:
        raise AssertionError("mixed providers must fail closed")
    selected = latest_available(
        [yahoo, eodhd],
        CUTOFF,
        selected_providers={"SPY": "yahoo_finance"},
    )
    assert selected["SPY"].value is yahoo
