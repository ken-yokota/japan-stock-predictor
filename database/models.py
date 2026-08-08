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
