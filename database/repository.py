"""Idempotent Phase 1 persistence operations."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import UTC, date, datetime
from typing import cast
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from data.provider_router import ProviderAttempt as ProviderAttemptResult
from data.providers.base import SymbolResolution
from data.schemas import AvailabilityMethod, MarketBar
from data.snapshot import FreshnessStatus, SelectionRole
from database.models import (
    DailyRun,
    IngestionBatch,
    InstrumentMapping,
    MarketData,
    ProviderAttempt,
    ProviderSelection,
    StockPrice,
)

UTC = UTC


def _as_utc(value: datetime) -> datetime:
    """Normalize timestamps, including SQLite's test-only naive round trips."""

    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


@dataclass(frozen=True, slots=True)
class UpsertSummary:
    inserted: int = 0
    reused: int = 0


class MarketDataRepository:
    """Keep corrected provider revisions instead of overwriting them."""

    def __init__(self, session: Session) -> None:
        self.session = session

    @staticmethod
    def _model_for(
        row: MarketBar, stock_symbols: set[str]
    ) -> type[MarketData] | type[StockPrice]:
        return StockPrice if row.canonical_symbol in stock_symbols else MarketData

    def upsert_bars(
        self,
        rows: list[MarketBar],
        *,
        stock_symbols: set[str] | None = None,
    ) -> UpsertSummary:
        """Insert new revisions and reuse identical rows on reruns."""

        targets = stock_symbols or set()
        inserted = 0
        reused = 0
        for row in rows:
            if row.raw_hash is None:
                raise ValueError("raw_hash is required for idempotent persistence")
            model = self._model_for(row, targets)
            existing = cast(
                MarketData | StockPrice | None,
                self.session.scalar(
                    select(model).where(
                        model.provider == row.provider,
                        model.symbol == row.provider_symbol,
                        model.interval == row.interval.value,
                        model.timestamp == row.timestamp,
                        model.raw_hash == row.raw_hash,
                    )
                ),
            )
            if existing is not None:
                if _as_utc(row.retrieved_at) > _as_utc(existing.last_seen_at):
                    existing.last_seen_at = row.retrieved_at
                reused += 1
                continue
            prior_revision = cast(
                MarketData | StockPrice | None,
                self.session.scalar(
                    select(model)
                    .where(
                        model.provider == row.provider,
                        model.symbol == row.provider_symbol,
                        model.interval == row.interval.value,
                        model.timestamp == row.timestamp,
                    )
                    .order_by(model.available_timestamp.desc())
                    .limit(1)
                ),
            )
            persisted_row = row
            if prior_revision is not None:
                persisted_row = replace(
                    row,
                    available_timestamp=row.first_observed_at,
                    availability_method=AvailabilityMethod.FIRST_OBSERVED,
                    quality_flags=tuple(
                        sorted(set((*row.quality_flags, "corrected_revision")))
                    ),
                )
            self.session.add(
                model(
                    canonical_symbol=persisted_row.canonical_symbol,
                    symbol=persisted_row.provider_symbol,
                    provider=persisted_row.provider,
                    market=persisted_row.market,
                    market_timezone=persisted_row.market_timezone,
                    market_date=persisted_row.market_date,
                    timestamp=persisted_row.timestamp,
                    source_timestamp=persisted_row.source_timestamp,
                    available_timestamp=persisted_row.available_timestamp,
                    first_observed_at=persisted_row.first_observed_at,
                    retrieved_at=persisted_row.retrieved_at,
                    last_seen_at=persisted_row.retrieved_at,
                    interval=persisted_row.interval.value,
                    availability_method=persisted_row.availability_method.value,
                    data_quality=persisted_row.data_quality.value,
                    is_realtime=persisted_row.is_realtime,
                    is_delayed=persisted_row.is_delayed,
                    open=persisted_row.open,
                    high=persisted_row.high,
                    low=persisted_row.low,
                    close=persisted_row.close,
                    adjusted_close=persisted_row.adjusted_close,
                    volume=persisted_row.volume,
                    currency=persisted_row.currency,
                    raw_hash=persisted_row.raw_hash,
                    quality_flags=list(persisted_row.quality_flags),
                )
            )
            inserted += 1
        self.session.flush()
        return UpsertSummary(inserted, reused)

    def save_provider_attempts(
        self,
        *,
        run_id: str,
        canonical_symbol: str,
        interval: str,
        attempts: tuple[ProviderAttemptResult, ...],
        expected_session: date | None = None,
        actual_session: date | None = None,
    ) -> None:
        """Persist every priority/gate decision for later diagnosis."""

        attempted_at = datetime.now(UTC)
        for priority, attempt in enumerate(attempts):
            self.session.add(
                ProviderAttempt(
                    run_id=run_id,
                    canonical_symbol=canonical_symbol,
                    interval=interval,
                    registry_key=attempt.registry_key,
                    provider=attempt.provider,
                    priority=priority,
                    accepted=attempt.accepted,
                    data_quality=attempt.data_quality.value,
                    freshness_status=attempt.freshness_status.value,
                    expected_session=expected_session,
                    actual_session=actual_session,
                    coverage=attempt.coverage,
                    reason={"message": attempt.reason},
                    attempted_at=attempted_at,
                )
            )
        self.session.flush()

    def save_provider_selection(
        self,
        *,
        run_id: str,
        canonical_symbol: str,
        interval: str,
        selected_registry_key: str,
        selected_provider: str,
        selection_role: SelectionRole,
        data_quality: str,
        freshness_status: FreshnessStatus,
        cutoff_at: datetime,
        coverage: float | None = None,
        details: dict[str, object] | None = None,
    ) -> ProviderSelection:
        """Record one deterministic provider choice; reject conflicting rewrites."""

        existing = self.session.scalar(
            select(ProviderSelection).where(
                ProviderSelection.run_id == run_id,
                ProviderSelection.canonical_symbol == canonical_symbol,
                ProviderSelection.interval == interval,
            )
        )
        identity = (selected_registry_key, selected_provider, selection_role.value)
        if existing is not None:
            persisted = (
                existing.selected_registry_key,
                existing.selected_provider,
                existing.selection_role,
            )
            if persisted != identity:
                raise ValueError("provider selection is immutable within one run")
            return existing
        selection = ProviderSelection(
            run_id=run_id,
            canonical_symbol=canonical_symbol,
            interval=interval,
            selected_registry_key=selected_registry_key,
            selected_provider=selected_provider,
            selection_role=selection_role.value,
            data_quality=data_quality,
            freshness_status=freshness_status.value,
            cutoff_at=cutoff_at,
            coverage=coverage,
            details=dict(details or {}),
            selected_at=datetime.now(UTC),
        )
        self.session.add(selection)
        self.session.flush()
        return selection

    def save_resolution(
        self,
        *,
        canonical_symbol: str,
        name: str,
        provider: str,
        resolution: SymbolResolution | None,
        verified_at: datetime,
        reason: str | None = None,
    ) -> InstrumentMapping:
        """Persist both verified and unsupported resolution results."""

        mapping = self.session.scalar(
            select(InstrumentMapping).where(
                InstrumentMapping.provider == provider,
                InstrumentMapping.canonical_symbol == canonical_symbol,
            )
        )
        values = {
            "provider_symbol": resolution.provider_symbol if resolution else None,
            "exchange_code": resolution.exchange_code if resolution else None,
            "exchange_mic": resolution.exchange_mic if resolution else None,
            "name": resolution.name if resolution else name,
            "currency": resolution.currency if resolution else None,
            "status": "VERIFIED" if resolution else "UNSUPPORTED",
            "verified_at": verified_at,
            "details": {"reason": reason} if reason else {},
        }
        if mapping is None:
            mapping = InstrumentMapping(
                canonical_symbol=canonical_symbol,
                provider=provider,
                **values,
            )
            self.session.add(mapping)
        else:
            for key, value in values.items():
                setattr(mapping, key, value)
        self.session.flush()
        return mapping

    def create_run(
        self,
        *,
        run_type: str,
        prediction_date: date,
        data_version: str,
        cutoff_at: datetime | None = None,
    ) -> DailyRun:
        run = DailyRun(
            run_id=str(uuid4()),
            run_type=run_type,
            prediction_date=prediction_date,
            cutoff_at=cutoff_at,
            started_at=datetime.now(UTC),
            status="RUNNING",
            current_step="INITIALIZE",
            data_version=data_version,
            failed_symbols=[],
        )
        self.session.add(run)
        self.session.flush()
        return run

    def create_ingestion_batch(
        self,
        *,
        run_id: str,
        provider: str,
        requested_symbols: int,
    ) -> IngestionBatch:
        """Start one provider-specific fetch audit record."""

        batch = IngestionBatch(
            batch_id=str(uuid4()),
            run_id=run_id,
            provider=provider,
            started_at=datetime.now(UTC),
            status="RUNNING",
            requested_symbols=requested_symbols,
            succeeded_symbols=0,
            failed_symbols=[],
            inserted_rows=0,
            reused_rows=0,
        )
        self.session.add(batch)
        self.session.flush()
        return batch

    def finish_ingestion_batch(
        self,
        batch: IngestionBatch,
        *,
        status: str,
        succeeded_symbols: int,
        failed_symbols: list[str],
        inserted_rows: int,
        reused_rows: int,
    ) -> None:
        """Complete an ingestion audit record."""

        batch.status = status
        batch.finished_at = datetime.now(UTC)
        batch.succeeded_symbols = succeeded_symbols
        batch.failed_symbols = sorted(set(failed_symbols))
        batch.inserted_rows = inserted_rows
        batch.reused_rows = reused_rows
        self.session.flush()

    def finish_run(
        self,
        run: DailyRun,
        *,
        status: str,
        failed_symbols: list[str] | None = None,
        error_message: str | None = None,
    ) -> None:
        run.status = status
        run.finished_at = datetime.now(UTC)
        run.current_step = (
            "COMPLETE" if status in {"SUCCESS", "PARTIAL", "SKIPPED"} else "FAILED"
        )
        run.failed_symbols = sorted(set(failed_symbols or []))
        run.error_message = error_message
        self.session.flush()
