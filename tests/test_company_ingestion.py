"""Dated company ingestion stays complete, explicit and point-in-time safe."""

from __future__ import annotations

from dataclasses import replace
from datetime import date, timedelta
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from data.availability import prediction_cutoff
from data.company_profiles import CompanyFactor, CompanyProfile, CompanyProfilesConfig
from data.config import (
    AppConfig,
    IndicatorConfig,
    IndicatorSourceConfig,
    load_app_config,
)
from data.env import EnvironmentSettings
from data.fetch import FetchPlan, IndicatorTarget, build_fetch_plan, execute_fetch_plan
from data.providers.base import MarketDataProvider
from data.providers.treasury import TreasuryProvider
from data.schemas import MarketBar
from database.repository import MarketDataRepository, UpsertSummary
from services import ingestion

PROFILE_DATE = date(2026, 10, 15)
PREDICTION_DATE = date(2026, 10, 16)
CUTOFF = prediction_cutoff(PREDICTION_DATE)
PUBLIC_ID = "jgb_2y_yield"
COMMON_IDS = ("usdjpy", "nikkei225_futures", "us_2y_yield", "wti", "copper", "fxi")


@pytest.fixture
def public_source() -> IndicatorSourceConfig:
    return IndicatorSourceConfig(
        kind="direct",
        provider="public_factors",
        provider_symbol="MOF:2Y",
        endpoint="https://example.com/synthetic-publication.csv",
        market="JP",
        market_timezone="Asia/Tokyo",
        data_mode="yield_curve",
        session_policy="provider_published_at_or_before_cutoff",
        is_proxy=False,
        status="verified",
        availability_at_0830="conditional",
    )


def _factor(series_id: str | None, *, factor_id: str | None = None) -> CompanyFactor:
    return CompanyFactor(
        id=factor_id or str(series_id),
        label=factor_id or str(series_id),
        series_id=series_id,
        frequency="daily",
        max_age_days=7,
        mechanism="Distinct synthetic economic series for ingestion contracts.",
        acquisition="Synthetic test; no external feed is contacted.",
        source_urls=["https://example.com/synthetic-evidence"],
    )


@pytest.fixture
def company_config(public_source: IndicatorSourceConfig) -> AppConfig:
    base = load_app_config()
    indicator = IndicatorConfig(
        id=PUBLIC_ID,
        name="Synthetic published series",
        category="yield",
        required=True,
        resolution_status="resolved",
        sources=[public_source],
    )
    profiles = [
        CompanyProfile(
            ticker=stock.ticker,
            name=stock.name,
            valid_from=PROFILE_DATE,
            thesis="Seven fixed factors for this synthetic ingestion test.",
            evidence="Synthetic fixture; not an investment recommendation.",
            factors=[_factor(value) for value in (*COMMON_IDS, PUBLIC_ID)],
        )
        for stock in base.stocks.stocks
        if stock.enabled
    ]
    return base.model_copy(
        update={
            "company_profiles": CompanyProfilesConfig(profiles=profiles),
            "indicators": base.indicators.model_copy(
                update={
                    "indicators": [
                        *(
                            item
                            for item in base.indicators.indicators
                            if item.id != PUBLIC_ID
                        ),
                        indicator,
                    ]
                }
            ),
        }
    )


def _planned_ids(plan: FetchPlan) -> set[str]:
    return {
        *(target.canonical_symbol for target in plan.eod),
        *(target.canonical_symbol for target in plan.snapshots),
        *(target.canonical_symbol for target in plan.published_series),
        *plan.treasury_symbols,
    }


def test_all_22_active_profiles_exclude_unused_legacy_indicators(
    company_config: AppConfig,
) -> None:
    legacy = build_fetch_plan(
        company_config, prediction_date=PROFILE_DATE - timedelta(days=1)
    )
    active = build_fetch_plan(company_config, prediction_date=PREDICTION_DATE)
    assert len(active.stocks) == len(company_config.company_profiles.profiles) == 22
    assert {stock.ticker for stock in active.stocks} == {
        stock.ticker for stock in legacy.stocks
    }
    assert "sp500" in _planned_ids(legacy)
    assert _planned_ids(active) == {*COMMON_IDS, PUBLIC_ID}
    assert [target.canonical_symbol for target in active.published_series] == [
        PUBLIC_ID
    ]
    assert PUBLIC_ID not in {target.canonical_symbol for target in active.eod}
    assert active.unresolved_required == ()
    assert active.external_target_count == (
        len(active.stocks)
        + len(active.eod)
        + len(active.snapshots)
        + len(active.treasury_symbols)
        + 1
    )


def test_plan_resolves_profile_effective_date_instead_of_latest_version(
    company_config: AppConfig,
) -> None:
    future_date = date(2026, 10, 20)
    future = [
        profile.model_copy(
            update={
                "valid_from": future_date,
                "factors": [
                    _factor("vix") if factor.id == "fxi" else factor
                    for factor in profile.factors
                ],
            }
        )
        for profile in company_config.company_profiles.profiles
    ]
    config = company_config.model_copy(
        update={
            "company_profiles": CompanyProfilesConfig(
                profiles=[*company_config.company_profiles.profiles, *future]
            )
        }
    )
    earlier = _planned_ids(build_fetch_plan(config, prediction_date=PREDICTION_DATE))
    later = _planned_ids(build_fetch_plan(config, prediction_date=future_date))
    assert "fxi" in earlier and "vix" not in earlier
    assert "vix" in later and "fxi" not in later


def test_unbound_company_factor_is_unresolved_even_when_absent_from_catalog(
    company_config: AppConfig,
) -> None:
    profiles = [
        profile.model_copy(
            update={
                "factors": [
                    *profile.factors[:-1],
                    _factor(None, factor_id="unbound_business_input"),
                ]
            }
        )
        for profile in company_config.company_profiles.profiles
    ]
    config = company_config.model_copy(
        update={"company_profiles": CompanyProfilesConfig(profiles=profiles)}
    )
    plan = build_fetch_plan(config, prediction_date=PREDICTION_DATE)
    assert plan.unresolved_required == ("unbound_business_input",)
    assert _planned_ids(plan) == set(COMMON_IDS)
    assert not plan.published_series


@pytest.fixture
def public_plan(public_source: IndicatorSourceConfig) -> FetchPlan:
    return FetchPlan(
        stocks=(),
        eod=(),
        snapshots=(),
        treasury_symbols=(),
        unresolved_required=(),
        published_series=(
            IndicatorTarget(PUBLIC_ID, "Published test", True, public_source),
        ),
    )


def _repository() -> Mock:
    repository = Mock(spec=MarketDataRepository)
    repository.stored_coverage.return_value = {}
    repository.upsert_bars.side_effect = lambda rows, **_: UpsertSummary(
        inserted=len(rows), reused=0
    )
    return repository


def _provider(rows: list[MarketBar]) -> Mock:
    provider = Mock(spec=MarketDataProvider)
    provider.name = "public_factors"
    provider.fetch_eod.return_value = rows
    return provider


def _execute(plan: FetchPlan, provider: Mock, repository: Mock, **kwargs):
    return execute_fetch_plan(
        plan,
        market_providers={"public_factors": provider},
        treasury_provider=Mock(spec=TreasuryProvider),
        repository=repository,
        start_date=date(2026, 9, 1),
        end_date=PREDICTION_DATE,
        cutoff_at=CUTOFF,
        include_snapshots=False,
        run_id="synthetic-run",
        **kwargs,
    )


@pytest.fixture
def published_bar(make_bar) -> MarketBar:
    return make_bar(
        canonical_symbol=PUBLIC_ID,
        provider="public_factors",
        provider_symbol="MOF:2Y",
        market_date=date(2026, 10, 14),
        timestamp=CUTOFF - timedelta(days=2),
        available_timestamp=CUTOFF - timedelta(hours=12),
        first_observed_at=CUTOFF - timedelta(minutes=20),
        retrieved_at=CUTOFF - timedelta(minutes=10),
    )


@pytest.mark.parametrize(
    "late_field", ["available_timestamp", "first_observed_at", "retrieved_at"]
)
def test_operational_publications_exclude_any_observation_after_fixed_cutoff(
    public_plan: FetchPlan,
    published_bar: MarketBar,
    late_field: str,
) -> None:
    changes = {late_field: CUTOFF + timedelta(seconds=1), "raw_hash": "b" * 64}
    # Keep the normalized bar's publication <= observed <= retrieved invariant.
    if late_field == "available_timestamp":
        changes["first_observed_at"] = CUTOFF + timedelta(seconds=1)
    if late_field in {"available_timestamp", "first_observed_at"}:
        changes["retrieved_at"] = CUTOFF + timedelta(seconds=1)
    late = replace(published_bar, **changes)
    repository = _repository()
    report = _execute(
        public_plan, _provider([published_bar, late]), repository, operational_run=True
    )
    assert report.status == "SUCCESS", report.failed_sources
    assert report.inserted_rows == 1
    assert repository.upsert_bars.call_args.args[0] == [published_bar]
    selection = repository.save_provider_selection.call_args.kwargs
    assert selection["selected_provider"] == "public_factors"
    assert selection["cutoff_at"] == CUTOFF
    assert selection["details"]["availability"] == "provider_publication_calendar"


def test_late_first_observation_alone_fails_closed(
    public_plan: FetchPlan,
    published_bar: MarketBar,
) -> None:
    late = replace(
        published_bar,
        first_observed_at=CUTOFF + timedelta(minutes=1),
        retrieved_at=CUTOFF + timedelta(minutes=1),
    )
    repository = _repository()
    report = _execute(public_plan, _provider([late]), repository, operational_run=True)
    assert report.status == "FAILED"
    assert "cutoff" in report.failed_sources[PUBLIC_ID]
    repository.upsert_bars.assert_not_called()
    repository.save_provider_selection.assert_not_called()


@pytest.mark.parametrize("wrong_identity", ["canonical_symbol", "provider"])
def test_publications_reject_mixed_provenance_before_any_persistence(
    public_plan: FetchPlan,
    published_bar: MarketBar,
    wrong_identity: str,
) -> None:
    other = replace(published_bar, **{wrong_identity: "different_series_or_provider"})
    repository = _repository()
    report = _execute(public_plan, _provider([published_bar, other]), repository)
    assert report.status == "FAILED"
    assert "mixed provenance" in report.failed_sources[PUBLIC_ID]
    repository.upsert_bars.assert_not_called()


def test_public_series_overlap_refetches_publications_even_with_recent_coverage(
    public_plan: FetchPlan,
    published_bar: MarketBar,
) -> None:
    repository = _repository()
    repository.stored_coverage.return_value = {PUBLIC_ID: date(2026, 10, 14)}
    provider = _provider([published_bar])
    report = _execute(
        public_plan, provider, repository, skip_covered=True, operational_run=True
    )
    request = provider.fetch_eod.call_args.args[0]
    assert report.status == "SUCCESS", report.failed_sources
    assert request.start_date == date(2026, 9, 30)
    assert request.canonical_symbol == PUBLIC_ID
    assert request.provider_symbol == "MOF:2Y"
    assert request.availability_lag_minutes == 0  # Release calendar is provider-owned.
    repository.stored_coverage.assert_called_once_with(cutoff_at=CUTOFF)


def test_scheduled_morning_registers_public_provider_and_passes_prediction_date(
    company_config: AppConfig,
    published_bar: MarketBar,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Use real published execution with synthetic feeds and a mock repository."""
    public = _provider([published_bar])
    yahoo = Mock(spec=MarketDataProvider)
    treasury = Mock(spec=TreasuryProvider)
    monkeypatch.setattr(ingestion, "YahooFinanceProvider", Mock(return_value=yahoo))
    monkeypatch.setattr(ingestion, "TreasuryProvider", Mock(return_value=treasury))
    # raising=False makes missing registration an assertion failure without importing
    # the real HTTP provider or depending on when its implementation is installed.
    constructor = Mock(return_value=public)
    monkeypatch.setattr(ingestion, "PublicFactorsProvider", constructor, raising=False)
    repository = _repository()
    repository.create_run.return_value = SimpleNamespace(run_id="morning-test")
    repository.create_ingestion_batch.return_value = SimpleNamespace(batch_id="test")
    monkeypatch.setattr(
        ingestion, "MarketDataRepository", Mock(return_value=repository)
    )
    session = Mock()
    factory = Mock(return_value=session)
    planned_dates: list[date | None] = []

    def dated_plan(
        config: AppConfig, *, prediction_date: date | None = None
    ) -> FetchPlan:
        planned_dates.append(prediction_date)
        plan = build_fetch_plan(config, prediction_date=prediction_date)
        # Other markets have their own tests. Preserve real publication targets and
        # unresolved state while preventing unnecessary synthetic stock requests.
        return replace(plan, stocks=(), eod=(), snapshots=(), treasury_symbols=())

    monkeypatch.setattr(ingestion, "build_fetch_plan", dated_plan)
    outcome = ingestion.ingest_free_morning_data(
        factory,
        company_config,
        EnvironmentSettings(_env_file=None, eodhd_api_key=None),
        prediction_date=PREDICTION_DATE,
        start_date=date(2026, 9, 1),
        end_date=PREDICTION_DATE,
        include_snapshots=False,
    )
    assert planned_dates == [PREDICTION_DATE]
    constructor.assert_called_once()
    assert outcome.report.status == "SUCCESS"
    assert outcome.report.selected_providers == {PUBLIC_ID: "public_factors"}
    assert repository.upsert_bars.call_args.args[0] == [published_bar]
    assert repository.create_run.call_args.kwargs["cutoff_at"] == CUTOFF
    public.close.assert_called_once()
    yahoo.close.assert_called_once()
    treasury.close.assert_called_once()
    session.close.assert_called_once()


def test_operational_publication_requires_latest_expected_observation(
    public_plan: FetchPlan,
    published_bar: MarketBar,
) -> None:
    # A three-day-old MOF value is inside the generic seven-day limit, but a
    # newer observation has already been published before this morning's cutoff.
    old = replace(
        published_bar,
        market_date=date(2026, 10, 13),
        timestamp=published_bar.timestamp - timedelta(days=1),
    )
    repository = _repository()
    report = _execute(public_plan, _provider([old]), repository, operational_run=True)
    assert report.status == "FAILED"
    assert PUBLIC_ID in report.failed_sources
    repository.upsert_bars.assert_not_called()


def test_missing_registered_public_provider_is_reported_without_fake_rows(
    public_plan: FetchPlan,
) -> None:
    repository = _repository()
    report = execute_fetch_plan(
        public_plan,
        market_providers={"yahoo_finance": Mock(spec=MarketDataProvider)},
        treasury_provider=Mock(spec=TreasuryProvider),
        repository=repository,
        start_date=date(2026, 9, 1),
        end_date=PREDICTION_DATE,
        cutoff_at=CUTOFF,
        include_snapshots=False,
        operational_run=True,
    )
    assert report.status == "FAILED"
    assert "not configured" in report.failed_sources[PUBLIC_ID]
    repository.upsert_bars.assert_not_called()
