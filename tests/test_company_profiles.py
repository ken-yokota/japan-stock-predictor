"""Business profiles remain fixed, dated and observable at the cutoff."""

from __future__ import annotations

from collections.abc import Iterator
from copy import deepcopy
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from shutil import copyfile
from typing import Any

import pytest
import yaml
from pydantic import ValidationError
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from data.availability import prediction_cutoff
from data.company_profiles import CompanyFactor, CompanyProfile, CompanyProfilesConfig
from data.config import AppConfig, ConfigError, load_app_config
from data.feature_registry import TickerFeatureSet
from data.market_calendar import japan_session_close, japan_sessions_before
from database.models import MarketData, StockPrice
from services.dataset import ModelSample, PointInTimeDatasetBuilder
from services.versioning import config_hash

PROJECT_ROOT = Path(__file__).resolve().parents[1]
PROFILE_DATE = date(2026, 10, 15)
SERIES_IDS = (
    "usdjpy",
    "nikkei225_futures",
    "us_2y_yield",
    "vix",
    "wti",
    "copper",
    "fxi",
    "eurjpy",
    "xlf",
    "xli",
    "brent",
)


def _factor(index: int, **updates: Any) -> dict[str, Any]:
    return {
        "id": f"driver_{index}",
        "label": f"Driver {index}",
        "series_id": SERIES_IDS[index],
        "transform": "return_1d",
        "frequency": "daily",
        "max_age_days": 7,
        "mechanism": "A distinct economic driver for this synthetic test profile.",
        "acquisition": "Synthetic observations; not evidence of a working data feed.",
        "source_urls": ["https://example.com/official-source"],
        **updates,
    }


def _profile_payload(count: int = 7, **updates: Any) -> dict[str, Any]:
    return {
        "ticker": "8306",
        "name": "Synthetic financial profile",
        "valid_from": PROFILE_DATE,
        "thesis": "Seven separate drivers with coefficients estimated daily.",
        "evidence": "Synthetic fixture; no investment efficacy assertion.",
        "factors": [_factor(index) for index in range(count)],
        **updates,
    }


def _profile(**updates: Any) -> CompanyProfile:
    return CompanyProfile.model_validate(_profile_payload(**updates))


@pytest.fixture
def base_config() -> AppConfig:
    payload = load_app_config().model_dump(mode="python")
    payload["company_profiles"] = {"version": 1, "profiles": []}
    return AppConfig.model_validate(payload)


def _config(base: AppConfig, *profiles: CompanyProfile) -> AppConfig:
    payload = base.model_dump(mode="python")
    payload["company_profiles"] = {
        "version": 1,
        "profiles": [profile.model_dump(mode="python") for profile in profiles],
    }
    return AppConfig.model_validate(payload)


@pytest.fixture
def session() -> Iterator[Session]:
    # No real database or external service is used. Raw observations below are
    # supplied directly, retaining the production visibility/feature code.
    engine = create_engine("sqlite+pysqlite:///:memory:")
    with Session(engine) as value:
        yield value
    engine.dispose()


def _row(
    series_id: str,
    day: date,
    index: int,
    *,
    close: float | None = None,
    stock: bool = False,
    available_at: datetime | None = None,
) -> MarketData | StockPrice:
    event_at = japan_session_close(day)
    available = available_at or event_at + timedelta(minutes=20)
    value = Decimal(str(close if close is not None else 100 + index))
    row_type = StockPrice if stock else MarketData
    return row_type(
        id=index + 1,
        canonical_symbol=series_id,
        symbol=f"SYNTHETIC-{series_id}",
        provider="yahoo_finance",
        market="JP",
        market_timezone="Asia/Tokyo",
        market_date=day,
        timestamp=event_at,
        source_timestamp=event_at,
        available_timestamp=available,
        first_observed_at=available,
        retrieved_at=available,
        last_seen_at=available,
        interval="eod",
        availability_method="provider_sla_estimate",
        data_quality="FREE_UNVERIFIED",
        is_realtime=False,
        is_delayed=True,
        open=value - Decimal("0.5"),
        high=value + Decimal(1),
        low=value - Decimal(1),
        close=value,
        adjusted_close=value,
        volume=1_000_000,
        currency="JPY",
        raw_hash=f"{index + 1:064x}",
        quality_flags=[],
    )


def _history(
    prediction_date: date, count: int = 15
) -> tuple[list[StockPrice], dict[tuple[str, str], list[MarketData]]]:
    sessions = japan_sessions_before(prediction_date, count)
    stocks = [
        _row("8306", day, index, stock=True) for index, day in enumerate(sessions)
    ]
    markets = {
        (series_id, "eod"): [
            _row(series_id, day, index) for index, day in enumerate(sessions)
        ]
        for series_id in SERIES_IDS[:7]
    }
    return stocks, markets  # type: ignore[return-value]


def _builder(
    session: Session,
    config: AppConfig,
    monkeypatch: pytest.MonkeyPatch,
    stocks: list[StockPrice],
    markets: dict[tuple[str, str], list[MarketData]],
) -> PointInTimeDatasetBuilder:
    builder = PointInTimeDatasetBuilder(session, config)
    monkeypatch.setattr(builder, "_load_rows", lambda *_args: (stocks, markets))
    # Freeze only the pre-profile registry entry; the dated profile takes precedence.
    assert config.ticker_features is not None
    config.ticker_features.tickers["8306"] = TickerFeatureSet(
        selected=["usdjpy"], reason="Synthetic legacy fixture"
    )
    return builder


def _sample(
    builder: PointInTimeDatasetBuilder,
    profile: CompanyProfile,
    markets: dict[tuple[str, str], list[MarketData]],
    *,
    day: date = PROFILE_DATE,
    operational: bool = True,
) -> ModelSample:
    return builder._sample(
        ticker="8306",
        sample_date=day,
        stock_rows=[],
        market_rows=markets,
        indicator_ids=builder._indicator_ids("8306", day),
        operational=operational,
        target_cutoff_at=prediction_cutoff(day),
        company_profile=profile,
    )


@pytest.mark.parametrize("count", [7, 8, 9, 10])
def test_profile_accepts_seven_to_ten_distinct_groups(count: int) -> None:
    assert len(CompanyProfile.model_validate(_profile_payload(count)).factors) == count


@pytest.mark.parametrize("count", [0, 6, 11])
def test_profile_rejects_group_count_outside_range(count: int) -> None:
    with pytest.raises(ValidationError):
        CompanyProfile.model_validate(_profile_payload(count))


@pytest.mark.parametrize(
    "updates",
    [
        {"max_age_days": True},
        {"max_age_days": "7"},
        {"max_age_days": 0},
        {"max_age_days": 371},
        {"frequency": "yearly"},
        {"transform": "return_20d"},
        {"id": "Driver-1"},
        {"label": ""},
        {"source_urls": []},
        {"unexpected_field": True},
    ],
)
def test_factor_validation_is_strict(updates: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        CompanyFactor.model_validate(_factor(0, **updates))


@pytest.mark.parametrize("field", ["id", "series_id"])
def test_duplicate_economic_series_cannot_be_counted_twice(field: str) -> None:
    payload = _profile_payload()
    payload["factors"][1][field] = payload["factors"][0][field]
    # Choosing another transform does not turn one series into another driver.
    payload["factors"][1]["transform"] = "level"
    with pytest.raises(ValidationError, match="distinct series"):
        CompanyProfile.model_validate(payload)


def test_distinct_unbound_requirements_are_preserved() -> None:
    profile = _profile(factors=[_factor(index, series_id=None) for index in range(7)])
    assert all(factor.series_id is None for factor in profile.factors)
    assert len({factor.id for factor in profile.factors}) == 7


@pytest.mark.parametrize("invalid", ["2026-10-15", "2026-02-30", 20261015, None])
def test_profile_date_is_not_silently_coerced(invalid: object) -> None:
    with pytest.raises(ValidationError):
        _profile(valid_from=invalid)


def test_profile_resolution_uses_latest_applicable_date_only() -> None:
    old = _profile(valid_from=date(2026, 1, 1))
    new = _profile()
    config = CompanyProfilesConfig(profiles=[new, old])
    assert config.resolve("8306", date(2025, 12, 31)) is None
    assert config.resolve("8306", date(2026, 10, 14)) == old
    assert config.resolve("8306", PROFILE_DATE) == new
    assert config.resolve("8316", PROFILE_DATE) is None
    with pytest.raises(
        ValidationError, match="duplicate ticker/profile effective date"
    ):
        CompanyProfilesConfig(profiles=[old, old])


@pytest.mark.parametrize(
    ("updates", "message"),
    [
        ({"ticker": "9999"}, "unknown company profile ticker"),
        (
            {
                "factors": [_factor(0, series_id="unregistered_series")]
                + [_factor(index) for index in range(1, 7)]
            },
            "unknown company factor binding",
        ),
    ],
)
def test_app_config_checks_cross_file_references(
    base_config: AppConfig, updates: dict[str, Any], message: str
) -> None:
    with pytest.raises(ValidationError, match=message):
        _config(base_config, _profile(**updates))


def test_optional_profile_yaml_loads_without_breaking_legacy_config(
    tmp_path: Path,
) -> None:
    for name in ("stocks", "indicators", "settings", "model", "trading"):
        copyfile(PROJECT_ROOT / "config" / f"{name}.yaml", tmp_path / f"{name}.yaml")
    assert load_app_config(tmp_path).company_profiles.profiles == []
    profile_path = tmp_path / "company_profiles.yaml"
    profile_path.write_text(
        yaml.safe_dump({"version": 1, "profiles": [_profile_payload()]}),
        encoding="utf-8",
    )
    assert load_app_config(tmp_path).company_profiles.resolve("8306", PROFILE_DATE)
    invalid = _profile_payload(valid_from="2026-10-15")
    profile_path.write_text(
        yaml.safe_dump({"version": 1, "profiles": [invalid]}), encoding="utf-8"
    )
    with pytest.raises(ConfigError):
        load_app_config(tmp_path)


@pytest.mark.parametrize(
    "change", ["binding", "transform", "freshness", "date", "evidence"]
)
def test_profile_changes_are_included_in_config_hash(
    base_config: AppConfig, change: str
) -> None:
    payload = _profile_payload()
    before = _config(base_config, CompanyProfile.model_validate(payload))
    changed = deepcopy(payload)
    if change == "binding":
        changed["factors"][0]["series_id"] = "eurjpy"
    elif change == "transform":
        changed["factors"][0]["transform"] = "difference_1d"
    elif change == "freshness":
        changed["factors"][0]["max_age_days"] = 6
    elif change == "date":
        changed["valid_from"] = date(2026, 10, 16)
    else:
        changed["evidence"] = "A different dated business rationale."
    after = _config(base_config, CompanyProfile.model_validate(changed))
    assert config_hash(before) != config_hash(after)
    assert config_hash(before) == config_hash(
        AppConfig.model_validate(before.model_dump())
    )


def test_dataset_has_one_column_per_factor_and_no_stock_padding(
    session: Session, base_config: AppConfig, monkeypatch: pytest.MonkeyPatch
) -> None:
    factors = [_factor(index) for index in range(7)]
    factors[1]["transform"] = "difference_1d"
    factors[2]["transform"] = "level"
    profile = _profile(factors=factors)
    stocks, markets = _history(PROFILE_DATE)
    builder = _builder(
        session, _config(base_config, profile), monkeypatch, stocks, markets
    )
    dataset = builder.build("8306", PROFILE_DATE, training_sessions=10)
    expected = {factor.feature_name for factor in profile.factors}
    assert set(dataset.feature_names) == expected
    assert len(dataset.feature_names) == dataset.candidate_feature_count == 7
    assert not any(name.startswith("stock__") for name in dataset.feature_names)
    assert not any("snapshot" in name for name in dataset.feature_names)
    assert dataset.missing_profile_factors == ()
    assert dataset.indicator_coverage == 1.0
    assert dataset.current_sample.reference_price == float(stocks[-1].close)
    assert dataset.current_sample.target_return is None
    assert dataset.current_sample.values["driver_0__return_1d"] == pytest.approx(
        114 / 113 - 1
    )
    assert dataset.current_sample.values["driver_1__difference_1d"] == pytest.approx(
        1.0
    )
    assert dataset.current_sample.values["driver_2__level"] == pytest.approx(114.0)
    assert len(dataset.current_sample.lineage["driver_0__return_1d"]) == 2
    assert len(dataset.current_sample.lineage["driver_2__level"]) == 1


@pytest.mark.parametrize(
    "missing_kind", ["unbound", "pending", "disabled", "absent", "stale", "history"]
)
def test_missing_profile_driver_is_required_and_never_replaced(
    session: Session,
    base_config: AppConfig,
    monkeypatch: pytest.MonkeyPatch,
    missing_kind: str,
) -> None:
    factors = [_factor(index) for index in range(7)]
    stocks, markets = _history(PROFILE_DATE)
    if missing_kind == "unbound":
        factors[0]["series_id"] = None
    elif missing_kind == "pending":
        factors[0]["series_id"] = "iron_ore"
        # Even pre-existing raw rows cannot make an unverified binding usable.
        markets[("iron_ore", "eod")] = markets[("usdjpy", "eod")]
    elif missing_kind == "disabled":
        factors[0]["series_id"] = "sp500"
        markets[("sp500", "eod")] = markets[("usdjpy", "eod")]
    elif missing_kind == "absent":
        del markets[("usdjpy", "eod")]
    elif missing_kind == "stale":
        markets[("usdjpy", "eod")] = markets[("usdjpy", "eod")][:2]
    else:
        # Present in the current row, but insufficient in the training window.
        markets[("usdjpy", "eod")] = markets[("usdjpy", "eod")][-2:]
    profile = _profile(factors=factors)
    builder = _builder(
        session, _config(base_config, profile), monkeypatch, stocks, markets
    )
    dataset = builder.build("8306", PROFILE_DATE, training_sessions=10)
    assert dataset.expected_indicators == tuple(factor.id for factor in profile.factors)
    assert dataset.missing_profile_factors == ("driver_0",)
    assert dataset.candidate_feature_count == 7
    assert dataset.feature_coverage == pytest.approx(6 / 7)
    assert dataset.missing_required_indicators == ("driver_0",)
    assert dataset.missing_optional_indicators == ()
    assert dataset.indicator_coverage == pytest.approx(6 / 7)
    assert len(dataset.feature_names) == 6
    assert not any(name.startswith("stock__") for name in dataset.feature_names)


@pytest.mark.parametrize(
    "late_field",
    ["timestamp", "available_timestamp", "first_observed_at", "retrieved_at"],
)
def test_profile_input_after_cutoff_is_excluded(
    session: Session,
    base_config: AppConfig,
    late_field: str,
) -> None:
    profile = _profile(
        factors=[_factor(index, transform="level") for index in range(7)]
    )
    builder = PointInTimeDatasetBuilder(session, _config(base_config, profile))
    day = date(2026, 10, 14)
    prior = _row("usdjpy", day, 1, close=100)
    revision = _row("usdjpy", day, 2, close=999)
    setattr(
        revision,
        late_field,
        prediction_cutoff(PROFILE_DATE) + timedelta(microseconds=1),
    )
    sample = _sample(builder, profile, {("usdjpy", "eod"): [prior, revision]})
    assert sample.values["driver_0__level"] == 100
    assert sample.lineage["driver_0__level"][0].row_id == prior.id
    sample.assert_safe(operational=True)


def test_backfilled_history_still_requires_market_publication_before_cutoff(
    session: Session, base_config: AppConfig
) -> None:
    profile = _profile(
        factors=[_factor(index, transform="level") for index in range(7)]
    )
    builder = PointInTimeDatasetBuilder(session, _config(base_config, profile))
    cutoff = prediction_cutoff(PROFILE_DATE)
    older = _row("usdjpy", date(2026, 10, 13), 1, close=100)
    public_but_fetched_late = _row("usdjpy", date(2026, 10, 14), 2, close=101)
    public_but_fetched_late.first_observed_at = cutoff + timedelta(days=1)
    public_but_fetched_late.retrieved_at = cutoff + timedelta(days=1)
    # Model the documented MOF release at 09:30 JST on the following day.
    unpublished = _row(
        "usdjpy",
        date(2026, 10, 14),
        3,
        close=999,
        available_at=cutoff + timedelta(hours=1),
    )
    markets = {("usdjpy", "eod"): [older, public_but_fetched_late, unpublished]}
    live = _sample(builder, profile, markets)
    replay = _sample(builder, profile, markets, operational=False)
    assert live.values["driver_0__level"] == 100
    assert replay.values["driver_0__level"] == 101
    assert replay.lineage["driver_0__level"][0].row_id == public_but_fetched_late.id


@pytest.mark.parametrize("transform", ["return_1d", "difference_1d"])
def test_daily_change_rejects_a_stale_comparison_observation(
    session: Session, base_config: AppConfig, transform: str
) -> None:
    profile = _profile(
        factors=[_factor(index, transform=transform) for index in range(7)]
    )
    builder = PointInTimeDatasetBuilder(session, _config(base_config, profile))
    markets = {
        ("usdjpy", "eod"): [
            _row("usdjpy", date(2026, 9, 1), 1, close=100),
            _row("usdjpy", date(2026, 10, 14), 2, close=110),
        ]
    }
    sample = _sample(builder, profile, markets)
    assert profile.factors[0].feature_name not in sample.values


def test_historical_backtest_does_not_use_a_later_researched_profile(
    session: Session, base_config: AppConfig, monkeypatch: pytest.MonkeyPatch
) -> None:
    stocks, markets = _history(PROFILE_DATE)
    profile = _profile(factors=[_factor(index, series_id=None) for index in range(7)])
    builder = _builder(
        session, _config(base_config, profile), monkeypatch, stocks, markets
    )
    result = builder.build_backtest_frame(
        "8306",
        stocks[2].market_date,
        stocks[-1].market_date,
        initial_training_sessions=4,
    )
    assert any(name.startswith("stock__") for name in result.feature_names)
    assert any(name.startswith("usdjpy__") for name in result.feature_names)
    assert not any(name.startswith("driver_") for name in result.feature_names)
    assert result.availability_evidence == "ESTIMATED_BACKFILL"


def test_backtest_cannot_silently_cross_profile_effective_date(
    session: Session, base_config: AppConfig, monkeypatch: pytest.MonkeyPatch
) -> None:
    stocks, markets = _history(PROFILE_DATE)
    profile = _profile(valid_from=stocks[8].market_date)
    builder = _builder(
        session, _config(base_config, profile), monkeypatch, stocks, markets
    )
    with pytest.raises(ValueError, match="crosses an indicator profile change"):
        builder.build_backtest_frame(
            "8306",
            stocks[2].market_date,
            stocks[-1].market_date,
            initial_training_sessions=4,
        )


def test_active_profile_backtest_requires_all_factors_in_oos_rows(
    session: Session, base_config: AppConfig, monkeypatch: pytest.MonkeyPatch
) -> None:
    stocks, markets = _history(PROFILE_DATE, count=25)
    factors = [_factor(index, transform="level") for index in range(7)]
    factors[0]["max_age_days"] = 1
    profile = _profile(valid_from=stocks[0].market_date, factors=factors)
    # A series that stops after initial training must not be imputed through OOS.
    markets[("usdjpy", "eod")] = markets[("usdjpy", "eod")][:12]
    builder = _builder(
        session, _config(base_config, profile), monkeypatch, stocks, markets
    )
    with pytest.raises(ValueError, match="company profile has missing factors"):
        builder.build_backtest_frame(
            "8306",
            stocks[2].market_date,
            stocks[-1].market_date,
            initial_training_sessions=8,
            minimum_feature_coverage=0.5,
        )


def test_shipped_profiles_cover_every_company_with_distinct_business_sets() -> None:
    """The delivered universe must not silently leave a stock on the old pool."""
    from data.config import load_app_config

    config = load_app_config()
    on = date(2026, 10, 16)
    tickers = {stock.ticker for stock in config.stocks.stocks if stock.enabled}
    profiles = [config.company_profiles.resolve(ticker, on) for ticker in tickers]
    assert len(profiles) == 22
    assert all(profile is not None for profile in profiles)
    resolved = [profile for profile in profiles if profile is not None]
    assert all(7 <= len(profile.factors) <= 10 for profile in resolved)
    assert len({frozenset(f.id for f in p.factors) for p in resolved}) == 22
    assert all(f.id != "stock" for p in resolved for f in p.factors)
    assert all(
        config.company_profiles.resolve(ticker, date(2026, 10, 15)) is None
        for ticker in tickers
    )
    # These are reviewed targets with explicit acquisition work still pending.
