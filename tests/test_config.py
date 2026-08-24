"""Tests for strict YAML configuration loading and cross-file validation."""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any, cast

import pytest
import yaml
from pydantic import ValidationError

from data.config import (
    AppConfig,
    ConfigError,
    IndicatorsConfig,
    ModelConfig,
    SettingsConfig,
    StocksConfig,
    TradingConfig,
    load_app_config,
    load_indicators_config,
    load_model_config,
    load_settings_config,
    load_stocks_config,
    load_trading_config,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]
CONFIG_DIR = PROJECT_ROOT / "config"

EXPECTED_TICKERS = {
    "1605",
    "5019",
    "5020",
    "5021",
    "7201",
    "7203",
    "7267",
    "7269",
    "7270",
    "8001",
    "8002",
    "8031",
    "8053",
    "8058",
    "8306",
    "8316",
    "8411",
    "8604",
    "8766",
    "9101",
    "9104",
    "9107",
}
EXPECTED_SECTORS = {
    "shipping",
    "oil_energy",
    "automotive",
    "financial",
    "trading_company",
}
EXPECTED_EODHD_FALLBACK_SYMBOLS = {
    "DIA.US",
}
EXPECTED_YAHOO_INDICATOR_SYMBOLS = {
    "^DJI",
    "^VIX",
}
# No snapshot series remain: the twelve FX and futures indicators that
# needed the 08:20 window were cut, which is what removed the morning's
# most fragile step entirely.
EXPECTED_YAHOO_SNAPSHOT_MAX_AGE: dict[str, int] = {}


def _yaml_mapping(filename: str) -> dict[str, Any]:
    """Load a test fixture as a mutable mapping."""

    with (CONFIG_DIR / filename).open("r", encoding="utf-8") as stream:
        value = yaml.safe_load(stream)
    assert isinstance(value, dict)
    return cast(dict[str, Any], value)


def test_default_stock_config_has_all_22_requested_stocks() -> None:
    """The initial universe matches the requested security codes and sectors."""

    config = load_stocks_config()

    assert len(config.stocks) == 22
    assert {stock.ticker for stock in config.stocks} == EXPECTED_TICKERS
    assert {stock.sector for stock in config.stocks} == EXPECTED_SECTORS
    assert all(stock.country_iso == "JP" for stock in config.stocks)
    assert all(stock.exchange_mic == "XTKS" for stock in config.stocks)
    assert all(stock.market_timezone == "Asia/Tokyo" for stock in config.stocks)


def test_stock_provider_symbols_use_verified_yahoo_t_suffix() -> None:
    """Yahoo carries verified `.T` symbols while unsupported EODHD stays null."""

    config = load_stocks_config()

    for stock in config.stocks:
        assert stock.provider_symbols == {
            "yahoo_finance": f"{stock.ticker}.T",
            "eodhd": None,
        }


def test_indicator_config_has_common_and_all_five_sector_groups() -> None:
    """Common and sector-specific assignments form a complete catalog."""

    config = load_indicators_config()
    indicator_ids = {indicator.id for indicator in config.indicators}
    assigned_ids = set(config.common)
    for group in config.sectors.values():
        assigned_ids.update(group.indicators)

    assert set(config.sectors) == EXPECTED_SECTORS
    # The catalog was cut to the six series that measured highest across all
    # 22 tickers; every sector now inherits exactly those and adds nothing.
    assert len(config.common) == 6
    assert indicator_ids == assigned_ids
    assert config.sectors["trading_company"].indicators == []
    assert config.sectors["trading_company"].notes is not None


def test_indicator_sources_make_resolution_state_explicit() -> None:
    """Source roles distinguish verified routes from unresolved candidates."""

    config = load_indicators_config()
    source_kinds = {
        source.kind for indicator in config.indicators for source in indicator.sources
    }

    assert source_kinds == {"direct", "proxy", "derived"}
    for indicator in config.indicators:
        for source in indicator.sources:
            if source.status == "pending":
                assert source.availability_at_0830 == "pending"
                assert source.provider_symbol is None
                assert source.endpoint is None


def test_officially_confirmed_symbols_and_endpoints_are_verified() -> None:
    """Primary Yahoo and optional EODHD identifiers remain provider-specific."""

    config = load_indicators_config()
    verified_sources = [
        source
        for indicator in config.indicators
        for source in indicator.sources
        if source.status == "verified"
    ]
    yahoo_symbols = {
        source.provider_symbol
        for source in verified_sources
        if source.provider == "yahoo_finance" and source.provider_symbol is not None
    }
    eodhd_symbols = {
        source.provider_symbol
        for source in verified_sources
        if source.provider == "eodhd_free" and source.provider_symbol is not None
    }

    assert yahoo_symbols == EXPECTED_YAHOO_INDICATOR_SYMBOLS
    assert eodhd_symbols == EXPECTED_EODHD_FALLBACK_SYMBOLS

    for source in verified_sources:
        if source.data_mode == "derived":
            assert source.session_policy == "derived_from_inputs"
            continue
        assert source.provider in {"yahoo_finance", "us_treasury", "eodhd_free"}
        assert source.market is not None
        assert source.market_timezone is not None
        assert source.endpoint is not None
        assert source.availability_at_0830 in {"available", "conditional"}

        if source.data_mode == "eod":
            assert source.endpoint in {
                "/eod/{ticker}",
                "yfinance.Ticker.history",
            }
            assert source.market in {"US", "US_INDEX", "FOREX", "FUTURES"}
            # The daily index Yahoo returns is stamped in the venue's own
            # timezone, and reading it in any other one moves a bar onto the
            # wrong calendar day. FX comes back in London, everything else in
            # New York, so the config must name the matching close.
            expected_zone = (
                "Europe/London" if source.market == "FOREX" else "America/New_York"
            )
            expected_close = {
                "FOREX": "22:00",
                "FUTURES": "17:00",
            }.get(source.market, "16:00")
            assert source.market_timezone == expected_zone
            assert source.market_close == expected_close
            assert source.session_policy == "previous_completed_session"
        elif source.data_mode == "real_time":
            assert source.endpoint in {
                "/real-time/{ticker}",
                "yfinance.Ticker.history",
            }
            assert source.market in {"FOREX", "FUTURES"}
            assert source.market_timezone == "UTC"
            assert source.session_policy == "latest_at_or_before_cutoff"
            assert source.is_proxy is False
        elif source.data_mode == "yield_curve":
            assert source.provider in {"us_treasury", "eodhd_free"}
            assert source.availability_at_0830 == "conditional"
            assert source.session_policy == "provider_published_at_or_before_cutoff"


def test_yahoo_snapshot_sources_have_explicit_freshness_limits() -> None:
    """Only approved Yahoo snapshots carry source-specific maximum ages."""

    config = load_indicators_config()
    snapshot_limits = {
        source.provider_symbol: source.max_age_minutes
        for indicator in config.indicators
        for source in indicator.sources
        if source.snapshot_enabled
    }

    assert snapshot_limits == EXPECTED_YAHOO_SNAPSHOT_MAX_AGE
    assert all(
        source.provider == "yahoo_finance" and source.status == "verified"
        for indicator in config.indicators
        for source in indicator.sources
        if source.snapshot_enabled
    )


def test_official_treasury_is_primary_yield_source() -> None:
    """Every configured Treasury tenor includes the official XML feed."""

    config = load_indicators_config()
    treasury_sources = [
        source
        for indicator in config.indicators
        if indicator.id in {"us_2y_yield", "us_10y_yield", "us_30y_yield"}
        for source in indicator.sources
        if source.provider == "us_treasury"
    ]

    assert len(treasury_sources) == 3
    assert all(source.status == "verified" for source in treasury_sources)
    assert all(source.data_mode == "yield_curve" for source in treasury_sources)
    assert all(
        source.endpoint is not None
        and source.endpoint.startswith("https://home.treasury.gov/")
        for source in treasury_sources
    )


def test_eodhd_free_verified_fallback_is_eod_only() -> None:
    """Unconfirmed Free-plan live and Treasury entitlements stay disabled."""

    config = load_indicators_config()
    verified_eodhd = [
        source
        for indicator in config.indicators
        for source in indicator.sources
        if source.provider == "eodhd_free" and source.status == "verified"
    ]

    assert len(verified_eodhd) == 22
    assert all(source.data_mode == "eod" for source in verified_eodhd)
    assert all(source.provider_symbol is not None for source in verified_eodhd)

    unavailable_entitlements = [
        source
        for indicator in config.indicators
        for source in indicator.sources
        if source.provider == "eodhd_free"
        and source.instrument_hint
        in {"USDJPY", "EURJPY", "AUDJPY", "US 2Y Yield", "US 10Y Yield", "US 30Y Yield"}
    ]
    assert len(unavailable_entitlements) == 6
    assert all(source.status == "pending" for source in unavailable_entitlements)
    assert all(source.provider_symbol is None for source in unavailable_entitlements)
    assert all(source.endpoint is None for source in unavailable_entitlements)


def test_reduced_catalog_carries_no_unresolved_or_snapshot_routes() -> None:
    """The cut removed every route that could not be relied on in the morning.

    The catalog was reduced to the six series that measured highest across all
    22 tickers. That happens to drop every snapshot source -- the twelve FX and
    futures series that could only be captured inside a ten-minute window and
    routinely were not -- so the morning no longer has that step at all.
    """

    config = load_indicators_config()

    assert len(config.indicators) == 6
    for indicator in config.indicators:
        assert indicator.required, indicator.id
        for source in indicator.sources:
            assert not source.snapshot_enabled, indicator.id
            assert source.max_age_minutes is None, indicator.id
            if source.status == "verified":
                assert source.data_mode in {"eod", "yield_curve", "derived"}

def test_snapshot_policy_is_independent_from_historical_data_mode() -> None:
    """A Yahoo EOD source may opt into snapshots without changing its session."""

    raw = _yaml_mapping("indicators.yaml")
    yahoo_sp500 = raw["indicators"][0]["sources"][0]
    assert yahoo_sp500["data_mode"] == "eod"
    yahoo_sp500["snapshot_enabled"] = True
    yahoo_sp500["max_age_minutes"] = 20

    IndicatorsConfig.model_validate(raw)


def test_non_yahoo_source_cannot_enable_snapshots() -> None:
    """Fallback sources cannot bypass the Yahoo-only freshness contract."""

    raw = _yaml_mapping("indicators.yaml")
    eodhd_spy = raw["indicators"][0]["sources"][2]
    eodhd_spy["snapshot_enabled"] = True
    eodhd_spy["max_age_minutes"] = 20

    with pytest.raises(ValidationError, match="only for verified Yahoo Finance"):
        IndicatorsConfig.model_validate(raw)


def test_enabled_snapshot_requires_source_specific_max_age() -> None:
    """A snapshot route cannot fall back to an undocumented unlimited age."""

    raw = _yaml_mapping("indicators.yaml")
    usdjpy = next(item for item in raw["indicators"] if item["id"] == "usdjpy")
    yahoo_fx = usdjpy["sources"][0]
    del yahoo_fx["max_age_minutes"]

    with pytest.raises(ValidationError, match="require max_age_minutes"):
        IndicatorsConfig.model_validate(raw)


def test_settings_match_specified_phase_one_defaults() -> None:
    """Operational defaults retain the confirmed conservative assumptions."""

    config = load_settings_config()

    assert config.application.timezone == "Asia/Tokyo"
    assert config.schedule.prediction_cutoff == "08:30"
    assert config.schedule.market_close == "15:30"
    assert config.provider.primary == "yahoo_finance"
    assert config.provider.treasury == "us_treasury"
    assert config.provider.fallback == ["eodhd_free"]
    assert config.provider.eodhd_free_max_calls_per_run == 5
    assert config.provider.snapshot_freshness_minutes == 15
    assert config.model.training_window_jpx_sessions == 120
    assert config.signal.predicted_intraday_return_threshold == pytest.approx(0.003)
    assert config.signal.probability_up_threshold == pytest.approx(0.60)
    assert config.data_quality.max_feature_missing_ratio == pytest.approx(0.20)
    assert config.data_quality.threshold_status == "confirmed"
    assert config.backtest.commission_bps_per_side == pytest.approx(5.0)
    assert config.backtest.slippage_bps_per_side == pytest.approx(5.0)
    assert config.backtest.cost_assumptions_status == "confirmed"


def test_model_and_trading_configs_make_safe_defaults_explicit() -> None:
    """Feature rejection, board lots, and simulated costs are confirmed inputs."""

    model = load_model_config()
    trading = load_trading_config()

    assert isinstance(model, ModelConfig)
    assert model.training.window_jpx_sessions == 120
    assert model.training.feature_warmup_jpx_sessions == 20
    assert model.features.return_windows == [1, 2, 3, 5, 20]
    assert model.features.max_missing_ratio == pytest.approx(0.20)
    assert model.features.missing_policy_status == "confirmed"
    assert model.cross_validation.strategy == "time_series_split"
    assert model.reproducibility.random_seed == 42

    assert isinstance(trading, TradingConfig)
    assert trading.position.lot_size == 100
    assert trading.position.lot_size_status == "confirmed"
    assert trading.costs.commission_bps_per_side == pytest.approx(5.0)
    assert trading.costs.slippage_bps_per_side == pytest.approx(5.0)
    assert trading.costs.assumptions_status == "confirmed"


def test_load_app_config_cross_validates_default_files() -> None:
    """The five shipped YAML files validate as one application config."""

    config = load_app_config()

    assert isinstance(config, AppConfig)
    assert config.settings.provider.primary == "yahoo_finance"
    assert config.model.features.max_missing_ratio == pytest.approx(0.20)
    assert config.trading.position.lot_size == 100


def test_duplicate_stock_ticker_is_rejected() -> None:
    """A repeated security code cannot silently enter the universe."""

    raw = _yaml_mapping("stocks.yaml")
    raw["stocks"].append(deepcopy(raw["stocks"][0]))

    with pytest.raises(ValidationError, match="duplicate stock tickers"):
        StocksConfig.model_validate(raw)


def test_duplicate_resolved_provider_symbol_is_rejected() -> None:
    """One provider identifier cannot resolve to two configured stocks."""

    raw = _yaml_mapping("stocks.yaml")
    raw["stocks"][0]["provider_symbols"]["test_provider"] = "verified.example"
    raw["stocks"][1]["provider_symbols"]["test_provider"] = "VERIFIED.EXAMPLE"

    with pytest.raises(ValidationError, match="duplicate test_provider symbol"):
        StocksConfig.model_validate(raw)


def test_invalid_market_timezone_is_rejected() -> None:
    """Timezone fields require valid IANA identifiers."""

    raw = _yaml_mapping("stocks.yaml")
    raw["stocks"][0]["market_timezone"] = "JST-ish"

    with pytest.raises(ValidationError, match="valid IANA timezone"):
        StocksConfig.model_validate(raw)


def test_required_stock_key_is_rejected_when_missing() -> None:
    """Required keys do not receive unsafe inferred defaults."""

    raw = _yaml_mapping("stocks.yaml")
    del raw["stocks"][0]["exchange_mic"]

    with pytest.raises(ValidationError, match="exchange_mic"):
        StocksConfig.model_validate(raw)


def test_strict_mode_rejects_coerced_ticker_type() -> None:
    """Quoted four-digit ticker strings are required; integers are not coerced."""

    raw = _yaml_mapping("stocks.yaml")
    raw["stocks"][0]["ticker"] = 9101

    with pytest.raises(ValidationError, match="ticker"):
        StocksConfig.model_validate(raw)


def test_duplicate_indicator_id_is_rejected() -> None:
    """A repeated factor ID cannot shadow another factor definition."""

    raw = _yaml_mapping("indicators.yaml")
    duplicate = deepcopy(raw["indicators"][0])
    raw["indicators"].append(duplicate)

    with pytest.raises(ValidationError, match="duplicate indicator ids"):
        IndicatorsConfig.model_validate(raw)


def test_unknown_sector_indicator_reference_is_rejected() -> None:
    """Every group reference must point to a catalog entry."""

    raw = _yaml_mapping("indicators.yaml")
    raw["sectors"]["shipping"]["indicators"].append("not_in_catalog")

    with pytest.raises(ValidationError, match="unknown indicators in sector shipping"):
        IndicatorsConfig.model_validate(raw)


def test_pending_source_cannot_contain_provider_identifier() -> None:
    """Pending research cannot be represented as a confirmed provider symbol."""

    raw = _yaml_mapping("indicators.yaml")
    raw["indicators"][0]["sources"][1]["provider_symbol"] = "guessed.symbol"

    with pytest.raises(
        ValidationError, match="may be set only after source verification"
    ):
        IndicatorsConfig.model_validate(raw)


def test_verified_eod_source_requires_point_in_time_metadata() -> None:
    """A verified daily source cannot omit the timezone needed for alignment."""

    raw = _yaml_mapping("indicators.yaml")
    del raw["indicators"][0]["sources"][0]["market_timezone"]

    with pytest.raises(
        ValidationError, match="verified sources need point-in-time metadata"
    ):
        IndicatorsConfig.model_validate(raw)


def test_settings_require_every_environment_key() -> None:
    """Deployment cannot omit a named secret or runtime environment variable."""

    raw = _yaml_mapping("settings.yaml")
    raw["environment"]["required"].remove("DATABASE_URL")

    with pytest.raises(ValidationError, match="DATABASE_URL"):
        SettingsConfig.model_validate(raw)


def test_eodhd_key_is_optional_and_free_fallback_is_rate_limited() -> None:
    """EODHD credentials are optional and a run cannot exceed five calls."""

    config = load_settings_config()
    assert "EODHD_API_KEY" not in config.environment.required
    assert config.environment.optional == ["EODHD_API_KEY", "RESEND_API_KEY"]

    raw = _yaml_mapping("settings.yaml")
    raw["provider"]["eodhd_free_max_calls_per_run"] = 6
    with pytest.raises(ValidationError, match="less than or equal to 5"):
        SettingsConfig.model_validate(raw)


def test_gmail_is_required_and_resend_is_an_optional_email_provider() -> None:
    """The free default needs Gmail credentials but not a Resend subscription."""

    config = load_settings_config()

    assert "SMTP_USERNAME" in config.environment.required
    assert "SMTP_PASSWORD" in config.environment.required
    assert "RESEND_API_KEY" not in config.environment.required
    assert "RESEND_API_KEY" in config.environment.optional


def test_snapshot_freshness_must_be_positive() -> None:
    """A disabled freshness guard cannot be accepted as a valid snapshot policy."""

    raw = _yaml_mapping("settings.yaml")
    raw["provider"]["snapshot_freshness_minutes"] = 0

    with pytest.raises(ValidationError, match="greater than 0"):
        SettingsConfig.model_validate(raw)


def test_settings_reject_invalid_timezone_and_schedule_order() -> None:
    """Both the business timezone and same-day stage order are validated."""

    invalid_timezone = _yaml_mapping("settings.yaml")
    invalid_timezone["application"]["timezone"] = "Invalid/Timezone"
    with pytest.raises(ValidationError, match="valid IANA timezone"):
        SettingsConfig.model_validate(invalid_timezone)

    invalid_schedule = _yaml_mapping("settings.yaml")
    invalid_schedule["schedule"]["email_send"] = "08:10"
    with pytest.raises(ValidationError, match="schedule must satisfy"):
        SettingsConfig.model_validate(invalid_schedule)


def test_duplicate_yaml_mapping_keys_are_rejected(tmp_path: Path) -> None:
    """The loader rejects duplicate YAML keys instead of accepting the last one."""

    config_path = tmp_path / "stocks.yaml"
    config_path.write_text("version: 1\nversion: 1\nstocks: []\n", encoding="utf-8")

    with pytest.raises(ConfigError, match="duplicate key"):
        load_stocks_config(config_path)
