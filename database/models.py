"""Phase 1 SQLAlchemy schema."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, declared_attr, mapped_column


class Base(DeclarativeBase):
    """Declarative model base."""


class PriceColumns:
    """Shared point-in-time OHLCV columns for market and target-stock data."""

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    canonical_symbol: Mapped[str] = mapped_column(String(64), nullable=False)
    symbol: Mapped[str] = mapped_column(String(96), nullable=False)
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    market: Mapped[str] = mapped_column(String(32), nullable=False)
    market_timezone: Mapped[str] = mapped_column(String(64), nullable=False)
    market_date: Mapped[date] = mapped_column(Date, nullable=False)
    timestamp: Mapped[datetime] = mapped_column(
        "market_timestamp", DateTime(timezone=True), nullable=False
    )
    source_timestamp: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    available_timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    first_observed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    retrieved_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    interval: Mapped[str] = mapped_column(String(24), nullable=False)
    availability_method: Mapped[str] = mapped_column(String(32), nullable=False)
    data_quality: Mapped[str] = mapped_column(String(32), nullable=False)
    is_realtime: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    is_delayed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    open: Mapped[Decimal | None] = mapped_column(Numeric(24, 10), nullable=True)
    high: Mapped[Decimal | None] = mapped_column(Numeric(24, 10), nullable=True)
    low: Mapped[Decimal | None] = mapped_column(Numeric(24, 10), nullable=True)
    close: Mapped[Decimal] = mapped_column(Numeric(24, 10), nullable=False)
    adjusted_close: Mapped[Decimal | None] = mapped_column(
        Numeric(24, 10), nullable=True
    )
    volume: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    currency: Mapped[str | None] = mapped_column(String(12), nullable=True)
    raw_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    quality_flags: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)

    @declared_attr.directive
    def __table_args__(cls) -> tuple[Any, ...]:
        prefix = str(cls.__tablename__)  # type: ignore[attr-defined]
        return (
            UniqueConstraint(
                "provider",
                "symbol",
                "interval",
                "market_timestamp",
                "raw_hash",
                name=f"uq_{prefix}_revision",
            ),
            CheckConstraint(
                "market_timestamp <= available_timestamp",
                name=f"ck_{prefix}_event_available",
            ),
            CheckConstraint(
                "available_timestamp <= first_observed_at",
                name=f"ck_{prefix}_availability_observed",
            ),
            CheckConstraint(
                "first_observed_at <= retrieved_at",
                name=f"ck_{prefix}_observed_retrieved",
            ),
            CheckConstraint(
                "retrieved_at <= last_seen_at",
                name=f"ck_{prefix}_retrieved_last_seen",
            ),
            CheckConstraint(
                "source_timestamp IS NULL OR source_timestamp <= retrieved_at",
                name=f"ck_{prefix}_source_retrieved",
            ),
            Index(
                f"ix_{prefix}_symbol_available",
                "canonical_symbol",
                "available_timestamp",
            ),
            Index(f"ix_{prefix}_symbol_market_date", "canonical_symbol", "market_date"),
        )


class MarketData(PriceColumns, Base):
    """Overseas indicators and other explanatory market series."""

    __tablename__ = "market_data"


class StockPrice(PriceColumns, Base):
    """Japanese target-stock OHLCV series."""

    __tablename__ = "stock_prices"


class InstrumentMapping(Base):
    """Provider mapping proven by metadata lookup, including unsupported results."""

    __tablename__ = "instrument_mappings"
    __table_args__ = (
        UniqueConstraint(
            "provider", "canonical_symbol", name="uq_instrument_mapping_provider_symbol"
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    canonical_symbol: Mapped[str] = mapped_column(String(64), nullable=False)
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    provider_symbol: Mapped[str | None] = mapped_column(String(96), nullable=True)
    exchange_code: Mapped[str | None] = mapped_column(String(32), nullable=True)
    exchange_mic: Mapped[str | None] = mapped_column(String(32), nullable=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    currency: Mapped[str | None] = mapped_column(String(12), nullable=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    verified_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    details: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)


class DailyRun(Base):
    """Audit record for one idempotent pipeline invocation."""

    __tablename__ = "daily_runs"
    __table_args__ = (
        Index("ix_daily_runs_prediction_status", "prediction_date", "status"),
    )

    run_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    run_type: Mapped[str] = mapped_column(String(24), nullable=False)
    prediction_date: Mapped[date] = mapped_column(Date, nullable=False)
    cutoff_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    current_step: Mapped[str | None] = mapped_column(String(64))
    data_version: Mapped[str] = mapped_column(String(64), nullable=False)
    model_version: Mapped[str | None] = mapped_column(String(64))
    failed_symbols: Mapped[list[str]] = mapped_column(
        JSON, nullable=False, default=list
    )
    error_message: Mapped[str | None] = mapped_column(Text)


class IngestionBatch(Base):
    """Per-provider fetch summary linked to a daily run."""

    __tablename__ = "ingestion_batches"

    batch_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    run_id: Mapped[str] = mapped_column(
        ForeignKey("daily_runs.run_id", ondelete="CASCADE"), nullable=False, index=True
    )
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    requested_symbols: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    succeeded_symbols: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    failed_symbols: Mapped[list[str]] = mapped_column(
        JSON, nullable=False, default=list
    )
    inserted_rows: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    reused_rows: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class ProviderAttempt(Base):
    """Audit every provider candidate and the reason it passed or failed gates."""

    __tablename__ = "provider_attempts"
    __table_args__ = (
        Index("ix_provider_attempt_run_symbol", "run_id", "canonical_symbol"),
    )

    attempt_id: Mapped[int] = mapped_column(
        Integer, primary_key=True, autoincrement=True
    )
    run_id: Mapped[str] = mapped_column(
        ForeignKey("daily_runs.run_id", ondelete="CASCADE"), nullable=False
    )
    canonical_symbol: Mapped[str] = mapped_column(String(64), nullable=False)
    interval: Mapped[str] = mapped_column(String(24), nullable=False)
    registry_key: Mapped[str] = mapped_column(String(32), nullable=False)
    provider: Mapped[str | None] = mapped_column(String(32))
    priority: Mapped[int] = mapped_column(Integer, nullable=False)
    accepted: Mapped[bool] = mapped_column(Boolean, nullable=False)
    data_quality: Mapped[str | None] = mapped_column(String(32))
    freshness_status: Mapped[str | None] = mapped_column(String(32))
    expected_session: Mapped[date | None] = mapped_column(Date)
    actual_session: Mapped[date | None] = mapped_column(Date)
    coverage: Mapped[float | None] = mapped_column(Float)
    reason: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    attempted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )


class ProviderSelection(Base):
    """One immutable provider choice per run/series/interval."""

    __tablename__ = "provider_selections"
    __table_args__ = (
        UniqueConstraint(
            "run_id",
            "canonical_symbol",
            "interval",
            name="uq_provider_selection_run_series_interval",
        ),
        Index("ix_provider_selection_run_symbol", "run_id", "canonical_symbol"),
    )

    selection_id: Mapped[int] = mapped_column(
        Integer, primary_key=True, autoincrement=True
    )
    run_id: Mapped[str] = mapped_column(
        ForeignKey("daily_runs.run_id", ondelete="CASCADE"), nullable=False
    )
    canonical_symbol: Mapped[str] = mapped_column(String(64), nullable=False)
    interval: Mapped[str] = mapped_column(String(24), nullable=False)
    selected_registry_key: Mapped[str] = mapped_column(String(32), nullable=False)
    selected_provider: Mapped[str] = mapped_column(String(32), nullable=False)
    selection_role: Mapped[str] = mapped_column(String(16), nullable=False)
    data_quality: Mapped[str] = mapped_column(String(32), nullable=False)
    freshness_status: Mapped[str] = mapped_column(String(32), nullable=False)
    cutoff_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    coverage: Mapped[float | None] = mapped_column(Float)
    details: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    selected_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )


class RunStep(Base):
    """One retryable, auditable step within a daily pipeline run."""

    __tablename__ = "run_steps"
    __table_args__ = (
        UniqueConstraint(
            "run_id", "step_name", "attempt_number", name="uq_run_steps_attempt"
        ),
        CheckConstraint("attempt_number > 0", name="ck_run_steps_attempt_positive"),
        CheckConstraint(
            "status IN ('RUNNING', 'SUCCESS', 'FAILED', 'SKIPPED')",
            name="ck_run_steps_status",
        ),
        CheckConstraint(
            "finished_at IS NULL OR finished_at >= started_at",
            name="ck_run_steps_time_order",
        ),
        Index("ix_run_steps_run_status", "run_id", "status"),
    )

    step_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(
        ForeignKey("daily_runs.run_id", ondelete="CASCADE"), nullable=False
    )
    step_name: Mapped[str] = mapped_column(String(64), nullable=False)
    attempt_number: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error_message: Mapped[str | None] = mapped_column(Text)
    details: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)


class FeatureSet(Base):
    """Immutable feature-build identity and its point-in-time upper bound."""

    __tablename__ = "feature_sets"
    __table_args__ = (
        UniqueConstraint(
            "run_id",
            "ticker",
            "feature_version",
            "set_kind",
            name="uq_feature_sets_run_ticker_version",
        ),
        UniqueConstraint("idempotency_key", name="uq_feature_sets_idempotency"),
        CheckConstraint(
            "status IN ('BUILDING', 'READY', 'INSUFFICIENT_DATA', 'FAILED')",
            name="ck_feature_sets_status",
        ),
        CheckConstraint(
            "set_kind IN ('MORNING', 'WALK_FORWARD')",
            name="ck_feature_sets_kind",
        ),
        CheckConstraint(
            "training_start <= training_end",
            name="ck_feature_sets_training_dates",
        ),
        CheckConstraint(
            "training_end < prediction_date",
            name="ck_feature_sets_training_before_prediction",
        ),
        CheckConstraint(
            "required_feature_count >= 0 AND missing_feature_count >= 0 "
            "AND missing_feature_count <= required_feature_count",
            name="ck_feature_sets_counts",
        ),
        CheckConstraint(
            "missing_ratio >= 0 AND missing_ratio <= 1",
            name="ck_feature_sets_missing_ratio",
        ),
        CheckConstraint(
            "max_available_timestamp IS NULL OR max_available_timestamp <= cutoff_at",
            name="ck_feature_sets_available_cutoff",
        ),
        CheckConstraint(
            "max_first_observed_at IS NULL OR max_first_observed_at <= cutoff_at",
            name="ck_feature_sets_observed_cutoff",
        ),
        CheckConstraint(
            "max_retrieved_at IS NULL OR max_retrieved_at <= cutoff_at",
            name="ck_feature_sets_retrieved_cutoff",
        ),
        CheckConstraint(
            "finalized_at IS NULL OR finalized_at >= created_at",
            name="ck_feature_sets_time_order",
        ),
        Index("ix_feature_sets_date_ticker", "prediction_date", "ticker"),
    )

    feature_set_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    run_id: Mapped[str] = mapped_column(
        ForeignKey("daily_runs.run_id", ondelete="CASCADE"), nullable=False
    )
    ticker: Mapped[str] = mapped_column(String(16), nullable=False)
    prediction_date: Mapped[date] = mapped_column(Date, nullable=False)
    cutoff_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    feature_version: Mapped[str] = mapped_column(String(64), nullable=False)
    set_kind: Mapped[str] = mapped_column(String(24), nullable=False)
    training_start: Mapped[date] = mapped_column(Date, nullable=False)
    training_end: Mapped[date] = mapped_column(Date, nullable=False)
    config_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    input_manifest_hash: Mapped[str | None] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    required_feature_count: Mapped[int] = mapped_column(Integer, nullable=False)
    missing_feature_count: Mapped[int] = mapped_column(Integer, nullable=False)
    missing_ratio: Mapped[float] = mapped_column(Float, nullable=False)
    max_available_timestamp: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    max_first_observed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    max_retrieved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    finalized_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    details: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False)


class FeatureValue(Base):
    """One named, versioned feature value for a feature set."""

    __tablename__ = "feature_values"
    __table_args__ = (
        UniqueConstraint(
            "feature_set_id",
            "sample_date",
            "row_role",
            "value_kind",
            "feature_name",
            name="uq_feature_values_name",
        ),
        CheckConstraint(
            "row_role IN ('TRAIN', 'SCORE')", name="ck_feature_values_row_role"
        ),
        CheckConstraint(
            "value_kind IN ('FEATURE', 'TARGET')",
            name="ck_feature_values_value_kind",
        ),
        CheckConstraint(
            "(is_missing AND value IS NULL) OR (NOT is_missing AND value IS NOT NULL)",
            name="ck_feature_values_missing_value",
        ),
        CheckConstraint(
            "available_timestamp IS NULL OR available_timestamp <= sample_cutoff_at",
            name="ck_feature_values_available_cutoff",
        ),
    )

    feature_value_id: Mapped[int] = mapped_column(
        Integer, primary_key=True, autoincrement=True
    )
    feature_set_id: Mapped[str] = mapped_column(
        ForeignKey("feature_sets.feature_set_id", ondelete="CASCADE"), nullable=False
    )
    sample_date: Mapped[date] = mapped_column(Date, nullable=False)
    sample_cutoff_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    row_role: Mapped[str] = mapped_column(String(16), nullable=False)
    value_kind: Mapped[str] = mapped_column(String(16), nullable=False)
    feature_name: Mapped[str] = mapped_column(String(128), nullable=False)
    value: Mapped[Decimal | None] = mapped_column(Numeric(30, 12))
    is_missing: Mapped[bool] = mapped_column(Boolean, nullable=False)
    available_timestamp: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    data_quality: Mapped[str | None] = mapped_column(String(32))
    details: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )


class FeatureInput(Base):
    """Raw-row lineage proving that a feature was observable by its cutoff."""

    __tablename__ = "feature_inputs"
    __table_args__ = (
        UniqueConstraint(
            "feature_value_id",
            "input_role",
            "source_type",
            "source_row_id",
            name="uq_feature_inputs_source",
        ),
        CheckConstraint(
            "source_type IN ('MARKET_DATA', 'STOCK_PRICE')",
            name="ck_feature_inputs_source_type",
        ),
        CheckConstraint(
            "(source_type = 'MARKET_DATA' AND market_data_id IS NOT NULL "
            "AND stock_price_id IS NULL AND source_row_id = market_data_id) OR "
            "(source_type = 'STOCK_PRICE' AND stock_price_id IS NOT NULL "
            "AND market_data_id IS NULL AND source_row_id = stock_price_id)",
            name="ck_feature_inputs_exact_source",
        ),
        CheckConstraint(
            "available_timestamp <= first_observed_at",
            name="ck_feature_inputs_available_observed",
        ),
        CheckConstraint(
            "first_observed_at <= retrieved_at",
            name="ck_feature_inputs_observed_retrieved",
        ),
        Index("ix_feature_inputs_feature_value", "feature_value_id"),
    )

    feature_input_id: Mapped[int] = mapped_column(
        Integer, primary_key=True, autoincrement=True
    )
    feature_value_id: Mapped[int] = mapped_column(
        ForeignKey("feature_values.feature_value_id", ondelete="CASCADE"),
        nullable=False,
    )
    input_role: Mapped[str] = mapped_column(String(64), nullable=False)
    source_type: Mapped[str] = mapped_column(String(16), nullable=False)
    source_row_id: Mapped[int] = mapped_column(Integer, nullable=False)
    market_data_id: Mapped[int | None] = mapped_column(ForeignKey("market_data.id"))
    stock_price_id: Mapped[int | None] = mapped_column(ForeignKey("stock_prices.id"))
    raw_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    available_timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    first_observed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    retrieved_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )


class ModelRun(Base):
    """Reproducible fitted-model identity, training interval, and CV evidence."""

    __tablename__ = "model_runs"
    __table_args__ = (
        UniqueConstraint(
            "run_id", "ticker", "task", "algorithm", name="uq_model_runs_identity"
        ),
        UniqueConstraint("idempotency_key", name="uq_model_runs_idempotency"),
        CheckConstraint(
            "task IN ('REGRESSION', 'CLASSIFICATION')", name="ck_model_runs_task"
        ),
        CheckConstraint(
            "status IN ('RUNNING', 'SUCCESS', 'FAILED')",
            name="ck_model_runs_status",
        ),
        CheckConstraint("training_rows > 0", name="ck_model_runs_training_rows"),
        CheckConstraint(
            "training_start <= training_end", name="ck_model_runs_training_dates"
        ),
        CheckConstraint(
            "finished_at IS NULL OR finished_at >= started_at",
            name="ck_model_runs_time_order",
        ),
        Index("ix_model_runs_ticker_status", "ticker", "status"),
    )

    model_run_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    run_id: Mapped[str] = mapped_column(
        ForeignKey("daily_runs.run_id", ondelete="CASCADE"), nullable=False
    )
    ticker: Mapped[str] = mapped_column(String(16), nullable=False)
    feature_set_id: Mapped[str] = mapped_column(
        ForeignKey("feature_sets.feature_set_id"), nullable=False
    )
    task: Mapped[str] = mapped_column(String(24), nullable=False)
    algorithm: Mapped[str] = mapped_column(String(32), nullable=False)
    training_start: Mapped[date] = mapped_column(Date, nullable=False)
    training_end: Mapped[date] = mapped_column(Date, nullable=False)
    cutoff_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    training_rows: Mapped[int] = mapped_column(Integer, nullable=False)
    feature_version: Mapped[str] = mapped_column(String(64), nullable=False)
    model_version: Mapped[str] = mapped_column(String(64), nullable=False)
    random_seed: Mapped[int] = mapped_column(Integer, nullable=False)
    parameters: Mapped[dict[str, Any]] = mapped_column(
        JSON, nullable=False, default=dict
    )
    cv_results: Mapped[dict[str, Any]] = mapped_column(
        JSON, nullable=False, default=dict
    )
    intercept: Mapped[Decimal | None] = mapped_column(Numeric(30, 12))
    artifact_uri: Mapped[str | None] = mapped_column(String(512))
    artifact_hash: Mapped[str | None] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error_message: Mapped[str | None] = mapped_column(Text)
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False)


class ModelCoefficient(Base):
    """Human-readable linear-model coefficient and scaler parameters."""

    __tablename__ = "model_coefficients"
    __table_args__ = (
        UniqueConstraint(
            "model_run_id", "feature_name", name="uq_model_coefficients_feature"
        ),
        CheckConstraint(
            "scaler_scale IS NULL OR scaler_scale > 0",
            name="ck_model_coefficients_scaler_scale",
        ),
    )

    coefficient_id: Mapped[int] = mapped_column(
        Integer, primary_key=True, autoincrement=True
    )
    model_run_id: Mapped[str] = mapped_column(
        ForeignKey("model_runs.model_run_id", ondelete="CASCADE"), nullable=False
    )
    feature_name: Mapped[str] = mapped_column(String(128), nullable=False)
    coefficient: Mapped[Decimal] = mapped_column(Numeric(30, 12), nullable=False)
    scaler_mean: Mapped[Decimal | None] = mapped_column(Numeric(30, 12))
    scaler_scale: Mapped[Decimal | None] = mapped_column(Numeric(30, 12))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )


class PredictionSet(Base):
    """Atomic publication unit for all predictions produced by one run."""

    __tablename__ = "prediction_sets"
    __table_args__ = (
        UniqueConstraint("run_id", name="uq_prediction_sets_run"),
        UniqueConstraint("idempotency_key", name="uq_prediction_sets_idempotency"),
        CheckConstraint(
            "status IN ('BUILDING', 'READY', 'INSUFFICIENT_DATA', 'FAILED')",
            name="ck_prediction_sets_status",
        ),
        CheckConstraint(
            "training_start <= training_end",
            name="ck_prediction_sets_training_dates",
        ),
        CheckConstraint(
            "training_end < prediction_date",
            name="ck_prediction_sets_training_before_prediction",
        ),
        CheckConstraint(
            "published_at IS NULL OR published_at >= generated_at",
            name="ck_prediction_sets_time_order",
        ),
        Index("ix_prediction_sets_date_status", "prediction_date", "status"),
    )

    prediction_set_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    run_id: Mapped[str] = mapped_column(
        ForeignKey("daily_runs.run_id", ondelete="CASCADE"), nullable=False
    )
    prediction_date: Mapped[date] = mapped_column(Date, nullable=False)
    cutoff_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    feature_version: Mapped[str] = mapped_column(String(64), nullable=False)
    model_version: Mapped[str] = mapped_column(String(64), nullable=False)
    strategy_version: Mapped[str] = mapped_column(String(64), nullable=False)
    training_start: Mapped[date] = mapped_column(Date, nullable=False)
    training_end: Mapped[date] = mapped_column(Date, nullable=False)
    generated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    warnings: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False)


class Prediction(Base):
    """Per-stock regression/classification output and trading decision."""

    __tablename__ = "predictions"
    __table_args__ = (
        UniqueConstraint(
            "prediction_set_id", "ticker", name="uq_predictions_set_ticker"
        ),
        UniqueConstraint("idempotency_key", name="uq_predictions_idempotency"),
        CheckConstraint(
            "status IN ('SUCCESS', 'INSUFFICIENT_DATA', 'FAILED')",
            name="ck_predictions_status",
        ),
        CheckConstraint(
            "signal IN ('BUY', 'NO_BUY', 'NONE')", name="ck_predictions_signal"
        ),
        CheckConstraint(
            "probability_up IS NULL OR (probability_up >= 0 AND probability_up <= 1)",
            name="ck_predictions_probability",
        ),
        CheckConstraint(
            "confidence_score IS NULL OR "
            "(confidence_score >= 0 AND confidence_score <= 100)",
            name="ck_predictions_confidence",
        ),
        CheckConstraint(
            "(prediction_interval_low IS NULL AND prediction_interval_high IS NULL) "
            "OR (prediction_interval_low IS NOT NULL "
            "AND prediction_interval_high IS NOT NULL "
            "AND prediction_interval_low <= prediction_interval_high)",
            name="ck_predictions_interval",
        ),
        CheckConstraint(
            "feature_coverage IS NULL OR "
            "(feature_coverage >= 0 AND feature_coverage <= 1)",
            name="ck_predictions_feature_coverage",
        ),
        CheckConstraint(
            "reference_price IS NULL OR reference_price > 0",
            name="ck_predictions_reference_price",
        ),
        CheckConstraint("rank IS NULL OR rank > 0", name="ck_predictions_rank"),
        CheckConstraint(
            "status != 'SUCCESS' OR "
            "(regression_model_run_id IS NOT NULL "
            "AND classification_model_run_id IS NOT NULL "
            "AND predicted_intraday_return IS NOT NULL "
            "AND probability_up IS NOT NULL)",
            name="ck_predictions_success_values",
        ),
        Index("ix_predictions_ticker_created", "ticker", "created_at"),
    )

    prediction_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    prediction_set_id: Mapped[str] = mapped_column(
        ForeignKey("prediction_sets.prediction_set_id", ondelete="CASCADE"),
        nullable=False,
    )
    ticker: Mapped[str] = mapped_column(String(16), nullable=False)
    feature_set_id: Mapped[str] = mapped_column(
        ForeignKey("feature_sets.feature_set_id"), nullable=False
    )
    reference_stock_price_id: Mapped[int | None] = mapped_column(
        ForeignKey("stock_prices.id")
    )
    regression_model_run_id: Mapped[str | None] = mapped_column(
        ForeignKey("model_runs.model_run_id")
    )
    classification_model_run_id: Mapped[str | None] = mapped_column(
        ForeignKey("model_runs.model_run_id")
    )
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    predicted_intraday_return: Mapped[Decimal | None] = mapped_column(Numeric(30, 12))
    prediction_interval_low: Mapped[Decimal | None] = mapped_column(Numeric(30, 12))
    prediction_interval_high: Mapped[Decimal | None] = mapped_column(Numeric(30, 12))
    probability_up: Mapped[Decimal | None] = mapped_column(Numeric(18, 12))
    reference_price: Mapped[Decimal | None] = mapped_column(Numeric(24, 10))
    reference_basis: Mapped[str] = mapped_column(String(32), nullable=False)
    predicted_price_difference: Mapped[Decimal | None] = mapped_column(Numeric(24, 10))
    predicted_close: Mapped[Decimal | None] = mapped_column(Numeric(24, 10))
    signal: Mapped[str] = mapped_column(String(16), nullable=False)
    rank: Mapped[int | None] = mapped_column(Integer)
    return_threshold: Mapped[Decimal] = mapped_column(Numeric(18, 12), nullable=False)
    probability_threshold: Mapped[Decimal] = mapped_column(
        Numeric(18, 12), nullable=False
    )
    confidence_score: Mapped[Decimal | None] = mapped_column(Numeric(8, 4))
    positive_factors: Mapped[list[str]] = mapped_column(
        JSON, nullable=False, default=list
    )
    negative_factors: Mapped[list[str]] = mapped_column(
        JSON, nullable=False, default=list
    )
    feature_coverage: Mapped[float | None] = mapped_column(Float)
    warnings: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False)


class ActualResult(Base):
    """Append-only observed outcome revision used to score a prediction."""

    __tablename__ = "actual_results"
    __table_args__ = (
        UniqueConstraint(
            "prediction_id", "result_version", name="uq_actual_results_version"
        ),
        UniqueConstraint(
            "supersedes_actual_result_id", name="uq_actual_results_supersedes"
        ),
        UniqueConstraint("idempotency_key", name="uq_actual_results_idempotency"),
        CheckConstraint("result_version > 0", name="ck_actual_results_version"),
        CheckConstraint(
            "status IN ('PENDING', 'FINAL', 'CORRECTED')",
            name="ck_actual_results_status",
        ),
        CheckConstraint(
            "actual_open IS NULL OR actual_open > 0",
            name="ck_actual_results_open",
        ),
        CheckConstraint(
            "actual_close IS NULL OR actual_close > 0",
            name="ck_actual_results_close",
        ),
        CheckConstraint(
            "status = 'PENDING' OR "
            "(actual_open IS NOT NULL AND actual_close IS NOT NULL "
            "AND actual_intraday_return IS NOT NULL "
            "AND actual_price_difference IS NOT NULL "
            "AND finalized_at IS NOT NULL)",
            name="ck_actual_results_final_values",
        ),
        Index("ix_actual_results_prediction", "prediction_id", "result_version"),
    )

    actual_result_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    prediction_id: Mapped[str] = mapped_column(
        ForeignKey("predictions.prediction_id", ondelete="CASCADE"), nullable=False
    )
    stock_price_id: Mapped[int | None] = mapped_column(ForeignKey("stock_prices.id"))
    supersedes_actual_result_id: Mapped[str | None] = mapped_column(
        ForeignKey("actual_results.actual_result_id")
    )
    result_version: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    actual_open: Mapped[Decimal | None] = mapped_column(Numeric(24, 10))
    actual_close: Mapped[Decimal | None] = mapped_column(Numeric(24, 10))
    actual_intraday_return: Mapped[Decimal | None] = mapped_column(Numeric(30, 12))
    actual_price_difference: Mapped[Decimal | None] = mapped_column(Numeric(24, 10))
    raw_hash: Mapped[str | None] = mapped_column(String(64))
    observed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finalized_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False)


class SimulatedTrade(Base):
    """Paper-only trade outcome; no field represents a broker order."""

    __tablename__ = "simulated_trades"
    __table_args__ = (
        UniqueConstraint(
            "prediction_id",
            "actual_result_id",
            "strategy_version",
            name="uq_simulated_trades_valuation",
        ),
        UniqueConstraint("idempotency_key", name="uq_simulated_trades_idempotency"),
        CheckConstraint(
            "status IN ('NOT_TRIGGERED', 'PENDING', 'FINAL', 'INSUFFICIENT_CONFIG')",
            name="ck_simulated_trades_status",
        ),
        CheckConstraint("is_simulated", name="ck_simulated_trades_paper_only"),
        CheckConstraint("capital_jpy > 0", name="ck_simulated_trades_capital"),
        CheckConstraint("shares >= 0", name="ck_simulated_trades_shares"),
        CheckConstraint(
            "commission_cost_jpy IS NULL OR commission_cost_jpy >= 0",
            name="ck_simulated_trades_commission",
        ),
        CheckConstraint(
            "slippage_cost_jpy IS NULL OR slippage_cost_jpy >= 0",
            name="ck_simulated_trades_slippage",
        ),
        CheckConstraint(
            "closed_at IS NULL OR opened_at IS NULL OR closed_at >= opened_at",
            name="ck_simulated_trades_time_order",
        ),
        Index("ix_simulated_trades_status", "status"),
    )

    trade_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    prediction_id: Mapped[str] = mapped_column(
        ForeignKey("predictions.prediction_id", ondelete="CASCADE"), nullable=False
    )
    actual_result_id: Mapped[str | None] = mapped_column(
        ForeignKey("actual_results.actual_result_id")
    )
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    is_simulated: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    capital_jpy: Mapped[Decimal] = mapped_column(Numeric(24, 4), nullable=False)
    shares: Mapped[int] = mapped_column(Integer, nullable=False)
    entry_price: Mapped[Decimal | None] = mapped_column(Numeric(24, 10))
    exit_price: Mapped[Decimal | None] = mapped_column(Numeric(24, 10))
    gross_profit_jpy: Mapped[Decimal | None] = mapped_column(Numeric(24, 4))
    commission_cost_jpy: Mapped[Decimal | None] = mapped_column(Numeric(24, 4))
    slippage_cost_jpy: Mapped[Decimal | None] = mapped_column(Numeric(24, 4))
    net_profit_jpy: Mapped[Decimal | None] = mapped_column(Numeric(24, 4))
    realized_return: Mapped[Decimal | None] = mapped_column(Numeric(30, 12))
    opened_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    strategy_version: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False)


class MetricSnapshot(Base):
    """Versioned point-in-time evaluation metrics for dashboard reads."""

    __tablename__ = "metric_snapshots"
    __table_args__ = (
        UniqueConstraint(
            "ticker",
            "as_of_date",
            "model_version",
            "strategy_version",
            "evaluation_window",
            name="uq_metric_snapshots_identity",
        ),
        UniqueConstraint("idempotency_key", name="uq_metric_snapshots_idempotency"),
        CheckConstraint(
            "status IN ('READY', 'INSUFFICIENT_DATA', 'FAILED')",
            name="ck_metric_snapshots_status",
        ),
        CheckConstraint(
            "sample_status IN ('NO_TRADES', 'LOW_SAMPLE', 'SUFFICIENT')",
            name="ck_metric_snapshots_sample_status",
        ),
        CheckConstraint(
            "prediction_count >= 0 AND trade_count >= 0 AND win_count >= 0 "
            "AND loss_count >= 0 AND win_count + loss_count <= trade_count",
            name="ck_metric_snapshots_counts",
        ),
        CheckConstraint(
            "win_rate IS NULL OR (win_rate >= 0 AND win_rate <= 1)",
            name="ck_metric_snapshots_win_rate",
        ),
        CheckConstraint(
            "direction_accuracy IS NULL OR "
            "(direction_accuracy >= 0 AND direction_accuracy <= 1)",
            name="ck_metric_snapshots_direction_accuracy",
        ),
        CheckConstraint(
            "readability_score IS NULL OR "
            "(readability_score >= 0 AND readability_score <= 100)",
            name="ck_metric_snapshots_readability",
        ),
        Index("ix_metric_snapshots_date_ticker", "as_of_date", "ticker"),
    )

    metric_snapshot_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    ticker: Mapped[str] = mapped_column(String(16), nullable=False)
    as_of_date: Mapped[date] = mapped_column(Date, nullable=False)
    model_version: Mapped[str] = mapped_column(String(64), nullable=False)
    strategy_version: Mapped[str] = mapped_column(String(64), nullable=False)
    evaluation_window: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    sample_status: Mapped[str] = mapped_column(String(32), nullable=False)
    prediction_count: Mapped[int] = mapped_column(Integer, nullable=False)
    trade_count: Mapped[int] = mapped_column(Integer, nullable=False)
    win_count: Mapped[int] = mapped_column(Integer, nullable=False)
    loss_count: Mapped[int] = mapped_column(Integer, nullable=False)
    win_rate: Mapped[Decimal | None] = mapped_column(Numeric(18, 12))
    gross_profit_jpy: Mapped[Decimal | None] = mapped_column(Numeric(24, 4))
    gross_loss_jpy: Mapped[Decimal | None] = mapped_column(Numeric(24, 4))
    net_profit_jpy: Mapped[Decimal | None] = mapped_column(Numeric(24, 4))
    average_win_jpy: Mapped[Decimal | None] = mapped_column(Numeric(24, 4))
    average_loss_jpy: Mapped[Decimal | None] = mapped_column(Numeric(24, 4))
    largest_win_jpy: Mapped[Decimal | None] = mapped_column(Numeric(24, 4))
    largest_loss_jpy: Mapped[Decimal | None] = mapped_column(Numeric(24, 4))
    payoff_ratio: Mapped[Decimal | None] = mapped_column(Numeric(30, 12))
    profit_factor: Mapped[Decimal | None] = mapped_column(Numeric(30, 12))
    expectancy_jpy: Mapped[Decimal | None] = mapped_column(Numeric(24, 4))
    sharpe_ratio: Mapped[Decimal | None] = mapped_column(Numeric(30, 12))
    sortino_ratio: Mapped[Decimal | None] = mapped_column(Numeric(30, 12))
    max_drawdown: Mapped[Decimal | None] = mapped_column(Numeric(30, 12))
    pearson_correlation: Mapped[Decimal | None] = mapped_column(Numeric(30, 12))
    spearman_correlation: Mapped[Decimal | None] = mapped_column(Numeric(30, 12))
    direction_accuracy: Mapped[Decimal | None] = mapped_column(Numeric(18, 12))
    readability_score: Mapped[Decimal | None] = mapped_column(Numeric(8, 4))
    input_manifest_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    details: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    computed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False)


class EmailLog(Base):
    """Idempotent delivery record for one operational mail.

    ``prediction_set_id`` is nullable because the after-close summary is a
    delivery in its own right and, on a JPX holiday, there is no publication to
    attach it to. ``idempotency_key`` carries the date and is what actually
    stops a retried workflow sending the same mail twice.
    """

    __tablename__ = "email_logs"
    __table_args__ = (
        UniqueConstraint(
            "prediction_set_id",
            "recipient",
            "template_version",
            name="uq_email_logs_delivery",
        ),
        UniqueConstraint("idempotency_key", name="uq_email_logs_idempotency"),
        UniqueConstraint("provider_message_id", name="uq_email_logs_provider_message"),
        CheckConstraint(
            "status IN ('PENDING', 'SENDING', 'SENT', 'FAILED')",
            name="ck_email_logs_status",
        ),
        CheckConstraint("attempt_count >= 0", name="ck_email_logs_attempt_count"),
        CheckConstraint(
            "sent_at IS NULL OR sent_at >= created_at", name="ck_email_logs_time_order"
        ),
        Index("ix_email_logs_status_created", "status", "created_at"),
    )

    email_log_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    prediction_set_id: Mapped[str | None] = mapped_column(
        ForeignKey("prediction_sets.prediction_set_id", ondelete="CASCADE"),
        nullable=True,
    )
    recipient: Mapped[str] = mapped_column(String(320), nullable=False)
    template_version: Mapped[str] = mapped_column(String(64), nullable=False)
    subject: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    provider_message_id: Mapped[str | None] = mapped_column(String(255))
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False)
    last_error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False)
