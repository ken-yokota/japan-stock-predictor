"""Strict, typed loading for the application's YAML configuration files.

Provider symbols are populated only after direct verification.  The free-data
configuration uses Yahoo Finance as its primary market source, the official US
Treasury feed for yields, and an optional rate-limited EODHD Free fallback.
"""

from __future__ import annotations

import re
from datetime import time
from pathlib import Path
from typing import Annotated, Any, Literal, Self, cast
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import yaml
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
    model_validator,
)
from yaml.constructor import ConstructorError
from yaml.nodes import MappingNode
from yaml.resolver import BaseResolver

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG_DIR = PROJECT_ROOT / "config"

Sector = Literal[
    "shipping",
    "oil_energy",
    "automotive",
    "financial",
    "trading_company",
]
SourceKind = Literal["direct", "proxy", "alternative", "derived"]
ResolutionStatus = Literal["pending", "resolved", "unavailable"]
SourceStatus = Literal["pending", "verified", "unavailable"]
AvailabilityStatus = Literal["pending", "available", "conditional", "unavailable"]
DataMode = Literal["eod", "real_time", "yield_curve", "derived"]
SessionPolicy = Literal[
    "previous_completed_session",
    "latest_at_or_before_cutoff",
    "provider_published_at_or_before_cutoff",
    "derived_from_inputs",
]

NonEmptyStr = Annotated[str, Field(min_length=1)]
Ticker = Annotated[str, Field(pattern=r"^[0-9]{4}$")]
IndicatorId = Annotated[str, Field(pattern=r"^[a-z][a-z0-9_]*$")]

_PROVIDER_NAME_PATTERN = re.compile(r"^[a-z][a-z0-9_]*$")
_CLOCK_PATTERN = re.compile(r"^(?:[01][0-9]|2[0-3]):[0-5][0-9]$")
_REQUIRED_ENVIRONMENT_VARIABLES = {
    "APP_URL",
    "DATABASE_URL",
    "EMAIL_FROM",
    "EMAIL_TO",
    "SMTP_PASSWORD",
    "SMTP_USERNAME",
    "TIMEZONE",
}
_OPTIONAL_ENVIRONMENT_VARIABLES = {"EODHD_API_KEY", "RESEND_API_KEY"}
_REQUIRED_SECTORS = {
    "shipping",
    "oil_energy",
    "automotive",
    "financial",
    "trading_company",
}


class ConfigError(ValueError):
    """Raised when a configuration file cannot be parsed or validated."""


class _StrictModel(BaseModel):
    """Base model shared by all application configuration models."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        strict=True,
        str_strip_whitespace=True,
        validate_default=True,
    )


def _validate_timezone(value: str, *, field_name: str) -> str:
    """Return a valid IANA timezone name or raise a descriptive error."""

    try:
        ZoneInfo(value)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise ValueError(
            f"{field_name} must be a valid IANA timezone: {value}"
        ) from exc
    return value


def _duplicates(values: list[str]) -> set[str]:
    """Return all duplicate strings, comparing exact normalized values."""

    seen: set[str] = set()
    duplicates: set[str] = set()
    for value in values:
        if value in seen:
            duplicates.add(value)
        else:
            seen.add(value)
    return duplicates


class StockConfig(_StrictModel):
    """A Japanese equity tracked by the application."""

    ticker: Ticker
    name: NonEmptyStr
    sector: Sector
    country_iso: Annotated[str, Field(pattern=r"^[A-Z]{2}$")]
    exchange_mic: Annotated[str, Field(pattern=r"^[A-Z0-9]{4}$")]
    market_timezone: NonEmptyStr
    provider_symbols: dict[str, str | None]
    enabled: bool = True

    @field_validator("market_timezone")
    @classmethod
    def validate_market_timezone(cls, value: str) -> str:
        """Require an installed, canonical IANA timezone."""

        return _validate_timezone(value, field_name="market_timezone")

    @field_validator("provider_symbols")
    @classmethod
    def validate_provider_symbols(
        cls, value: dict[str, str | None]
    ) -> dict[str, str | None]:
        """Require primary/fallback slots and reject malformed symbols."""

        required_providers = {"yahoo_finance", "eodhd"}
        missing_providers = required_providers - set(value)
        if missing_providers:
            raise ValueError(
                "provider_symbols missing required providers: "
                f"{sorted(missing_providers)}"
            )
        if value["yahoo_finance"] is None:
            raise ValueError("yahoo_finance stock symbol must be resolved")
        if value["eodhd"] is not None:
            raise ValueError(
                "Japanese-stock eodhd symbol must remain null until supported"
            )
        for provider, symbol in value.items():
            if not _PROVIDER_NAME_PATTERN.fullmatch(provider):
                raise ValueError(
                    f"provider name must be lower_snake_case: {provider!r}"
                )
            if symbol is not None and (not symbol or symbol != symbol.strip()):
                raise ValueError(
                    f"provider symbol for {provider!r} must be non-blank and trimmed"
                )
        return value

    @model_validator(mode="after")
    def validate_yahoo_symbol(self) -> Self:
        """Bind each verified Tokyo listing to its matching Yahoo `.T` symbol."""

        expected_symbol = f"{self.ticker}.T"
        if self.provider_symbols["yahoo_finance"] != expected_symbol:
            raise ValueError(
                f"yahoo_finance symbol must be {expected_symbol!r} for "
                f"ticker {self.ticker}"
            )
        return self


class StocksConfig(_StrictModel):
    """Top-level stock universe configuration."""

    version: Literal[1]
    stocks: Annotated[list[StockConfig], Field(min_length=1)]

    @model_validator(mode="after")
    def reject_duplicates(self) -> Self:
        """Reject duplicate identities and verified provider symbols."""

        duplicate_tickers = _duplicates([stock.ticker for stock in self.stocks])
        if duplicate_tickers:
            raise ValueError(f"duplicate stock tickers: {sorted(duplicate_tickers)}")

        duplicate_names = _duplicates([stock.name for stock in self.stocks])
        if duplicate_names:
            raise ValueError(f"duplicate stock names: {sorted(duplicate_names)}")

        resolved_symbols: dict[tuple[str, str], str] = {}
        for stock in self.stocks:
            for provider, symbol in stock.provider_symbols.items():
                if symbol is None:
                    continue
                key = (provider, symbol.casefold())
                if key in resolved_symbols:
                    first_ticker = resolved_symbols[key]
                    raise ValueError(
                        f"duplicate {provider} symbol {symbol!r} for "
                        f"tickers {first_ticker} and {stock.ticker}"
                    )
                resolved_symbols[key] = stock.ticker
        return self


class IndicatorSourceConfig(_StrictModel):
    """One candidate source for an indicator.

    ``instrument_hint`` is a human-readable lookup hint from the product
    specification.  It is not a provider symbol.  A ``provider_symbol`` may be
    populated only after the candidate has been marked verified.
    """

    kind: SourceKind
    provider: NonEmptyStr | None
    instrument_hint: NonEmptyStr | None = None
    provider_symbol: NonEmptyStr | None = None
    endpoint: NonEmptyStr | None = None
    market: NonEmptyStr | None = None
    market_timezone: NonEmptyStr | None = None
    data_mode: DataMode | None = None
    session_policy: SessionPolicy | None = None
    market_close: NonEmptyStr | None = None
    is_proxy: bool | None = None
    snapshot_enabled: bool = False
    max_age_minutes: Annotated[int, Field(gt=0, le=180)] | None = None
    status: SourceStatus
    availability_at_0830: AvailabilityStatus
    inputs: list[IndicatorId] = Field(default_factory=list)
    notes: NonEmptyStr | None = None

    @field_validator("market_timezone")
    @classmethod
    def validate_optional_market_timezone(cls, value: str | None) -> str | None:
        """Validate candidate timezone when provider research has supplied it."""

        if value is None:
            return None
        return _validate_timezone(value, field_name="market_timezone")

    @field_validator("market_close")
    @classmethod
    def validate_optional_market_close(cls, value: str | None) -> str | None:
        """Require an unambiguous local close time when one is supplied."""

        if value is not None and not _CLOCK_PATTERN.fullmatch(value):
            raise ValueError("market_close must use zero-padded HH:MM format")
        return value

    @model_validator(mode="after")
    def validate_resolution_state(self) -> Self:
        """Prevent pending identifiers from becoming accidental hard-coded IDs."""

        duplicate_inputs = _duplicates(list(self.inputs))
        if duplicate_inputs:
            raise ValueError(f"duplicate derived inputs: {sorted(duplicate_inputs)}")

        if self.kind == "derived":
            if self.provider != "internal":
                raise ValueError("derived sources must use provider='internal'")
            if self.provider_symbol is not None or self.endpoint is not None:
                raise ValueError("derived sources cannot define provider identifiers")
            if not self.inputs:
                raise ValueError("derived sources must declare at least one input")
            if self.status != "verified":
                raise ValueError("derived formulas must be marked verified")
            if self.data_mode != "derived":
                raise ValueError("derived sources must use data_mode='derived'")
            if self.session_policy != "derived_from_inputs":
                raise ValueError(
                    "derived sources must use session_policy='derived_from_inputs'"
                )
            if self.is_proxy is not False:
                raise ValueError("derived sources must set is_proxy=false")
            if self.snapshot_enabled or self.max_age_minutes is not None:
                raise ValueError("derived sources cannot define snapshot settings")
            return self

        if self.inputs:
            raise ValueError("only derived sources may declare inputs")
        if self.kind in {"direct", "proxy"} and self.provider is None:
            raise ValueError(f"{self.kind} sources must declare a provider")
        if self.status != "verified" and (
            self.provider_symbol is not None or self.endpoint is not None
        ):
            raise ValueError(
                "provider_symbol/endpoint may be set only after source verification"
            )
        if self.status == "verified" and (
            self.provider_symbol is None and self.endpoint is None
        ):
            raise ValueError(
                "verified non-derived sources need a provider_symbol or endpoint"
            )
        if self.status == "verified":
            required_metadata = {
                "provider": self.provider,
                "market": self.market,
                "market_timezone": self.market_timezone,
                "data_mode": self.data_mode,
                "session_policy": self.session_policy,
                "is_proxy": self.is_proxy,
            }
            missing_metadata = sorted(
                key for key, metadata in required_metadata.items() if metadata is None
            )
            if missing_metadata:
                raise ValueError(
                    f"verified sources need point-in-time metadata: {missing_metadata}"
                )
            if self.availability_at_0830 not in {"available", "conditional"}:
                raise ValueError(
                    "verified sources need available or conditional 08:30 status"
                )
            if self.kind == "proxy" and self.is_proxy is not True:
                raise ValueError("proxy sources must set is_proxy=true")
            if self.kind != "proxy" and self.is_proxy is not False:
                raise ValueError("non-proxy sources must set is_proxy=false")
            if self.data_mode == "eod":
                if self.provider_symbol is None or self.endpoint is None:
                    raise ValueError(
                        "verified EOD sources need provider_symbol and endpoint"
                    )
                if self.market_close is None:
                    raise ValueError("verified EOD sources need market_close")
                if self.session_policy != "previous_completed_session":
                    raise ValueError(
                        "EOD sources must use the previous completed session"
                    )
            elif self.data_mode == "real_time":
                if self.provider_symbol is None or self.endpoint is None:
                    raise ValueError(
                        "verified real-time sources need provider_symbol and endpoint"
                    )
                if self.market_close is not None:
                    raise ValueError("real-time sources cannot define market_close")
                if self.session_policy != "latest_at_or_before_cutoff":
                    raise ValueError(
                        "real-time sources must use the cutoff-aware session policy"
                    )
            elif self.data_mode == "yield_curve":
                if self.endpoint is None:
                    raise ValueError("verified yield sources need an endpoint")
                if self.market_close is not None:
                    raise ValueError("yield-curve sources cannot define market_close")
                if self.session_policy != "provider_published_at_or_before_cutoff":
                    raise ValueError(
                        "yield sources must enforce provider publication cutoff"
                    )

        if self.snapshot_enabled:
            if self.status != "verified" or self.provider != "yahoo_finance":
                raise ValueError(
                    "snapshots are allowed only for verified Yahoo Finance sources"
                )
            if self.max_age_minutes is None:
                raise ValueError("enabled snapshots require max_age_minutes")
        elif self.max_age_minutes is not None:
            raise ValueError("max_age_minutes requires snapshot_enabled=true")
        return self


class IndicatorConfig(_StrictModel):
    """A conceptual market factor and its candidate data sources."""

    id: IndicatorId
    name: NonEmptyStr
    category: NonEmptyStr
    required: bool
    optional_reason: NonEmptyStr | None = None
    resolution_status: ResolutionStatus
    applies_to_tickers: list[Ticker] = Field(default_factory=list)
    sources: Annotated[list[IndicatorSourceConfig], Field(min_length=1)]

    @model_validator(mode="after")
    def validate_indicator(self) -> Self:
        """Check optionality, ticker targeting, and source candidate uniqueness."""

        if not self.required and self.optional_reason is None:
            raise ValueError("optional indicators must explain optional_reason")
        if self.required and self.optional_reason is not None:
            raise ValueError("required indicators cannot define optional_reason")

        duplicate_tickers = _duplicates(list(self.applies_to_tickers))
        if duplicate_tickers:
            raise ValueError(
                f"duplicate applies_to_tickers: {sorted(duplicate_tickers)}"
            )

        candidate_keys: set[tuple[str, str | None, str | None]] = set()
        for source in self.sources:
            key = (source.kind, source.provider, source.instrument_hint)
            if key in candidate_keys:
                raise ValueError(f"duplicate source candidate: {key}")
            candidate_keys.add(key)

        verified_count = sum(source.status == "verified" for source in self.sources)
        if self.resolution_status == "resolved" and verified_count == 0:
            raise ValueError("resolved indicators need at least one verified source")
        if self.resolution_status == "pending" and verified_count:
            raise ValueError("pending indicators cannot contain verified sources")
        if self.resolution_status == "unavailable" and any(
            source.status != "unavailable" for source in self.sources
        ):
            raise ValueError("unavailable indicators require unavailable sources")
        return self


class IndicatorGroupConfig(_StrictModel):
    """Additional indicators assigned to one sector."""

    indicators: list[IndicatorId]
    notes: NonEmptyStr | None = None

    @model_validator(mode="after")
    def validate_group(self) -> Self:
        """Reject duplicate references and explain intentionally empty groups."""

        duplicates = _duplicates(list(self.indicators))
        if duplicates:
            raise ValueError(f"duplicate indicator references: {sorted(duplicates)}")
        if not self.indicators and self.notes is None:
            raise ValueError("empty indicator groups must include notes")
        return self


class IndicatorsConfig(_StrictModel):
    """Top-level indicator catalog and common/sector assignments."""

    version: Literal[1]
    provider_symbol_policy: Literal["official_verification_required"]
    indicators: Annotated[list[IndicatorConfig], Field(min_length=1)]
    common: Annotated[list[IndicatorId], Field(min_length=1)]
    sectors: dict[Sector, IndicatorGroupConfig]

    @model_validator(mode="after")
    def validate_catalog_references(self) -> Self:
        """Validate IDs, assignments, derivations, and resolved symbol uniqueness."""

        ids = [indicator.id for indicator in self.indicators]
        duplicate_ids = _duplicates(ids)
        if duplicate_ids:
            raise ValueError(f"duplicate indicator ids: {sorted(duplicate_ids)}")
        known_ids = set(ids)

        duplicate_common = _duplicates(list(self.common))
        if duplicate_common:
            raise ValueError(
                f"duplicate common indicator references: {sorted(duplicate_common)}"
            )
        missing_common = set(self.common) - known_ids
        if missing_common:
            raise ValueError(f"unknown common indicators: {sorted(missing_common)}")

        actual_sectors = set(self.sectors)
        if actual_sectors != _REQUIRED_SECTORS:
            missing_sectors = sorted(_REQUIRED_SECTORS - actual_sectors)
            extra = sorted(actual_sectors - _REQUIRED_SECTORS)
            raise ValueError(
                f"sector groups must match the five supported sectors; "
                f"missing={missing_sectors}, extra={extra}"
            )

        referenced_ids = set(self.common)
        for sector, group in self.sectors.items():
            missing_indicators = set(group.indicators) - known_ids
            if missing_indicators:
                raise ValueError(
                    "unknown indicators in sector "
                    f"{sector}: {sorted(missing_indicators)}"
                )
            referenced_ids.update(group.indicators)

        unassigned = known_ids - referenced_ids
        if unassigned:
            raise ValueError(f"unassigned indicators: {sorted(unassigned)}")

        resolved_symbols: dict[tuple[str, str], str] = {}
        dependencies: dict[str, set[str]] = {
            indicator_id: set() for indicator_id in ids
        }
        for indicator in self.indicators:
            for source in indicator.sources:
                missing_inputs = set(source.inputs) - known_ids
                if missing_inputs:
                    raise ValueError(
                        f"unknown derived inputs for {indicator.id}: "
                        f"{sorted(missing_inputs)}"
                    )
                if indicator.id in source.inputs:
                    raise ValueError(
                        f"indicator {indicator.id} cannot depend on itself"
                    )
                dependencies[indicator.id].update(source.inputs)

                if source.provider is None or source.provider_symbol is None:
                    continue
                key = (source.provider, source.provider_symbol.casefold())
                if key in resolved_symbols:
                    first_id = resolved_symbols[key]
                    raise ValueError(
                        f"duplicate {source.provider} symbol "
                        f"{source.provider_symbol!r} for {first_id} and {indicator.id}"
                    )
                resolved_symbols[key] = indicator.id

        self._reject_dependency_cycles(dependencies)
        return self

    @staticmethod
    def _reject_dependency_cycles(dependencies: dict[str, set[str]]) -> None:
        """Reject cycles among derived indicators."""

        visiting: set[str] = set()
        visited: set[str] = set()

        def visit(indicator_id: str) -> None:
            if indicator_id in visiting:
                raise ValueError(
                    f"cyclic indicator dependency involving {indicator_id}"
                )
            if indicator_id in visited:
                return
            visiting.add(indicator_id)
            for dependency in dependencies[indicator_id]:
                visit(dependency)
            visiting.remove(indicator_id)
            visited.add(indicator_id)

        for indicator_id in dependencies:
            visit(indicator_id)


class ApplicationSettings(_StrictModel):
    """Application identity and business timezone."""

    name: NonEmptyStr
    timezone: NonEmptyStr

    @field_validator("timezone")
    @classmethod
    def validate_timezone(cls, value: str) -> str:
        """Require the specification's Japanese business timezone."""

        _validate_timezone(value, field_name="timezone")
        if value != "Asia/Tokyo":
            raise ValueError("application timezone must be Asia/Tokyo")
        return value


class ScheduleSettings(_StrictModel):
    """JST market and pipeline clock times in HH:MM format."""

    morning_fetch: NonEmptyStr
    prediction_cutoff: NonEmptyStr
    email_send: NonEmptyStr
    market_open: NonEmptyStr
    market_close: NonEmptyStr
    close_update: NonEmptyStr

    @field_validator(
        "morning_fetch",
        "prediction_cutoff",
        "email_send",
        "market_open",
        "market_close",
        "close_update",
    )
    @classmethod
    def validate_clock(cls, value: str) -> str:
        """Require unambiguous 24-hour HH:MM values."""

        if not _CLOCK_PATTERN.fullmatch(value):
            raise ValueError("clock values must use zero-padded HH:MM format")
        return value

    @model_validator(mode="after")
    def validate_order(self) -> Self:
        """Ensure pipeline stages follow the required same-day sequence."""

        ordered = [
            self.morning_fetch,
            self.prediction_cutoff,
            self.email_send,
            self.market_open,
            self.market_close,
            self.close_update,
        ]
        parsed = [time.fromisoformat(value) for value in ordered]
        if parsed != sorted(parsed) or len(set(parsed)) != len(parsed):
            raise ValueError(
                "schedule must satisfy morning_fetch < prediction_cutoff < "
                "email_send < market_open < market_close < close_update"
            )
        return self


class ProviderSettings(_StrictModel):
    """External provider resilience settings."""

    primary: Literal["yahoo_finance"]
    treasury: Literal["us_treasury"]
    fallback: list[Literal["eodhd_free"]]
    eodhd_free_max_calls_per_run: Annotated[int, Field(ge=0, le=5)]
    snapshot_freshness_minutes: Annotated[int, Field(gt=0, le=120)]
    request_timeout_seconds: Annotated[float, Field(gt=0.0, le=120.0)]
    max_retries: Annotated[int, Field(ge=0, le=10)]
    backoff_initial_seconds: Annotated[float, Field(gt=0.0, le=60.0)]

    @field_validator("fallback")
    @classmethod
    def reject_duplicate_fallbacks(
        cls, value: list[Literal["eodhd_free"]]
    ) -> list[Literal["eodhd_free"]]:
        """Reject duplicate fallback routes that could double-spend call quota."""

        duplicates = _duplicates(list(value))
        if duplicates:
            raise ValueError(f"duplicate fallback providers: {sorted(duplicates)}")
        return value


class ModelSettings(_StrictModel):
    """Leakage-safe initial model defaults."""

    primary: Literal["ridge"]
    training_window_jpx_sessions: Annotated[int, Field(ge=20)]
    scaler: Literal["standard_scaler"]
    cross_validation: Literal["time_series_split"]


class SignalSettings(_StrictModel):
    """Default BUY signal thresholds."""

    predicted_intraday_return_threshold: Annotated[float, Field(gt=-1.0, lt=1.0)]
    probability_up_threshold: Annotated[float, Field(ge=0.0, le=1.0)]


class DataQualitySettings(_StrictModel):
    """Missing-data policy, kept unresolved until the owner chooses a threshold."""

    max_feature_missing_ratio: Annotated[float, Field(ge=0.0, le=1.0)] | None
    threshold_status: Literal["pending_confirmation", "confirmed"]
    insufficient_data_prediction_status: Literal["INSUFFICIENT_DATA"]

    @model_validator(mode="after")
    def validate_threshold_state(self) -> Self:
        """Do not silently invent a financially meaningful missing-data cutoff."""

        if (
            self.threshold_status == "pending_confirmation"
            and self.max_feature_missing_ratio is not None
        ):
            raise ValueError("pending missing-data threshold must remain null")
        if (
            self.threshold_status == "confirmed"
            and self.max_feature_missing_ratio is None
        ):
            raise ValueError("confirmed missing-data threshold needs a value")
        return self


class BacktestSettings(_StrictModel):
    """Initial capital and explicitly unresolved cost assumptions."""

    capital_per_stock_jpy: Annotated[int, Field(gt=0)]
    commission_bps_per_side: Annotated[float, Field(ge=0.0)] | None
    slippage_bps_per_side: Annotated[float, Field(ge=0.0)] | None
    cost_assumptions_status: Literal["pending_confirmation", "confirmed"]
    quantity_method: Literal["floor_capital_div_open"]
    carry_overnight: Literal[False]

    @model_validator(mode="after")
    def validate_cost_state(self) -> Self:
        """Prevent unconfirmed zero costs from looking like accepted assumptions."""

        costs = (self.commission_bps_per_side, self.slippage_bps_per_side)
        if self.cost_assumptions_status == "pending_confirmation" and any(
            value is not None for value in costs
        ):
            raise ValueError("pending trading costs must remain null")
        if self.cost_assumptions_status == "confirmed" and any(
            value is None for value in costs
        ):
            raise ValueError("confirmed trading costs require both values")
        return self


class TrainingConfig(_StrictModel):
    """Rolling-window training contract shared by live and OOS runs."""

    window_jpx_sessions: Annotated[int, Field(ge=20)]
    minimum_complete_rows: Annotated[int, Field(ge=20)]
    feature_warmup_jpx_sessions: Annotated[int, Field(ge=1)]
    target: Literal["intraday_return"]

    @model_validator(mode="after")
    def validate_training_lengths(self) -> Self:
        if self.minimum_complete_rows > self.window_jpx_sessions:
            raise ValueError("minimum_complete_rows cannot exceed training window")
        return self


class FeatureEngineeringConfig(_StrictModel):
    """Versioned feature windows and explicit missing-data policy."""

    return_windows: Annotated[list[Annotated[int, Field(gt=0)]], Field(min_length=1)]
    volatility_windows: Annotated[
        list[Annotated[int, Field(gt=1)]], Field(min_length=1)
    ]
    moving_average_window: Annotated[int, Field(gt=1)]
    treasury_change_lags: Annotated[
        list[Annotated[int, Field(gt=0)]], Field(min_length=1)
    ]
    include_log_return: bool
    include_open_close_return: bool
    include_high_low_range: bool
    max_missing_ratio: Annotated[float, Field(ge=0.0, le=1.0)] | None
    missing_policy_status: Literal["pending_confirmation", "confirmed"]

    @field_validator("return_windows", "volatility_windows", "treasury_change_lags")
    @classmethod
    def validate_ordered_windows(cls, value: list[int]) -> list[int]:
        if value != sorted(set(value)):
            raise ValueError("feature windows must be unique and ascending")
        return value

    @model_validator(mode="after")
    def validate_missing_policy(self) -> Self:
        if (
            self.missing_policy_status == "pending_confirmation"
            and self.max_missing_ratio is not None
        ):
            raise ValueError("pending feature missing policy must remain null")
        if self.missing_policy_status == "confirmed" and self.max_missing_ratio is None:
            raise ValueError("confirmed feature missing policy needs a ratio")
        return self


RegressionModel = Literal["ridge", "elastic_net", "ols", "lasso"]
ClassificationModel = Literal["logistic_regression"]


class ModelFamilyConfig(_StrictModel):
    """Permitted initial algorithms without implying they are all implemented."""

    regression_primary: Literal["ridge"]
    regression_candidates: Annotated[list[RegressionModel], Field(min_length=1)]
    classification_primary: Literal["logistic_regression"]
    classification_candidates: Annotated[list[ClassificationModel], Field(min_length=1)]
    scaler: Literal["standard_scaler"]

    @model_validator(mode="after")
    def validate_primary_candidates(self) -> Self:
        if len(set(self.regression_candidates)) != len(self.regression_candidates):
            raise ValueError("duplicate regression model candidates")
        if len(set(self.classification_candidates)) != len(
            self.classification_candidates
        ):
            raise ValueError("duplicate classification model candidates")
        if self.regression_primary not in self.regression_candidates:
            raise ValueError("primary regression model must be a candidate")
        if self.classification_primary not in self.classification_candidates:
            raise ValueError("primary classification model must be a candidate")
        return self


class CrossValidationConfig(_StrictModel):
    """Forward-only model-selection settings."""

    strategy: Literal["time_series_split"]
    n_splits: Annotated[int, Field(ge=2, le=20)]
    gap: Annotated[int, Field(ge=0)]


class HyperparameterConfig(_StrictModel):
    """Small deterministic search grids for the initial linear models."""

    ridge_alpha: Annotated[list[Annotated[float, Field(gt=0.0)]], Field(min_length=1)]
    elastic_net_alpha: Annotated[
        list[Annotated[float, Field(gt=0.0)]], Field(min_length=1)
    ]
    elastic_net_l1_ratio: Annotated[
        list[Annotated[float, Field(gt=0.0, le=1.0)]], Field(min_length=1)
    ]
    logistic_c: Annotated[list[Annotated[float, Field(gt=0.0)]], Field(min_length=1)]

    @field_validator(
        "ridge_alpha", "elastic_net_alpha", "elastic_net_l1_ratio", "logistic_c"
    )
    @classmethod
    def validate_unique_grid(cls, value: list[float]) -> list[float]:
        if len(set(value)) != len(value):
            raise ValueError("hyperparameter grid values must be unique")
        return value


class ReproducibilityConfig(_StrictModel):
    """Inputs required to reproduce a daily model run."""

    random_seed: Annotated[int, Field(ge=0)]
    deterministic: Literal[True]


class ModelConfig(_StrictModel):
    """Top-level ``model.yaml`` contract."""

    version: Literal[1]
    training: TrainingConfig
    features: FeatureEngineeringConfig
    models: ModelFamilyConfig
    cross_validation: CrossValidationConfig
    hyperparameters: HyperparameterConfig
    reproducibility: ReproducibilityConfig

    @model_validator(mode="after")
    def validate_feature_warmup(self) -> Self:
        longest_window = max(
            *self.features.return_windows,
            *self.features.volatility_windows,
            self.features.moving_average_window,
        )
        if self.training.feature_warmup_jpx_sessions < longest_window:
            raise ValueError("feature warm-up must cover the longest feature window")
        return self


class TradingSignalConfig(_StrictModel):
    """BUY thresholds and their exact boundary semantics."""

    predicted_intraday_return_threshold: Annotated[float, Field(gt=-1.0, lt=1.0)]
    probability_up_threshold: Annotated[float, Field(ge=0.0, le=1.0)]
    return_comparison: Literal["strict_greater_than"]
    probability_comparison: Literal["greater_than_or_equal"]
    insufficient_data_status: Literal["INSUFFICIENT_DATA"]


class PositionConfig(_StrictModel):
    """Intraday-only simulated position sizing."""

    capital_per_stock_jpy: Annotated[int, Field(gt=0)]
    quantity_method: Literal["floor_capital_div_open"]
    lot_size: Annotated[int, Field(gt=0)] | None
    lot_size_status: Literal["pending_confirmation", "confirmed"]
    carry_overnight: Literal[False]

    @model_validator(mode="after")
    def validate_lot_size(self) -> Self:
        if self.lot_size_status == "pending_confirmation" and self.lot_size is not None:
            raise ValueError("pending lot size must remain null")
        if self.lot_size_status == "confirmed" and self.lot_size is None:
            raise ValueError("confirmed lot size needs a value")
        return self


class TradingCostConfig(_StrictModel):
    """Explicitly confirmed or pending commission and slippage assumptions."""

    commission_bps_per_side: Annotated[float, Field(ge=0.0)] | None
    slippage_bps_per_side: Annotated[float, Field(ge=0.0)] | None
    assumptions_status: Literal["pending_confirmation", "confirmed"]

    @model_validator(mode="after")
    def validate_assumptions(self) -> Self:
        costs = (self.commission_bps_per_side, self.slippage_bps_per_side)
        if self.assumptions_status == "pending_confirmation" and any(
            value is not None for value in costs
        ):
            raise ValueError("pending trading costs must remain null")
        if self.assumptions_status == "confirmed" and any(
            value is None for value in costs
        ):
            raise ValueError("confirmed trading costs require both values")
        return self


class PredictionPriceConfig(_StrictModel):
    """Reference used before the actual market open is known."""

    morning_reference: Literal["previous_close"]
    recompute_after_actual_open: bool


class TradingConfig(_StrictModel):
    """Top-level ``trading.yaml`` contract."""

    version: Literal[1]
    signal: TradingSignalConfig
    position: PositionConfig
    costs: TradingCostConfig
    prediction_price: PredictionPriceConfig


class EnvironmentSettings(_StrictModel):
    """Names of secrets and deployment settings expected from the environment."""

    required: Annotated[list[NonEmptyStr], Field(min_length=1)]
    optional: list[NonEmptyStr]

    @field_validator("required")
    @classmethod
    def validate_required_names(cls, value: list[str]) -> list[str]:
        """Require every environment key named in the specification exactly once."""

        duplicates = _duplicates(value)
        if duplicates:
            raise ValueError(
                f"duplicate required environment variables: {sorted(duplicates)}"
            )
        missing = _REQUIRED_ENVIRONMENT_VARIABLES - set(value)
        if missing:
            raise ValueError(
                f"missing required environment variables: {sorted(missing)}"
            )
        return value

    @field_validator("optional")
    @classmethod
    def validate_optional_names(cls, value: list[str]) -> list[str]:
        """Keep optional provider credentials explicit and non-duplicated."""

        duplicates = _duplicates(value)
        if duplicates:
            raise ValueError(
                f"duplicate optional environment variables: {sorted(duplicates)}"
            )
        missing = _OPTIONAL_ENVIRONMENT_VARIABLES - set(value)
        if missing:
            raise ValueError(
                f"missing optional environment variables: {sorted(missing)}"
            )
        return value

    @model_validator(mode="after")
    def reject_required_optional_overlap(self) -> Self:
        """One environment key cannot be both required and optional."""

        overlap = set(self.required) & set(self.optional)
        if overlap:
            raise ValueError(
                f"environment variables cannot be required and optional: "
                f"{sorted(overlap)}"
            )
        return self


class SettingsConfig(_StrictModel):
    """Top-level operational settings."""

    version: Literal[1]
    application: ApplicationSettings
    schedule: ScheduleSettings
    provider: ProviderSettings
    model: ModelSettings
    signal: SignalSettings
    data_quality: DataQualitySettings
    backtest: BacktestSettings
    environment: EnvironmentSettings


class AppConfig(_StrictModel):
    """Validated bundle of all five application configuration files."""

    stocks: StocksConfig
    indicators: IndicatorsConfig
    settings: SettingsConfig
    model: ModelConfig
    trading: TradingConfig

    @model_validator(mode="after")
    def validate_cross_file_references(self) -> Self:
        """Validate stock-specific factors and primary-provider coverage."""

        tickers = {stock.ticker for stock in self.stocks.stocks}
        primary_provider = self.settings.provider.primary

        legacy_model = self.settings.model
        if (
            self.model.training.window_jpx_sessions
            != legacy_model.training_window_jpx_sessions
            or self.model.models.regression_primary != legacy_model.primary
            or self.model.models.scaler != legacy_model.scaler
            or self.model.cross_validation.strategy != legacy_model.cross_validation
        ):
            raise ValueError("model.yaml disagrees with legacy settings model values")

        legacy_signal = self.settings.signal
        legacy_backtest = self.settings.backtest
        if (
            self.trading.signal.predicted_intraday_return_threshold
            != legacy_signal.predicted_intraday_return_threshold
            or self.trading.signal.probability_up_threshold
            != legacy_signal.probability_up_threshold
            or self.trading.position.capital_per_stock_jpy
            != legacy_backtest.capital_per_stock_jpy
            or self.trading.position.quantity_method != legacy_backtest.quantity_method
            or self.trading.position.carry_overnight != legacy_backtest.carry_overnight
            or self.trading.costs.commission_bps_per_side
            != legacy_backtest.commission_bps_per_side
            or self.trading.costs.slippage_bps_per_side
            != legacy_backtest.slippage_bps_per_side
            or self.trading.costs.assumptions_status
            != legacy_backtest.cost_assumptions_status
        ):
            raise ValueError("trading.yaml disagrees with legacy settings values")

        if (
            self.model.features.max_missing_ratio
            != self.settings.data_quality.max_feature_missing_ratio
            or self.model.features.missing_policy_status
            != self.settings.data_quality.threshold_status
        ):
            raise ValueError("model.yaml disagrees with legacy missing-data policy")

        missing_provider = sorted(
            stock.ticker
            for stock in self.stocks.stocks
            if primary_provider not in stock.provider_symbols
        )
        if missing_provider:
            raise ValueError(
                f"stocks missing primary provider {primary_provider}: "
                f"{missing_provider}"
            )

        for indicator in self.indicators.indicators:
            unknown_tickers = set(indicator.applies_to_tickers) - tickers
            if unknown_tickers:
                raise ValueError(
                    f"indicator {indicator.id} targets unknown tickers: "
                    f"{sorted(unknown_tickers)}"
                )

        configured_providers = {
            self.settings.provider.primary,
            self.settings.provider.treasury,
            *self.settings.provider.fallback,
            "internal",
        }
        for indicator in self.indicators.indicators:
            unexpected = sorted(
                {
                    source.provider
                    for source in indicator.sources
                    if source.status == "verified"
                    and source.provider is not None
                    and source.provider not in configured_providers
                }
            )
            if unexpected:
                raise ValueError(
                    f"indicator {indicator.id} uses unconfigured providers: "
                    f"{unexpected}"
                )
        return self


class _UniqueKeyLoader(yaml.SafeLoader):
    """Safe YAML loader that rejects duplicate mapping keys."""


def _construct_unique_mapping(
    loader: _UniqueKeyLoader, node: MappingNode, deep: bool = False
) -> dict[Any, Any]:
    """Construct a YAML mapping without PyYAML's silent key overwrites."""

    loader.flatten_mapping(node)
    mapping: dict[Any, Any] = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        try:
            already_present = key in mapping
        except TypeError as exc:
            raise ConstructorError(
                "while constructing a mapping",
                node.start_mark,
                "found an unhashable key",
                key_node.start_mark,
            ) from exc
        if already_present:
            raise ConstructorError(
                "while constructing a mapping",
                node.start_mark,
                f"found duplicate key {key!r}",
                key_node.start_mark,
            )
        mapping[key] = loader.construct_object(value_node, deep=deep)
    return mapping


_UniqueKeyLoader.add_constructor(
    BaseResolver.DEFAULT_MAPPING_TAG, _construct_unique_mapping
)


def _load_yaml(path: Path) -> dict[str, Any]:
    """Read one YAML mapping and add file context to parsing failures."""

    try:
        with path.open("r", encoding="utf-8") as stream:
            raw = yaml.load(stream, Loader=_UniqueKeyLoader)
    except (OSError, yaml.YAMLError) as exc:
        raise ConfigError(f"failed to read configuration {path}: {exc}") from exc

    if not isinstance(raw, dict):
        raise ConfigError(f"configuration root must be a mapping: {path}")
    return cast(dict[str, Any], raw)


def _validate_file(model: type[_StrictModel], path: Path) -> _StrictModel:
    """Load and validate a YAML file with a stable public error type."""

    try:
        return model.model_validate(_load_yaml(path))
    except ValidationError as exc:
        raise ConfigError(f"invalid configuration {path}: {exc}") from exc


def load_stocks_config(path: str | Path | None = None) -> StocksConfig:
    """Load ``stocks.yaml`` from ``path`` or the project config directory."""

    resolved_path = (
        Path(path) if path is not None else DEFAULT_CONFIG_DIR / "stocks.yaml"
    )
    return cast(StocksConfig, _validate_file(StocksConfig, resolved_path))


def load_indicators_config(path: str | Path | None = None) -> IndicatorsConfig:
    """Load ``indicators.yaml`` from ``path`` or the project config directory."""

    resolved_path = (
        Path(path) if path is not None else DEFAULT_CONFIG_DIR / "indicators.yaml"
    )
    return cast(IndicatorsConfig, _validate_file(IndicatorsConfig, resolved_path))


def load_settings_config(path: str | Path | None = None) -> SettingsConfig:
    """Load ``settings.yaml`` from ``path`` or the project config directory."""

    resolved_path = (
        Path(path) if path is not None else DEFAULT_CONFIG_DIR / "settings.yaml"
    )
    return cast(SettingsConfig, _validate_file(SettingsConfig, resolved_path))


def load_model_config(path: str | Path | None = None) -> ModelConfig:
    """Load ``model.yaml`` from ``path`` or the project config directory."""

    resolved_path = (
        Path(path) if path is not None else DEFAULT_CONFIG_DIR / "model.yaml"
    )
    return cast(ModelConfig, _validate_file(ModelConfig, resolved_path))


def load_trading_config(path: str | Path | None = None) -> TradingConfig:
    """Load ``trading.yaml`` from ``path`` or the project config directory."""

    resolved_path = (
        Path(path) if path is not None else DEFAULT_CONFIG_DIR / "trading.yaml"
    )
    return cast(TradingConfig, _validate_file(TradingConfig, resolved_path))


def load_app_config(config_dir: str | Path | None = None) -> AppConfig:
    """Load and cross-validate every application configuration file."""

    directory = Path(config_dir) if config_dir is not None else DEFAULT_CONFIG_DIR
    try:
        return AppConfig(
            stocks=load_stocks_config(directory / "stocks.yaml"),
            indicators=load_indicators_config(directory / "indicators.yaml"),
            settings=load_settings_config(directory / "settings.yaml"),
            model=load_model_config(directory / "model.yaml"),
            trading=load_trading_config(directory / "trading.yaml"),
        )
    except ValidationError as exc:
        raise ConfigError(
            f"invalid cross-file configuration in {directory}: {exc}"
        ) from exc
