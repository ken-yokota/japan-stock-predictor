from __future__ import annotations

from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from data.config import load_app_config
from data.fetch import (
    FetchPlan,
    build_fetch_plan,
    compare_free_eod,
    execute_fetch_plan,
)
from data.schemas import DataInterval, MarketBar
from data.treasury_features import TREASURY_CHANGE_SYMBOLS
from database.models import Base
from database.repository import MarketDataRepository, UpsertSummary


class FakeRepository:
    def __init__(self, coverage: dict[str, date] | None = None) -> None:
        self.rows: list[MarketBar] = []
        self.coverage = dict(coverage or {})
        self.coverage_calls = 0
        self.coverage_cutoffs: list[datetime | None] = []

    def stored_coverage(
        self, interval: str = "eod", *, cutoff_at: datetime | None = None
    ) -> dict[str, date]:
        assert interval == "eod"
        self.coverage_calls += 1
        self.coverage_cutoffs.append(cutoff_at)
        return dict(self.coverage)

    def upsert_bars(self, rows, *, stock_symbols=None):
        del stock_symbols
        self.rows.extend(rows)
        for row in rows:
            if row.interval is not DataInterval.EOD:
                continue
            latest = self.coverage.get(row.canonical_symbol)
            if latest is None or row.market_date > latest:
                self.coverage[row.canonical_symbol] = row.market_date
        return UpsertSummary(inserted=len(rows), reused=0)


class FakeYahooProvider:
    name = "yahoo_finance"

    def __init__(self, bar, *, snapshot_bar=None) -> None:
        self.bar = bar
        self.eod_calls = 0
        self.eod_requests = []
        self.snapshot_bar = snapshot_bar
        self.snapshot_calls = 0
        self.snapshot_requests = []

    def fetch_eod(self, request):
        self.eod_calls += 1
        self.eod_requests.append(request)
        return [self.bar]

    def fetch_snapshot(self, request):
        self.snapshot_calls += 1
        self.snapshot_requests.append(request)
        return self.snapshot_bar


class RequestAwareProvider:
    def __init__(self, name, rows_by_symbol) -> None:
        self.name = name
        self.rows_by_symbol = rows_by_symbol
        self.eod_requests = []

    def fetch_eod(self, request):
        self.eod_requests.append(request)
        return self.rows_by_symbol[request.provider_symbol]


class FakeTreasuryProvider:
    name = "us_treasury"

    def __init__(self, rows: list[MarketBar] | None = None) -> None:
        self.rows = list(rows or [])
        self.calls: list[tuple[date, date]] = []

    def fetch_range(self, start_date, end_date):
        self.calls.append((start_date, end_date))
        return [row for row in self.rows if start_date <= row.market_date <= end_date]


def test_default_plan_is_yahoo_first_and_free_only() -> None:
    plan = build_fetch_plan(load_app_config())
    assert len(plan.stocks) == 22
    # The twelve snapshot indicators also carry a daily-history source. Without
    # it they reach the model with no past sessions at all, which is why they
    # were absent from every stored feature set.
    assert len(plan.eod) == 29
    assert len(plan.snapshots) == 12
    snapshot_ids = {target.canonical_symbol for target in plan.snapshots}
    assert snapshot_ids <= {target.canonical_symbol for target in plan.eod}
    assert len(plan.treasury_symbols) == 3
    # Direct iron-ore data is unavailable in the free stack, so the indicator
    # is explicitly optional instead of making every operational run PARTIAL.
    assert plan.unresolved_required == ()
    assert all(target.primary.provider == "yahoo_finance" for target in plan.eod)
    assert all(
        target.fallback is None or target.fallback.provider == "eodhd_free"
        for target in plan.eod
    )


def test_execute_plan_persists_selected_primary_series(make_bar) -> None:
    default = build_fetch_plan(load_app_config())
    target = default.eod[0]
    plan = FetchPlan(
        stocks=(),
        eod=(target,),
        snapshots=(),
        treasury_symbols=(),
        unresolved_required=("iron_ore",),
    )
    row = make_bar(
        canonical_symbol=target.canonical_symbol,
        provider="yahoo_finance",
        provider_symbol=target.primary.provider_symbol,
        market_date=date(2026, 8, 7),
    )
    repository = FakeRepository()
    report = execute_fetch_plan(
        plan,
        market_providers={
            "yahoo_finance": FakeYahooProvider(row)  # type: ignore[dict-item]
        },
        treasury_provider=FakeTreasuryProvider(),  # type: ignore[arg-type]
        repository=repository,  # type: ignore[arg-type]
        start_date=date(2026, 8, 7),
        end_date=date(2026, 8, 7),
        cutoff_at=datetime(2026, 8, 8, 0, 0, tzinfo=UTC),
        include_snapshots=False,
    )
    assert report.succeeded_sources == 1
    assert report.inserted_rows == 1
    assert report.unresolved_required == ["iron_ore"]
    assert report.status == "PARTIAL"
    assert report.selected_providers[target.canonical_symbol] == "yahoo_finance"
    assert len(repository.rows) == 1


def test_snapshots_are_explicitly_excluded_when_not_requested() -> None:
    default = build_fetch_plan(load_app_config())
    target = default.snapshots[0]
    plan = FetchPlan(
        stocks=(),
        eod=(),
        snapshots=(target,),
        treasury_symbols=(),
        unresolved_required=(),
    )
    repository = FakeRepository()
    report = execute_fetch_plan(
        plan,
        market_providers={
            "yahoo_finance": FakeYahooProvider(None)  # type: ignore[dict-item]
        },
        treasury_provider=FakeTreasuryProvider(),  # type: ignore[arg-type]
        repository=repository,  # type: ignore[arg-type]
        start_date=date(2026, 8, 7),
        end_date=date(2026, 8, 7),
        cutoff_at=datetime(2026, 8, 8, 0, 0, tzinfo=UTC),
        include_snapshots=False,
    )
    assert report.succeeded_sources == 0
    assert report.skipped_sources == {
        target.canonical_symbol: "08:30 snapshot fetch not requested"
    }
    assert report.status == "PARTIAL"


def test_free_comparison_uses_only_equivalent_listed_instruments(make_bar) -> None:
    plan = build_fetch_plan(load_app_config())
    proxy_target = next(item for item in plan.eod if item.canonical_symbol == "sp500")
    target = next(item for item in plan.eod if item.canonical_symbol == "xle")
    yahoo_row = make_bar(
        canonical_symbol="xle",
        provider="yahoo_finance",
        provider_symbol="XLE",
    )
    eodhd_row = make_bar(
        canonical_symbol="xle",
        provider="eodhd",
        provider_symbol="XLE.US",
        raw_hash="b" * 64,
    )
    result = compare_free_eod(
        FetchPlan(
            stocks=(),
            eod=(proxy_target, target),
            snapshots=(),
            treasury_symbols=(),
            unresolved_required=(),
        ),
        market_providers={
            "yahoo_finance": RequestAwareProvider(
                "yahoo_finance", {"XLE": [yahoo_row]}
            ),  # type: ignore[dict-item]
            "eodhd_free": RequestAwareProvider("eodhd", {"XLE.US": [eodhd_row]}),  # type: ignore[dict-item]
        },
        start_date=date(2026, 8, 7),
        end_date=date(2026, 8, 7),
        max_series=1,
    )
    assert "sp500" not in result["results"]
    assert result["results"]["xle"]["status"] == "COMPARED"
    assert result["results"]["xle"]["relative_close_difference"] == 0


def test_monday_reuses_friday_eod_and_treasury_but_fetches_snapshot_each_run(
    make_bar,
) -> None:
    """A Sunday end date must resolve to Friday, while snapshots stay live."""

    default = build_fetch_plan(load_app_config())
    stock = default.stocks[0]
    eod = default.eod[0]
    snapshot = default.snapshots[0]
    plan = FetchPlan(
        stocks=(stock,),
        eod=(eod,),
        snapshots=(snapshot,),
        treasury_symbols=default.treasury_symbols,
        unresolved_required=(),
    )
    friday = date(2026, 8, 7)
    coverage = {
        stock.ticker: friday,
        eod.canonical_symbol: friday,
        **{symbol: friday for symbol in default.treasury_symbols},
    }
    cutoff_at = datetime(2026, 8, 10, 8, 30, tzinfo=ZoneInfo("Asia/Tokyo"))
    snapshot_at = datetime(2026, 8, 9, 23, 25, tzinfo=UTC)
    snapshot_row = make_bar(
        canonical_symbol=snapshot.canonical_symbol,
        provider="yahoo_finance",
        provider_symbol=snapshot.source.provider_symbol,
        market_date=date(2026, 8, 10),
        timestamp=snapshot_at,
        available_timestamp=snapshot_at,
        first_observed_at=snapshot_at,
        retrieved_at=snapshot_at,
        interval=DataInterval.LIVE_SNAPSHOT,
        is_delayed=True,
    )
    provider = FakeYahooProvider(None, snapshot_bar=snapshot_row)
    treasury = FakeTreasuryProvider()
    repository = FakeRepository(coverage)

    reports = [
        execute_fetch_plan(
            plan,
            market_providers={"yahoo_finance": provider},  # type: ignore[dict-item]
            treasury_provider=treasury,  # type: ignore[arg-type]
            repository=repository,  # type: ignore[arg-type]
            start_date=date(2026, 8, 1),
            end_date=date(2026, 8, 9),
            cutoff_at=cutoff_at,
            include_snapshots=True,
            operational_run=True,
            skip_covered=True,
        )
        for _ in range(2)
    ]

    assert provider.eod_calls == 0
    assert treasury.calls == []
    assert provider.snapshot_calls == 2
    assert repository.coverage_calls == 2
    assert repository.coverage_cutoffs == [cutoff_at, cutoff_at]
    assert all(report.status == "SUCCESS" for report in reports)
    assert set(reports[0].covered_sources) == {
        stock.ticker,
        eod.canonical_symbol,
        *default.treasury_symbols,
    }


def test_holiday_and_cutoff_use_market_target_without_current_day_lookahead(
    make_bar,
) -> None:
    """11 Aug is closed in Tokyo but open in New York in 2026."""

    default = build_fetch_plan(load_app_config())
    stock = default.stocks[0]
    eod = default.eod[0]
    plan = FetchPlan(
        stocks=(stock,),
        eod=(eod,),
        snapshots=(),
        treasury_symbols=(),
        unresolved_required=(),
    )
    us_row = make_bar(
        canonical_symbol=eod.canonical_symbol,
        provider="yahoo_finance",
        provider_symbol=eod.primary.provider_symbol,
        market_date=date(2026, 8, 11),
        timestamp=datetime(2026, 8, 11, 20, 0, tzinfo=UTC),
        available_timestamp=datetime(2026, 8, 11, 20, 30, tzinfo=UTC),
        first_observed_at=datetime(2026, 8, 11, 23, 0, tzinfo=UTC),
        retrieved_at=datetime(2026, 8, 11, 23, 0, tzinfo=UTC),
    )
    provider = FakeYahooProvider(us_row)
    repository = FakeRepository(
        {
            # Tokyo's last session is Monday 10 Aug: already complete.
            stock.ticker: date(2026, 8, 10),
            # New York is open on Tuesday 11 Aug: one session is missing.
            eod.canonical_symbol: date(2026, 8, 10),
        }
    )

    report = execute_fetch_plan(
        plan,
        market_providers={"yahoo_finance": provider},  # type: ignore[dict-item]
        treasury_provider=FakeTreasuryProvider(),  # type: ignore[arg-type]
        repository=repository,  # type: ignore[arg-type]
        start_date=date(2026, 8, 1),
        # Even when a caller includes the current calendar date, neither the
        # in-progress Tokyo nor New York session may enter the request.
        end_date=date(2026, 8, 12),
        cutoff_at=datetime(2026, 8, 12, 8, 30, tzinfo=ZoneInfo("Asia/Tokyo")),
        include_snapshots=False,
        operational_run=True,
        skip_covered=True,
    )

    assert report.status == "SUCCESS"
    assert set(report.covered_sources) == {stock.ticker}
    assert provider.eod_calls == 1
    request = provider.eod_requests[0]
    assert request.canonical_symbol == eod.canonical_symbol
    assert request.start_date == date(2026, 8, 11)
    assert request.end_date == date(2026, 8, 11)


def test_one_missing_series_is_fetched_once_then_reused_on_second_run(
    make_bar,
) -> None:
    """A missing coverage entry must not disable incremental idempotency."""

    default = build_fetch_plan(load_app_config())
    covered, missing = default.eod[:2]
    friday = date(2026, 8, 7)
    missing_row = make_bar(
        canonical_symbol=missing.canonical_symbol,
        provider="yahoo_finance",
        provider_symbol=missing.primary.provider_symbol,
        market_date=friday,
    )
    provider = FakeYahooProvider(missing_row)
    repository = FakeRepository({covered.canonical_symbol: friday})
    plan = FetchPlan(
        stocks=(),
        eod=(covered, missing),
        snapshots=(),
        treasury_symbols=(),
        unresolved_required=(),
    )

    first = execute_fetch_plan(
        plan,
        market_providers={"yahoo_finance": provider},  # type: ignore[dict-item]
        treasury_provider=FakeTreasuryProvider(),  # type: ignore[arg-type]
        repository=repository,  # type: ignore[arg-type]
        start_date=friday,
        end_date=date(2026, 8, 9),
        cutoff_at=datetime(2026, 8, 10, 8, 30, tzinfo=ZoneInfo("Asia/Tokyo")),
        include_snapshots=False,
        operational_run=True,
        skip_covered=True,
    )
    second = execute_fetch_plan(
        plan,
        market_providers={"yahoo_finance": provider},  # type: ignore[dict-item]
        treasury_provider=FakeTreasuryProvider(),  # type: ignore[arg-type]
        repository=repository,  # type: ignore[arg-type]
        start_date=friday,
        end_date=date(2026, 8, 9),
        cutoff_at=datetime(2026, 8, 10, 8, 30, tzinfo=ZoneInfo("Asia/Tokyo")),
        include_snapshots=False,
        operational_run=True,
        skip_covered=True,
    )

    assert first.status == "SUCCESS"
    assert first.covered_sources.keys() == {covered.canonical_symbol}
    assert first.selected_providers == {missing.canonical_symbol: "yahoo_finance"}
    assert second.status == "SUCCESS"
    assert set(second.covered_sources) == {
        covered.canonical_symbol,
        missing.canonical_symbol,
    }
    assert provider.eod_calls == 1
    assert provider.eod_requests[0].start_date == friday
    assert provider.eod_requests[0].end_date == friday


def test_incremental_coverage_ignores_intraday_and_after_cutoff_rows(
    make_bar,
) -> None:
    """Coverage must use PIT-visible EOD rows from both persistence tables."""

    default = build_fetch_plan(load_app_config())
    stock = default.stocks[0]
    eod = default.eod[0]
    friday = date(2026, 8, 7)
    cutoff_at = datetime(2026, 8, 9, 23, 30, tzinfo=UTC)
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    try:
        with Session(engine) as session:
            repository = MarketDataRepository(session)
            repository.upsert_bars(
                [
                    # Newer intraday rows cannot stand in for the stock's EOD.
                    make_bar(
                        canonical_symbol=stock.ticker,
                        provider="yahoo_finance",
                        provider_symbol=stock.provider_symbols["yahoo_finance"],
                        market_date=friday,
                        interval=DataInterval.ONE_MINUTE,
                        raw_hash="b" * 64,
                    )
                ],
                stock_symbols={stock.ticker},
            )
            repository.upsert_bars(
                [
                    # The date is right, but this revision was observed only
                    # after the immutable prediction cutoff.
                    make_bar(
                        canonical_symbol=eod.canonical_symbol,
                        provider="yahoo_finance",
                        provider_symbol=eod.primary.provider_symbol,
                        market_date=friday,
                        first_observed_at=datetime(2026, 8, 10, 0, 0, tzinfo=UTC),
                        retrieved_at=datetime(2026, 8, 10, 0, 0, tzinfo=UTC),
                        raw_hash="c" * 64,
                    )
                ]
            )
            provider = RequestAwareProvider(
                "yahoo_finance",
                {
                    stock.provider_symbols["yahoo_finance"]: [
                        make_bar(
                            canonical_symbol=stock.ticker,
                            provider="yahoo_finance",
                            provider_symbol=stock.provider_symbols["yahoo_finance"],
                            market_date=friday,
                            raw_hash="d" * 64,
                        )
                    ],
                    eod.primary.provider_symbol: [
                        make_bar(
                            canonical_symbol=eod.canonical_symbol,
                            provider="yahoo_finance",
                            provider_symbol=eod.primary.provider_symbol,
                            market_date=friday,
                            raw_hash="e" * 64,
                        )
                    ],
                },
            )

            report = execute_fetch_plan(
                FetchPlan(
                    stocks=(stock,),
                    eod=(eod,),
                    snapshots=(),
                    treasury_symbols=(),
                    unresolved_required=(),
                ),
                market_providers={
                    "yahoo_finance": provider  # type: ignore[dict-item]
                },
                treasury_provider=FakeTreasuryProvider(),  # type: ignore[arg-type]
                repository=repository,
                start_date=friday,
                end_date=date(2026, 8, 9),
                cutoff_at=cutoff_at,
                include_snapshots=False,
                operational_run=True,
                skip_covered=True,
            )

            assert report.status == "SUCCESS"
            assert report.covered_sources == {}
            assert [request.canonical_symbol for request in provider.eod_requests] == [
                stock.ticker,
                eod.canonical_symbol,
            ]
    finally:
        engine.dispose()


def test_partially_stale_treasury_uses_only_bounded_change_warmup(
    make_bar,
) -> None:
    default = build_fetch_plan(load_app_config())
    target = date(2026, 8, 7)
    stale_symbol, *covered_symbols = default.treasury_symbols
    treasury_row = make_bar(
        canonical_symbol=stale_symbol,
        provider="us_treasury",
        provider_symbol=f"TREASURY:{stale_symbol}",
        market_date=target,
    )
    treasury = FakeTreasuryProvider([treasury_row])
    repository = FakeRepository(
        {
            stale_symbol: date(2026, 8, 6),
            **{symbol: target for symbol in covered_symbols},
        }
    )
    plan = FetchPlan(
        stocks=(),
        eod=(),
        snapshots=(),
        treasury_symbols=default.treasury_symbols,
        unresolved_required=(),
    )

    report = execute_fetch_plan(
        plan,
        market_providers={
            "yahoo_finance": FakeYahooProvider(None)  # type: ignore[dict-item]
        },
        treasury_provider=treasury,  # type: ignore[arg-type]
        repository=repository,  # type: ignore[arg-type]
        start_date=date(2026, 8, 1),
        end_date=date(2026, 8, 9),
        cutoff_at=datetime(2026, 8, 10, 8, 30, tzinfo=ZoneInfo("Asia/Tokyo")),
        include_snapshots=False,
        operational_run=True,
        skip_covered=True,
    )

    assert report.status == "SUCCESS"
    assert treasury.calls == [(date(2026, 8, 1), target)]
    assert set(report.covered_sources) == set(covered_symbols)
    assert report.selected_providers == {stale_symbol: "us_treasury"}


def test_treasury_overlap_emits_new_one_three_and_five_observation_changes(
    make_bar,
) -> None:
    default = build_fetch_plan(load_app_config())
    observations = (
        date(2026, 7, 31),
        date(2026, 8, 3),
        date(2026, 8, 4),
        date(2026, 8, 5),
        date(2026, 8, 6),
        date(2026, 8, 7),
    )
    treasury_rows = [
        make_bar(
            canonical_symbol=symbol,
            provider="us_treasury",
            provider_symbol=f"TREASURY:{symbol}",
            market_date=market_date,
            raw_hash=f"{row_number:064x}",
        )
        for row_number, (market_date, symbol) in enumerate(
            (
                (market_date, symbol)
                for market_date in observations
                for symbol in default.treasury_symbols
            ),
            start=1,
        )
    ]
    treasury = FakeTreasuryProvider(treasury_rows)
    repository = FakeRepository(
        {symbol: date(2026, 8, 6) for symbol in default.treasury_symbols}
    )

    report = execute_fetch_plan(
        FetchPlan(
            stocks=(),
            eod=(),
            snapshots=(),
            treasury_symbols=default.treasury_symbols,
            unresolved_required=(),
        ),
        market_providers={
            "yahoo_finance": FakeYahooProvider(None)  # type: ignore[dict-item]
        },
        treasury_provider=treasury,  # type: ignore[arg-type]
        repository=repository,  # type: ignore[arg-type]
        start_date=date(2025, 2, 4),
        end_date=date(2026, 8, 9),
        cutoff_at=datetime(2026, 8, 10, 8, 30, tzinfo=ZoneInfo("Asia/Tokyo")),
        include_snapshots=False,
        operational_run=True,
        skip_covered=True,
    )

    fetch_start, fetch_end = treasury.calls[0]
    assert fetch_start == date(2026, 7, 24)
    assert fetch_end == date(2026, 8, 7)
    assert fetch_start != date(2025, 2, 4)
    expected_changes = set(TREASURY_CHANGE_SYMBOLS.values())
    target_changes = {
        row.canonical_symbol
        for row in repository.rows
        if row.market_date == date(2026, 8, 7)
        and row.canonical_symbol in expected_changes
    }
    assert target_changes == expected_changes
    assert report.status == "SUCCESS"
