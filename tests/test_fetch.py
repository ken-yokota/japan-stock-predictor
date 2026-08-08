from __future__ import annotations

from datetime import UTC, date, datetime

from data.config import load_app_config
from data.fetch import (
    FetchPlan,
    build_fetch_plan,
    compare_free_eod,
    execute_fetch_plan,
)
from database.repository import UpsertSummary


class FakeRepository:
    def __init__(self) -> None:
        self.rows = []

    def upsert_bars(self, rows, *, stock_symbols=None):
        del stock_symbols
        self.rows.extend(rows)
        return UpsertSummary(inserted=len(rows), reused=0)


class FakeYahooProvider:
    name = "yahoo_finance"

    def __init__(self, bar) -> None:
        self.bar = bar
        self.eod_calls = 0

    def fetch_eod(self, request):
        del request
        self.eod_calls += 1
        return [self.bar]


class RequestAwareProvider:
    def __init__(self, name, rows_by_symbol) -> None:
        self.name = name
        self.rows_by_symbol = rows_by_symbol

    def fetch_eod(self, request):
        return self.rows_by_symbol[request.provider_symbol]


class FakeTreasuryProvider:
    name = "us_treasury"

    def fetch_range(self, start_date, end_date):
        del start_date, end_date
        return []


def test_default_plan_is_yahoo_first_and_free_only() -> None:
    plan = build_fetch_plan(load_app_config())
    assert len(plan.stocks) == 22
    assert len(plan.eod) == 17
    assert len(plan.snapshots) == 12
    assert len(plan.treasury_symbols) == 3
    assert plan.unresolved_required == ("iron_ore",)
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
    proxy_target = next(
        item for item in plan.eod if item.canonical_symbol == "sp500"
    )
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
            "eodhd_free": RequestAwareProvider(
                "eodhd", {"XLE.US": [eodhd_row]}
            ),  # type: ignore[dict-item]
        },
        start_date=date(2026, 8, 7),
        end_date=date(2026, 8, 7),
        max_series=1,
    )
    assert "sp500" not in result["results"]
    assert result["results"]["xle"]["status"] == "COMPARED"
    assert result["results"]["xle"]["relative_close_difference"] == 0
