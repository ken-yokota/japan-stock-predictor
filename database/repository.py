"""Idempotent Phase 1 persistence operations."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any, cast
from uuid import uuid4

from sqlalchemy import func, select, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.orm import Session

from data.provider_router import ProviderAttempt as ProviderAttemptResult
from data.providers.base import SymbolResolution
from data.schemas import AvailabilityMethod, MarketBar
from data.snapshot import FreshnessStatus, SelectionRole
from database.models import (
    ActualResult,
    DailyRun,
    EmailLog,
    FeatureInput,
    FeatureSet,
    FeatureValue,
    IngestionBatch,
    InstrumentMapping,
    MarketData,
    MetricSnapshot,
    ModelCoefficient,
    ModelRun,
    Prediction,
    PredictionSet,
    ProviderAttempt,
    ProviderSelection,
    RunStep,
    SimulatedTrade,
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


def _require_aware(value: datetime, *, field_name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} must be timezone-aware")
    return value.astimezone(UTC)


def _require_hash(value: str, *, field_name: str) -> str:
    if len(value) != 64:
        raise ValueError(f"{field_name} must be a 64-character digest")
    try:
        int(value, 16)
    except ValueError as exc:
        raise ValueError(f"{field_name} must be hexadecimal") from exc
    return value.lower()


def _same_instant(left: datetime, right: datetime) -> bool:
    return _as_utc(left) == _as_utc(right)


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
        grouped: dict[
            tuple[type[MarketData] | type[StockPrice], str, str, str],
            list[MarketBar],
        ] = defaultdict(list)
        for row in rows:
            if row.raw_hash is None:
                raise ValueError("raw_hash is required for idempotent persistence")
            model = self._model_for(row, targets)
            grouped[
                (model, row.provider, row.provider_symbol, row.interval.value)
            ].append(row)

        # A normal morning contains roughly 20k already-known bars.  Loading
        # their revisions once per logical series avoids one or two hosted-DB
        # round trips for every individual bar.
        for (model, provider, symbol, interval), group in grouped.items():
            timestamps = list(dict.fromkeys(row.timestamp for row in group))
            stored: list[MarketData | StockPrice] = []
            # Keep the IN predicate below conservative SQLite/Postgres bind
            # limits while retaining the same production query shape.
            for offset in range(0, len(timestamps), 500):
                stored.extend(
                    self.session.scalars(
                        select(model).where(
                            model.provider == provider,
                            model.symbol == symbol,
                            model.interval == interval,
                            model.timestamp.in_(timestamps[offset : offset + 500]),
                        )
                    )
                )
            exact = {
                (_as_utc(existing.timestamp), existing.raw_hash): existing
                for existing in stored
            }
            prior_timestamps = {_as_utc(existing.timestamp) for existing in stored}

            for row in group:
                timestamp_key = _as_utc(row.timestamp)
                raw_hash = row.raw_hash
                if raw_hash is None:  # Defensive narrowing for static analysis.
                    raise ValueError("raw_hash is required for idempotent persistence")
                existing = exact.get((timestamp_key, raw_hash))
                if existing is not None:
                    if _as_utc(row.retrieved_at) > _as_utc(existing.last_seen_at):
                        existing.last_seen_at = row.retrieved_at
                    reused += 1
                    continue
                persisted_row = row
                if timestamp_key in prior_timestamps:
                    persisted_row = replace(
                        row,
                        available_timestamp=row.first_observed_at,
                        availability_method=AvailabilityMethod.FIRST_OBSERVED,
                        quality_flags=tuple(
                            sorted(set((*row.quality_flags, "corrected_revision")))
                        ),
                    )
                new_row = model(
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
                self.session.add(new_row)
                persisted_hash = persisted_row.raw_hash
                if persisted_hash is None:  # replace() preserves the checked hash.
                    raise ValueError("raw_hash is required for idempotent persistence")
                exact[(timestamp_key, persisted_hash)] = new_row
                prior_timestamps.add(timestamp_key)
                inserted += 1
        self.session.flush()
        return UpsertSummary(inserted, reused)

    def stored_coverage(
        self,
        interval: str = "eod",
        *,
        cutoff_at: datetime | None = None,
    ) -> dict[str, date]:
        """Return the newest stored market date per series.

        Used to skip a fetch entirely. Narrowing the requested date range does
        not help -- one series is one request whether it asks for five days or
        five hundred and fifty -- so the only lever is not asking at all when
        storage already reaches the date being requested.
        """

        market_filters = [MarketData.interval == interval]
        stock_filters = [StockPrice.interval == interval]
        if cutoff_at is not None:
            normalized_cutoff = _require_aware(cutoff_at, field_name="cutoff_at")
            market_filters.extend(
                (
                    MarketData.available_timestamp <= normalized_cutoff,
                    MarketData.first_observed_at <= normalized_cutoff,
                    MarketData.retrieved_at <= normalized_cutoff,
                )
            )
            stock_filters.extend(
                (
                    StockPrice.available_timestamp <= normalized_cutoff,
                    StockPrice.first_observed_at <= normalized_cutoff,
                    StockPrice.retrieved_at <= normalized_cutoff,
                )
            )
        rows = self.session.execute(
            select(
                MarketData.canonical_symbol,
                func.max(MarketData.market_date),
            )
            .where(*market_filters)
            .group_by(MarketData.canonical_symbol)
        ).all()
        stock_rows = self.session.execute(
            select(StockPrice.canonical_symbol, func.max(StockPrice.market_date))
            .where(*stock_filters)
            .group_by(StockPrice.canonical_symbol)
        ).all()
        return {
            str(symbol): latest
            for symbol, latest in [*rows, *stock_rows]
            if latest is not None
        }

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


class PredictionPipelineRepository:
    """Persist prediction artifacts with fail-closed PIT and retry semantics."""

    _STEP_TERMINAL = frozenset({"SUCCESS", "FAILED", "SKIPPED"})
    _FEATURE_TERMINAL = frozenset({"READY", "INSUFFICIENT_DATA", "FAILED"})
    _MODEL_TERMINAL = frozenset({"SUCCESS", "FAILED"})
    _PREDICTION_SET_TERMINAL = frozenset({"READY", "INSUFFICIENT_DATA", "FAILED"})

    def __init__(self, session: Session) -> None:
        self.session = session
        # Per-set indexes of what has already been written, so idempotency does
        # not cost a query per row. Scoped to this repository instance, which
        # lives for one session, so they cannot outlive their transaction.
        self._feature_value_caches: dict[
            str, dict[tuple[date, str, str, str], FeatureValue]
        ] = {}
        self._feature_input_keys: dict[tuple[int, str, str, int], FeatureInput] = {}
        self._feature_inputs_loaded_for_sets: set[str] = set()
        self._model_coefficient_caches: dict[str, dict[str, ModelCoefficient]] = {}
        # Rows fetched by primary key during one write, keyed by model and id.
        self._row_cache: dict[tuple[str, Any], Any] = {}

    def _cached_get(self, model: type[Any], row_id: Any) -> Any:
        """Keep frequently validated ORM rows alive for the whole write."""

        key = (model.__name__, row_id)
        if key not in self._row_cache:
            self._row_cache[key] = self.session.get(model, row_id)
        return self._row_cache[key]

    def flush_pending(self) -> None:
        """Flush one completed persistence batch instead of every cell."""

        self.session.flush()

    def preload_feature_input_sources(
        self,
        *,
        market_data_ids: set[int],
        stock_price_ids: set[int],
    ) -> None:
        """Load raw lineage sources in bounded batches before cell validation."""

        for model, requested in (
            (MarketData, market_data_ids),
            (StockPrice, stock_price_ids),
        ):
            missing = {
                row_id
                for row_id in requested
                if (model.__name__, row_id) not in self._row_cache
            }
            ordered = sorted(missing)
            for offset in range(0, len(ordered), 500):
                for row in self.session.scalars(
                    select(model).where(model.id.in_(ordered[offset : offset + 500]))
                ):
                    persisted = cast(MarketData | StockPrice, row)
                    self._row_cache[(model.__name__, persisted.id)] = persisted
            unresolved = {
                row_id
                for row_id in requested
                if self._row_cache.get((model.__name__, row_id)) is None
            }
            if unresolved:
                label = "MARKET_DATA" if model is MarketData else "STOCK_PRICE"
                sample = sorted(unresolved)[:5]
                raise ValueError(f"unknown {label} rows: {sample}")

    def start_run_step(
        self,
        *,
        run_id: str,
        step_name: str,
        attempt_number: int,
        details: Mapping[str, object] | None = None,
        started_at: datetime | None = None,
    ) -> RunStep:
        """Start one append-only retry attempt, reusing the same identity."""

        if not step_name.strip():
            raise ValueError("step_name must not be blank")
        if attempt_number <= 0:
            raise ValueError("attempt_number must be positive")
        if self.session.get(DailyRun, run_id) is None:
            raise ValueError(f"unknown daily run: {run_id}")
        existing = self.session.scalar(
            select(RunStep).where(
                RunStep.run_id == run_id,
                RunStep.step_name == step_name,
                RunStep.attempt_number == attempt_number,
            )
        )
        if existing is not None:
            return existing
        timestamp = started_at or datetime.now(UTC)
        step = RunStep(
            run_id=run_id,
            step_name=step_name,
            attempt_number=attempt_number,
            status="RUNNING",
            started_at=_require_aware(timestamp, field_name="started_at"),
            details=dict(details or {}),
        )
        self.session.add(step)
        self.session.flush()
        return step

    def finish_run_step(
        self,
        step: RunStep,
        *,
        status: str,
        error_message: str | None = None,
        details: Mapping[str, object] | None = None,
        finished_at: datetime | None = None,
    ) -> RunStep:
        """Apply the only permitted RUNNING-to-terminal step transition."""

        if status not in self._STEP_TERMINAL:
            raise ValueError(f"invalid terminal run-step status: {status}")
        if step.status != "RUNNING":
            if step.status == status:
                return step
            raise ValueError(f"run step is already terminal: {step.status}")
        if status == "FAILED" and not error_message:
            raise ValueError("failed run step requires an error message")
        if status != "FAILED" and error_message is not None:
            raise ValueError("only failed run steps may store an error message")
        timestamp = _require_aware(
            finished_at or datetime.now(UTC), field_name="finished_at"
        )
        if timestamp < _as_utc(step.started_at):
            raise ValueError("finished_at cannot precede started_at")
        step.status = status
        step.finished_at = timestamp
        step.error_message = error_message
        if details is not None:
            step.details = dict(details)
        self.session.flush()
        return step

    def create_feature_set(
        self,
        *,
        run_id: str,
        ticker: str,
        prediction_date: date,
        cutoff_at: datetime,
        feature_version: str,
        set_kind: str,
        training_start: date,
        training_end: date,
        config_hash: str,
        required_feature_count: int,
        idempotency_key: str,
        details: Mapping[str, object] | None = None,
    ) -> FeatureSet:
        """Create a frozen feature-build identity before any values are stored."""

        existing = self.session.scalar(
            select(FeatureSet).where(FeatureSet.idempotency_key == idempotency_key)
        )
        if existing is not None:
            persisted_identity = (
                existing.run_id,
                existing.ticker,
                existing.prediction_date,
                existing.feature_version,
                existing.set_kind,
            )
            requested_identity = (
                run_id,
                ticker,
                prediction_date,
                feature_version,
                set_kind,
            )
            if persisted_identity != requested_identity:
                raise ValueError("feature-set idempotency key has conflicting identity")
            return existing
        run = self.session.get(DailyRun, run_id)
        if run is None:
            raise ValueError(f"unknown daily run: {run_id}")
        normalized_cutoff = _require_aware(cutoff_at, field_name="cutoff_at")
        if run.prediction_date != prediction_date:
            raise ValueError("feature-set prediction date differs from its daily run")
        if run.cutoff_at is not None and not _same_instant(
            run.cutoff_at, normalized_cutoff
        ):
            raise ValueError("feature-set cutoff differs from its daily run")
        if set_kind not in {"MORNING", "WALK_FORWARD"}:
            raise ValueError(f"invalid feature-set kind: {set_kind}")
        if training_start > training_end or training_end >= prediction_date:
            raise ValueError("training dates must precede the prediction date")
        if required_feature_count < 0:
            raise ValueError("required_feature_count cannot be negative")
        digest = _require_hash(config_hash, field_name="config_hash")
        logical_existing = self.session.scalar(
            select(FeatureSet).where(
                FeatureSet.run_id == run_id,
                FeatureSet.ticker == ticker,
                FeatureSet.feature_version == feature_version,
                FeatureSet.set_kind == set_kind,
            )
        )
        if logical_existing is not None:
            raise ValueError("feature set already exists under another idempotency key")
        feature_set = FeatureSet(
            feature_set_id=str(uuid4()),
            run_id=run_id,
            ticker=ticker,
            prediction_date=prediction_date,
            cutoff_at=normalized_cutoff,
            feature_version=feature_version,
            set_kind=set_kind,
            training_start=training_start,
            training_end=training_end,
            config_hash=digest,
            status="BUILDING",
            required_feature_count=required_feature_count,
            missing_feature_count=required_feature_count,
            missing_ratio=1.0 if required_feature_count else 0.0,
            created_at=datetime.now(UTC),
            details=dict(details or {}),
            idempotency_key=idempotency_key,
        )
        self.session.add(feature_set)
        self.session.flush()
        return feature_set

    def add_feature_value(
        self,
        *,
        feature_set_id: str,
        sample_date: date,
        sample_cutoff_at: datetime,
        row_role: str,
        value_kind: str,
        feature_name: str,
        value: Decimal | None,
        is_missing: bool,
        available_timestamp: datetime | None = None,
        data_quality: str | None = None,
        details: Mapping[str, object] | None = None,
        flush: bool = True,
    ) -> FeatureValue:
        """Store a feature/target cell after validating its sample-time boundary."""

        feature_set = self._cached_get(FeatureSet, feature_set_id)
        if feature_set is None:
            raise ValueError(f"unknown feature set: {feature_set_id}")
        if feature_set.status != "BUILDING":
            raise ValueError("feature values can only be added while BUILDING")
        if row_role not in {"TRAIN", "SCORE"}:
            raise ValueError(f"invalid row role: {row_role}")
        if value_kind not in {"FEATURE", "TARGET"}:
            raise ValueError(f"invalid value kind: {value_kind}")
        if not feature_name.strip():
            raise ValueError("feature_name must not be blank")
        if is_missing != (value is None):
            raise ValueError("is_missing must exactly match a null value")
        normalized_sample_cutoff = _require_aware(
            sample_cutoff_at, field_name="sample_cutoff_at"
        )
        if normalized_sample_cutoff > _as_utc(feature_set.cutoff_at):
            raise ValueError("sample cutoff cannot exceed feature-set cutoff")
        if row_role == "SCORE":
            if sample_date != feature_set.prediction_date:
                raise ValueError("score rows must use the prediction date")
            if value_kind == "TARGET":
                raise ValueError("a score-row target would leak the outcome")
        elif not feature_set.training_start <= sample_date <= feature_set.training_end:
            raise ValueError("training row falls outside the frozen training interval")
        normalized_available: datetime | None = None
        if available_timestamp is not None:
            normalized_available = _require_aware(
                available_timestamp, field_name="available_timestamp"
            )
            if normalized_available > normalized_sample_cutoff:
                raise ValueError("feature value was unavailable at its sample cutoff")
        # A feature set writes tens of thousands of these. Asking the database
        # whether each one exists costs a round trip apiece, which is what made
        # a morning's persistence take hours against a hosted database; the set
        # is loaded once and answered from memory instead. Rows added in this
        # session are registered below, so the cache stays authoritative
        # without another query.
        cache = self._feature_value_cache(feature_set_id)
        key = (sample_date, row_role, value_kind, feature_name)
        existing = cache.get(key)
        if existing is not None:
            identity = (existing.value, existing.is_missing, existing.data_quality)
            if identity != (value, is_missing, data_quality):
                raise ValueError("feature value is immutable within a feature set")
            return existing
        feature_value = FeatureValue(
            feature_set_id=feature_set_id,
            sample_date=sample_date,
            sample_cutoff_at=normalized_sample_cutoff,
            row_role=row_role,
            value_kind=value_kind,
            feature_name=feature_name,
            value=value,
            is_missing=is_missing,
            available_timestamp=normalized_available,
            data_quality=data_quality,
            details=dict(details or {}),
            created_at=datetime.now(UTC),
        )
        self.session.add(feature_value)
        # Existing callers receive the old immediate-ID behavior. The
        # production persistence service stages a complete feature set and
        # flushes it once, reducing thousands of hosted-DB round trips to one.
        if flush:
            self.session.flush()
        cache[key] = feature_value
        return feature_value

    def _feature_value_cache(
        self, feature_set_id: str
    ) -> dict[tuple[date, str, str, str], FeatureValue]:
        """Load one feature set's existing rows once, keyed for lookup."""

        cached = self._feature_value_caches.get(feature_set_id)
        if cached is None:
            cached = {
                (row.sample_date, row.row_role, row.value_kind, row.feature_name): row
                for row in self.session.scalars(
                    select(FeatureValue).where(
                        FeatureValue.feature_set_id == feature_set_id
                    )
                )
            }
            self._feature_value_caches[feature_set_id] = cached
        return cached

    def add_feature_input(
        self,
        *,
        feature_value_id: int,
        input_role: str,
        source_type: str,
        source_row_id: int,
        flush: bool = True,
        observed_by_cutoff: bool = True,
    ) -> FeatureInput:
        """Attach the exact raw revision and reject any look-ahead evidence.

        ``observed_by_cutoff`` is the liveness claim, not the look-ahead one.
        A replayed session sets it False: only data available in the market by
        the cutoff is used, but the evidence that this system had fetched it by
        then does not exist and must not be asserted.
        """

        feature_value = self._cached_get(FeatureValue, feature_value_id)
        if feature_value is None:
            raise ValueError(f"unknown feature value: {feature_value_id}")
        feature_set = self._cached_get(FeatureSet, feature_value.feature_set_id)
        if feature_set is None:
            raise ValueError("feature value has no feature set")
        if feature_set.status != "BUILDING":
            raise ValueError("feature inputs can only be added while BUILDING")
        if feature_value.is_missing:
            raise ValueError("a missing feature value cannot have raw inputs")
        raw_row: MarketData | StockPrice | None
        if source_type == "MARKET_DATA":
            raw_row = self._cached_get(MarketData, source_row_id)
            market_data_id = source_row_id
            stock_price_id = None
        elif source_type == "STOCK_PRICE":
            raw_row = self._cached_get(StockPrice, source_row_id)
            market_data_id = None
            stock_price_id = source_row_id
        else:
            raise ValueError(f"invalid feature-input source type: {source_type}")
        if raw_row is None:
            raise ValueError(f"unknown {source_type} row: {source_row_id}")
        sample_cutoff = _as_utc(feature_value.sample_cutoff_at)
        set_cutoff = _as_utc(feature_set.cutoff_at)
        available_at = _as_utc(raw_row.available_timestamp)
        observed_at = _as_utc(raw_row.first_observed_at)
        retrieved_at = _as_utc(raw_row.retrieved_at)
        # Look-ahead: the value must have existed in the market by the sample's
        # own cutoff. Never relaxed -- a violation here is a leak.
        if available_at > sample_cutoff:
            raise ValueError("raw input was unavailable at the sample cutoff")
        # Liveness: this system must also have *held* the value by then. A
        # replay of a past session cannot satisfy that, because the rows were
        # fetched afterwards, so a backfilled set records the weaker claim.
        if observed_by_cutoff and (
            observed_at > set_cutoff or retrieved_at > set_cutoff
        ):
            raise ValueError("raw input was not observed by the prediction cutoff")
        # The same liveness claim, tightened to the sample's own cutoff for the
        # scored row and for walk-forward sets. A replay cannot assert it either.
        if (
            observed_by_cutoff
            and (
                feature_set.set_kind == "WALK_FORWARD"
                or feature_value.row_role == "SCORE"
            )
            and (observed_at > sample_cutoff or retrieved_at > sample_cutoff)
        ):
            raise ValueError("raw input violates walk-forward first-observed cutoff")
        if feature_set.feature_set_id not in self._feature_inputs_loaded_for_sets:
            for stored_input in self.session.scalars(
                select(FeatureInput)
                .join(
                    FeatureValue,
                    FeatureValue.feature_value_id == FeatureInput.feature_value_id,
                )
                .where(FeatureValue.feature_set_id == feature_set.feature_set_id)
            ):
                stored_key = (
                    stored_input.feature_value_id,
                    stored_input.input_role,
                    stored_input.source_type,
                    stored_input.source_row_id,
                )
                self._feature_input_keys[stored_key] = stored_input
            self._feature_inputs_loaded_for_sets.add(feature_set.feature_set_id)
        lineage_key = (feature_value_id, input_role, source_type, source_row_id)
        existing_input = self._feature_input_keys.get(lineage_key)
        if existing_input is not None:
            return existing_input
        feature_input = FeatureInput(
            feature_value_id=feature_value_id,
            input_role=input_role,
            source_type=source_type,
            source_row_id=source_row_id,
            market_data_id=market_data_id,
            stock_price_id=stock_price_id,
            raw_hash=raw_row.raw_hash,
            available_timestamp=raw_row.available_timestamp,
            first_observed_at=raw_row.first_observed_at,
            retrieved_at=raw_row.retrieved_at,
            created_at=datetime.now(UTC),
        )
        if feature_value.available_timestamp is None or available_at > _as_utc(
            feature_value.available_timestamp
        ):
            feature_value.available_timestamp = raw_row.available_timestamp
        self.session.add(feature_input)
        self._feature_input_keys[lineage_key] = feature_input
        if flush:
            self.session.flush()
        return feature_input

    def finalize_feature_set(
        self,
        feature_set: FeatureSet,
        *,
        status: str,
        input_manifest_hash: str | None,
        details: Mapping[str, object] | None = None,
        finalized_at: datetime | None = None,
    ) -> FeatureSet:
        """Freeze counts and maximum input timestamps after all cells are stored."""

        if status not in self._FEATURE_TERMINAL:
            raise ValueError(f"invalid terminal feature-set status: {status}")
        if feature_set.status != "BUILDING":
            if feature_set.status == status:
                return feature_set
            raise ValueError(f"feature set is already terminal: {feature_set.status}")
        values = list(
            self.session.scalars(
                select(FeatureValue).where(
                    FeatureValue.feature_set_id == feature_set.feature_set_id
                )
            )
        )
        missing_count = sum(value.is_missing for value in values)
        if len(values) != feature_set.required_feature_count:
            raise ValueError(
                "persisted feature-value count does not match the frozen requirement"
            )
        if status == "READY" and input_manifest_hash is None:
            raise ValueError("ready feature set requires an input manifest hash")
        digest = (
            _require_hash(input_manifest_hash, field_name="input_manifest_hash")
            if input_manifest_hash is not None
            else None
        )
        max_available, max_observed, max_retrieved = cast(
            tuple[datetime | None, datetime | None, datetime | None],
            self.session.execute(
                select(
                    func.max(FeatureInput.available_timestamp),
                    func.max(FeatureInput.first_observed_at),
                    func.max(FeatureInput.retrieved_at),
                )
                .join(
                    FeatureValue,
                    FeatureValue.feature_value_id == FeatureInput.feature_value_id,
                )
                .where(FeatureValue.feature_set_id == feature_set.feature_set_id)
            ).one(),
        )
        cutoff = _as_utc(feature_set.cutoff_at)
        for label, timestamp in (
            ("available", max_available),
            ("first-observed", max_observed),
            ("retrieved", max_retrieved),
        ):
            if timestamp is not None and _as_utc(timestamp) > cutoff:
                raise ValueError(f"feature-set {label} lineage exceeds its cutoff")
        completed_at = _require_aware(
            finalized_at or datetime.now(UTC), field_name="finalized_at"
        )
        if completed_at < _as_utc(feature_set.created_at):
            raise ValueError("finalized_at cannot precede created_at")
        feature_set.status = status
        feature_set.missing_feature_count = missing_count
        feature_set.missing_ratio = missing_count / len(values) if values else 0.0
        feature_set.max_available_timestamp = max_available
        feature_set.max_first_observed_at = max_observed
        feature_set.max_retrieved_at = max_retrieved
        feature_set.input_manifest_hash = digest
        feature_set.finalized_at = completed_at
        if details is not None:
            feature_set.details = dict(details)
        self.session.flush()
        return feature_set

    def create_model_run(
        self,
        *,
        run_id: str,
        ticker: str,
        feature_set_id: str,
        task: str,
        algorithm: str,
        training_start: date,
        training_end: date,
        cutoff_at: datetime,
        training_rows: int,
        feature_version: str,
        model_version: str,
        random_seed: int,
        parameters: Mapping[str, object],
        cv_results: Mapping[str, object],
        idempotency_key: str,
        started_at: datetime | None = None,
    ) -> ModelRun:
        """Start a deterministic fit tied to one finalized feature matrix."""

        existing = self.session.scalar(
            select(ModelRun).where(ModelRun.idempotency_key == idempotency_key)
        )
        if existing is not None:
            identity = (
                existing.run_id,
                existing.ticker,
                existing.feature_set_id,
                existing.task,
                existing.algorithm,
            )
            if identity != (run_id, ticker, feature_set_id, task, algorithm):
                raise ValueError("model-run idempotency key has conflicting identity")
            return existing
        feature_set = self.session.get(FeatureSet, feature_set_id)
        if feature_set is None or feature_set.status != "READY":
            raise ValueError("model run requires a READY feature set")
        if run_id != feature_set.run_id or ticker != feature_set.ticker:
            raise ValueError("model run identity differs from its feature set")
        if task not in {"REGRESSION", "CLASSIFICATION"}:
            raise ValueError(f"invalid model task: {task}")
        if (training_start, training_end) != (
            feature_set.training_start,
            feature_set.training_end,
        ):
            raise ValueError("model training interval differs from its feature set")
        if feature_version != feature_set.feature_version:
            raise ValueError("model feature version differs from its feature set")
        normalized_cutoff = _require_aware(cutoff_at, field_name="cutoff_at")
        if not _same_instant(feature_set.cutoff_at, normalized_cutoff):
            raise ValueError("model cutoff differs from its feature set")
        if training_rows <= 0:
            raise ValueError("training_rows must be positive")
        logical_existing = self.session.scalar(
            select(ModelRun).where(
                ModelRun.run_id == run_id,
                ModelRun.ticker == ticker,
                ModelRun.task == task,
                ModelRun.algorithm == algorithm,
            )
        )
        if logical_existing is not None:
            raise ValueError("model run already exists under another idempotency key")
        model_run = ModelRun(
            model_run_id=str(uuid4()),
            run_id=run_id,
            ticker=ticker,
            feature_set_id=feature_set_id,
            task=task,
            algorithm=algorithm,
            training_start=training_start,
            training_end=training_end,
            cutoff_at=normalized_cutoff,
            training_rows=training_rows,
            feature_version=feature_version,
            model_version=model_version,
            random_seed=random_seed,
            parameters=dict(parameters),
            cv_results=dict(cv_results),
            status="RUNNING",
            started_at=_require_aware(
                started_at or datetime.now(UTC), field_name="started_at"
            ),
            idempotency_key=idempotency_key,
        )
        self.session.add(model_run)
        self.session.flush()
        return model_run

    def add_model_coefficient(
        self,
        *,
        model_run_id: str,
        feature_name: str,
        coefficient: Decimal,
        scaler_mean: Decimal | None,
        scaler_scale: Decimal | None,
        flush: bool = True,
    ) -> ModelCoefficient:
        """Persist one coefficient and the StandardScaler state needed to reuse it."""

        model_run = self._cached_get(ModelRun, model_run_id)
        if model_run is None:
            raise ValueError(f"unknown model run: {model_run_id}")
        if model_run.status != "RUNNING":
            raise ValueError("coefficients can only be added while a model is RUNNING")
        if scaler_scale is not None and scaler_scale <= 0:
            raise ValueError("scaler_scale must be positive")
        cache = self._model_coefficient_caches.get(model_run_id)
        if cache is None:
            cache = {
                row.feature_name: row
                for row in self.session.scalars(
                    select(ModelCoefficient).where(
                        ModelCoefficient.model_run_id == model_run_id
                    )
                )
            }
            self._model_coefficient_caches[model_run_id] = cache
        existing = cache.get(feature_name)
        if existing is not None:
            identity = (
                existing.coefficient,
                existing.scaler_mean,
                existing.scaler_scale,
            )
            if identity != (coefficient, scaler_mean, scaler_scale):
                raise ValueError("model coefficient is immutable")
            return existing
        row = ModelCoefficient(
            model_run_id=model_run_id,
            feature_name=feature_name,
            coefficient=coefficient,
            scaler_mean=scaler_mean,
            scaler_scale=scaler_scale,
            created_at=datetime.now(UTC),
        )
        self.session.add(row)
        cache[feature_name] = row
        if flush:
            self.session.flush()
        return row

    def finish_model_run(
        self,
        model_run: ModelRun,
        *,
        status: str,
        intercept: Decimal | None = None,
        artifact_uri: str | None = None,
        artifact_hash: str | None = None,
        error_message: str | None = None,
        finished_at: datetime | None = None,
    ) -> ModelRun:
        """Freeze a model fit and its reproducibility metadata."""

        if status not in self._MODEL_TERMINAL:
            raise ValueError(f"invalid terminal model status: {status}")
        if model_run.status != "RUNNING":
            if model_run.status == status:
                return model_run
            raise ValueError(f"model run is already terminal: {model_run.status}")
        if status == "SUCCESS" and intercept is None:
            raise ValueError("successful linear model requires an intercept")
        if status == "FAILED" and not error_message:
            raise ValueError("failed model run requires an error message")
        if artifact_hash is not None:
            artifact_hash = _require_hash(artifact_hash, field_name="artifact_hash")
        completed_at = _require_aware(
            finished_at or datetime.now(UTC), field_name="finished_at"
        )
        if completed_at < _as_utc(model_run.started_at):
            raise ValueError("finished_at cannot precede started_at")
        model_run.status = status
        model_run.intercept = intercept
        model_run.artifact_uri = artifact_uri
        model_run.artifact_hash = artifact_hash
        model_run.error_message = error_message
        model_run.finished_at = completed_at
        self.session.flush()
        return model_run

    def create_prediction_set(
        self,
        *,
        run_id: str,
        prediction_date: date,
        cutoff_at: datetime,
        feature_version: str,
        model_version: str,
        strategy_version: str,
        training_start: date,
        training_end: date,
        idempotency_key: str,
        warnings: list[str] | None = None,
        generated_at: datetime | None = None,
    ) -> PredictionSet:
        """Create the unpublished unit that an email and dashboard will consume."""

        existing = self.session.scalar(
            select(PredictionSet).where(
                PredictionSet.idempotency_key == idempotency_key
            )
        )
        if existing is not None:
            if existing.prediction_date != prediction_date:
                raise ValueError(
                    "prediction-set idempotency key has conflicting identity"
                )
            return existing
        run = self.session.get(DailyRun, run_id)
        if run is None:
            raise ValueError(f"unknown daily run: {run_id}")
        normalized_cutoff = _require_aware(cutoff_at, field_name="cutoff_at")
        if run.prediction_date != prediction_date:
            raise ValueError("prediction-set date differs from its daily run")
        if run.cutoff_at is not None and not _same_instant(
            run.cutoff_at, normalized_cutoff
        ):
            raise ValueError("prediction-set cutoff differs from its daily run")
        if training_start > training_end or training_end >= prediction_date:
            raise ValueError("training dates must precede the prediction date")
        if (
            self.session.scalar(
                select(PredictionSet).where(PredictionSet.run_id == run_id)
            )
            is not None
        ):
            raise ValueError(
                "prediction set already exists under another idempotency key"
            )
        row = PredictionSet(
            prediction_set_id=str(uuid4()),
            run_id=run_id,
            prediction_date=prediction_date,
            cutoff_at=normalized_cutoff,
            status="BUILDING",
            feature_version=feature_version,
            model_version=model_version,
            strategy_version=strategy_version,
            training_start=training_start,
            training_end=training_end,
            generated_at=_require_aware(
                generated_at or datetime.now(UTC), field_name="generated_at"
            ),
            warnings=sorted(set(warnings or [])),
            idempotency_key=idempotency_key,
        )
        self.session.add(row)
        self.session.flush()
        return row

    def add_prediction(
        self,
        *,
        prediction_set_id: str,
        ticker: str,
        feature_set_id: str,
        regression_model_run_id: str | None,
        classification_model_run_id: str | None,
        status: str,
        predicted_intraday_return: Decimal | None,
        probability_up: Decimal | None,
        reference_stock_price_id: int | None,
        reference_price: Decimal | None,
        reference_basis: str,
        predicted_price_difference: Decimal | None,
        predicted_close: Decimal | None,
        signal: str,
        rank: int | None,
        return_threshold: Decimal,
        probability_threshold: Decimal,
        confidence_score: Decimal | None,
        idempotency_key: str,
        warnings: list[str] | None = None,
        prediction_interval_low: Decimal | None = None,
        prediction_interval_high: Decimal | None = None,
        positive_factors: list[str] | None = None,
        negative_factors: list[str] | None = None,
        feature_coverage: float | None = None,
    ) -> Prediction:
        """Persist one stock prediction after validating all upstream identities."""

        existing = self.session.scalar(
            select(Prediction).where(Prediction.idempotency_key == idempotency_key)
        )
        if existing is not None:
            if (existing.prediction_set_id, existing.ticker) != (
                prediction_set_id,
                ticker,
            ):
                raise ValueError("prediction idempotency key has conflicting identity")
            return existing
        prediction_set = self.session.get(PredictionSet, prediction_set_id)
        if prediction_set is None or prediction_set.status != "BUILDING":
            raise ValueError("predictions require a BUILDING prediction set")
        feature_set = self.session.get(FeatureSet, feature_set_id)
        if feature_set is None:
            raise ValueError("prediction requires a feature set")
        if ticker != feature_set.ticker:
            raise ValueError("prediction ticker differs from its feature set")
        if feature_set.prediction_date != prediction_set.prediction_date:
            raise ValueError("prediction and feature-set dates differ")
        if feature_set.feature_version != prediction_set.feature_version:
            raise ValueError("prediction and feature-set versions differ")
        if _as_utc(feature_set.cutoff_at) > _as_utc(prediction_set.cutoff_at):
            raise ValueError("feature-set cutoff exceeds the prediction cutoff")
        if status not in {"SUCCESS", "INSUFFICIENT_DATA", "FAILED"}:
            raise ValueError(f"invalid prediction status: {status}")
        if status == "SUCCESS" and feature_set.status != "READY":
            raise ValueError("successful prediction requires a READY feature set")
        if status != "SUCCESS" and feature_set.status not in {
            "READY",
            "INSUFFICIENT_DATA",
            "FAILED",
        }:
            raise ValueError(
                "non-successful prediction requires a terminal feature set"
            )
        if signal not in {"BUY", "NO_BUY", "NONE"}:
            raise ValueError(f"invalid prediction signal: {signal}")
        if (prediction_interval_low is None) != (prediction_interval_high is None):
            raise ValueError("prediction interval bounds must both be present or null")
        if (
            prediction_interval_low is not None
            and prediction_interval_high is not None
            and prediction_interval_low > prediction_interval_high
        ):
            raise ValueError("prediction interval low cannot exceed high")
        if feature_coverage is not None and not 0.0 <= feature_coverage <= 1.0:
            raise ValueError("feature_coverage must be between zero and one")
        if status == "SUCCESS":
            if None in (
                regression_model_run_id,
                classification_model_run_id,
                predicted_intraday_return,
                probability_up,
            ):
                raise ValueError("successful prediction requires both model outputs")
            if signal == "NONE":
                raise ValueError("successful prediction requires a trading decision")
        elif signal != "NONE":
            raise ValueError("non-successful prediction must use signal NONE")
        self._validate_prediction_model(
            model_run_id=regression_model_run_id,
            task="REGRESSION",
            ticker=ticker,
            feature_set_id=feature_set_id,
            model_version=prediction_set.model_version,
        )
        self._validate_prediction_model(
            model_run_id=classification_model_run_id,
            task="CLASSIFICATION",
            ticker=ticker,
            feature_set_id=feature_set_id,
            model_version=prediction_set.model_version,
        )
        if reference_stock_price_id is not None:
            reference_row = self.session.get(StockPrice, reference_stock_price_id)
            if reference_row is None:
                raise ValueError("unknown reference stock-price revision")
            cutoff = _as_utc(prediction_set.cutoff_at)
            if (
                _as_utc(reference_row.available_timestamp) > cutoff
                or _as_utc(reference_row.first_observed_at) > cutoff
                or _as_utc(reference_row.retrieved_at) > cutoff
            ):
                raise ValueError("reference stock price was not known by the cutoff")
            if reference_price != reference_row.close:
                raise ValueError("reference price differs from its raw revision")
        logical_existing = self.session.scalar(
            select(Prediction).where(
                Prediction.prediction_set_id == prediction_set_id,
                Prediction.ticker == ticker,
            )
        )
        if logical_existing is not None:
            raise ValueError("prediction exists under another idempotency key")
        prediction = Prediction(
            prediction_id=str(uuid4()),
            prediction_set_id=prediction_set_id,
            ticker=ticker,
            feature_set_id=feature_set_id,
            reference_stock_price_id=reference_stock_price_id,
            regression_model_run_id=regression_model_run_id,
            classification_model_run_id=classification_model_run_id,
            status=status,
            predicted_intraday_return=predicted_intraday_return,
            prediction_interval_low=prediction_interval_low,
            prediction_interval_high=prediction_interval_high,
            probability_up=probability_up,
            reference_price=reference_price,
            reference_basis=reference_basis,
            predicted_price_difference=predicted_price_difference,
            predicted_close=predicted_close,
            signal=signal,
            rank=rank,
            return_threshold=return_threshold,
            probability_threshold=probability_threshold,
            confidence_score=confidence_score,
            positive_factors=list(positive_factors or []),
            negative_factors=list(negative_factors or []),
            feature_coverage=feature_coverage,
            warnings=sorted(set(warnings or [])),
            created_at=datetime.now(UTC),
            idempotency_key=idempotency_key,
        )
        self.session.add(prediction)
        self.session.flush()
        return prediction

    def _validate_prediction_model(
        self,
        *,
        model_run_id: str | None,
        task: str,
        ticker: str,
        feature_set_id: str,
        model_version: str,
    ) -> None:
        if model_run_id is None:
            return
        model_run = self.session.get(ModelRun, model_run_id)
        if model_run is None or model_run.status != "SUCCESS":
            raise ValueError(f"prediction requires a successful {task.lower()} model")
        if (
            model_run.task != task
            or model_run.ticker != ticker
            or model_run.feature_set_id != feature_set_id
            or model_run.model_version != model_version
        ):
            raise ValueError(f"{task.lower()} model identity differs from prediction")

    def finalize_prediction_set(
        self,
        prediction_set: PredictionSet,
        *,
        status: str,
        expected_tickers: set[str] | None = None,
        published_at: datetime | None = None,
    ) -> PredictionSet:
        """Atomically mark the prediction collection ready for readers."""

        if status not in self._PREDICTION_SET_TERMINAL:
            raise ValueError(f"invalid terminal prediction-set status: {status}")
        if prediction_set.status != "BUILDING":
            if prediction_set.status == status:
                return prediction_set
            raise ValueError(
                f"prediction set is already terminal: {prediction_set.status}"
            )
        actual_tickers = set(
            self.session.scalars(
                select(Prediction.ticker).where(
                    Prediction.prediction_set_id == prediction_set.prediction_set_id
                )
            )
        )
        if expected_tickers is not None and actual_tickers != expected_tickers:
            raise ValueError("persisted predictions do not match expected tickers")
        if status == "READY" and not actual_tickers:
            raise ValueError("ready prediction set cannot be empty")
        completed_at = _require_aware(
            published_at or datetime.now(UTC), field_name="published_at"
        )
        if completed_at < _as_utc(prediction_set.generated_at):
            raise ValueError("published_at cannot precede generated_at")
        prediction_set.status = status
        prediction_set.published_at = completed_at
        self.session.flush()
        return prediction_set

    def save_actual_result(
        self,
        *,
        prediction_id: str,
        stock_price_id: int | None,
        supersedes_actual_result_id: str | None,
        result_version: int,
        status: str,
        actual_open: Decimal | None,
        actual_close: Decimal | None,
        observed_at: datetime | None,
        finalized_at: datetime | None,
        idempotency_key: str,
    ) -> ActualResult:
        """Append one outcome revision and derive return/P&L inputs consistently."""

        existing = self.session.scalar(
            select(ActualResult).where(ActualResult.idempotency_key == idempotency_key)
        )
        if existing is not None:
            if (existing.prediction_id, existing.result_version) != (
                prediction_id,
                result_version,
            ):
                raise ValueError(
                    "actual-result idempotency key has conflicting identity"
                )
            return existing
        prediction = self.session.get(Prediction, prediction_id)
        if prediction is None:
            raise ValueError(f"unknown prediction: {prediction_id}")
        if result_version <= 0:
            raise ValueError("result_version must be positive")
        if status not in {"PENDING", "FINAL", "CORRECTED"}:
            raise ValueError(f"invalid actual-result status: {status}")
        if supersedes_actual_result_id is None:
            if result_version != 1:
                raise ValueError("the first actual-result revision must be version 1")
        else:
            prior = self.session.get(ActualResult, supersedes_actual_result_id)
            if prior is None or prior.prediction_id != prediction_id:
                raise ValueError("superseded result must belong to this prediction")
            if result_version != prior.result_version + 1:
                raise ValueError("actual-result versions must be contiguous")
        stock_row: StockPrice | None = None
        raw_hash: str | None = None
        if stock_price_id is not None:
            stock_row = self.session.get(StockPrice, stock_price_id)
            if stock_row is None or stock_row.canonical_symbol != prediction.ticker:
                raise ValueError("actual-result stock revision has the wrong ticker")
            raw_hash = stock_row.raw_hash
            if actual_open is not None and actual_open != stock_row.open:
                raise ValueError("actual open differs from its raw stock revision")
            if actual_close is not None and actual_close != stock_row.close:
                raise ValueError("actual close differs from its raw stock revision")
        normalized_observed = (
            _require_aware(observed_at, field_name="observed_at")
            if observed_at is not None
            else None
        )
        normalized_finalized = (
            _require_aware(finalized_at, field_name="finalized_at")
            if finalized_at is not None
            else None
        )
        actual_return: Decimal | None = None
        price_difference: Decimal | None = None
        if status == "PENDING":
            if actual_close is not None or normalized_finalized is not None:
                raise ValueError("pending outcome cannot contain final close values")
        else:
            if (
                actual_open is None
                or actual_close is None
                or actual_open <= 0
                or normalized_finalized is None
            ):
                raise ValueError(
                    "final outcome requires positive open, close, and time"
                )
            price_difference = actual_close - actual_open
            actual_return = price_difference / actual_open
        row = ActualResult(
            actual_result_id=str(uuid4()),
            prediction_id=prediction_id,
            stock_price_id=stock_price_id,
            supersedes_actual_result_id=supersedes_actual_result_id,
            result_version=result_version,
            status=status,
            actual_open=actual_open,
            actual_close=actual_close,
            actual_intraday_return=actual_return,
            actual_price_difference=price_difference,
            raw_hash=raw_hash,
            observed_at=normalized_observed,
            finalized_at=normalized_finalized,
            created_at=datetime.now(UTC),
            idempotency_key=idempotency_key,
        )
        self.session.add(row)
        self.session.flush()
        return row

    def save_simulated_trade(
        self,
        *,
        prediction_id: str,
        actual_result_id: str | None,
        status: str,
        capital_jpy: Decimal,
        shares: int,
        entry_price: Decimal | None,
        exit_price: Decimal | None,
        gross_profit_jpy: Decimal | None,
        commission_cost_jpy: Decimal | None,
        slippage_cost_jpy: Decimal | None,
        net_profit_jpy: Decimal | None,
        realized_return: Decimal | None,
        opened_at: datetime | None,
        closed_at: datetime | None,
        strategy_version: str,
        idempotency_key: str,
    ) -> SimulatedTrade:
        """Persist an explicitly simulated trade without inventing pending costs."""

        existing = self.session.scalar(
            select(SimulatedTrade).where(
                SimulatedTrade.idempotency_key == idempotency_key
            )
        )
        if existing is not None:
            if existing.prediction_id != prediction_id:
                raise ValueError("trade idempotency key has conflicting identity")
            return existing
        prediction = self.session.get(Prediction, prediction_id)
        if prediction is None:
            raise ValueError(f"unknown prediction: {prediction_id}")
        allowed_statuses = {
            "NOT_TRIGGERED",
            "PENDING",
            "FINAL",
            "INSUFFICIENT_CONFIG",
        }
        if status not in allowed_statuses:
            raise ValueError(f"invalid simulated-trade status: {status}")
        if capital_jpy <= 0 or shares < 0:
            raise ValueError("capital must be positive and shares non-negative")
        actual_result: ActualResult | None = None
        if actual_result_id is not None:
            actual_result = self.session.get(ActualResult, actual_result_id)
            if actual_result is None or actual_result.prediction_id != prediction_id:
                raise ValueError("trade result must belong to its prediction")
        if status == "FINAL":
            required_values = (
                actual_result,
                entry_price,
                exit_price,
                gross_profit_jpy,
                commission_cost_jpy,
                slippage_cost_jpy,
                net_profit_jpy,
                realized_return,
                opened_at,
                closed_at,
            )
            if any(value is None for value in required_values):
                raise ValueError(
                    "final trade requires a result, prices, costs, and times"
                )
        if status == "INSUFFICIENT_CONFIG" and any(
            value is not None
            for value in (
                commission_cost_jpy,
                slippage_cost_jpy,
                net_profit_jpy,
                realized_return,
            )
        ):
            raise ValueError("unconfirmed trading costs must remain null")
        normalized_opened = (
            _require_aware(opened_at, field_name="opened_at")
            if opened_at is not None
            else None
        )
        normalized_closed = (
            _require_aware(closed_at, field_name="closed_at")
            if closed_at is not None
            else None
        )
        if (
            normalized_opened is not None
            and normalized_closed is not None
            and normalized_closed < normalized_opened
        ):
            raise ValueError("closed_at cannot precede opened_at")
        row = SimulatedTrade(
            trade_id=str(uuid4()),
            prediction_id=prediction_id,
            actual_result_id=actual_result_id,
            status=status,
            is_simulated=True,
            capital_jpy=capital_jpy,
            shares=shares,
            entry_price=entry_price,
            exit_price=exit_price,
            gross_profit_jpy=gross_profit_jpy,
            commission_cost_jpy=commission_cost_jpy,
            slippage_cost_jpy=slippage_cost_jpy,
            net_profit_jpy=net_profit_jpy,
            realized_return=realized_return,
            opened_at=normalized_opened,
            closed_at=normalized_closed,
            strategy_version=strategy_version,
            created_at=datetime.now(UTC),
            idempotency_key=idempotency_key,
        )
        self.session.add(row)
        self.session.flush()
        return row

    _METRIC_VALUE_FIELDS = frozenset(
        {
            "win_rate",
            "gross_profit_jpy",
            "gross_loss_jpy",
            "net_profit_jpy",
            "average_win_jpy",
            "average_loss_jpy",
            "largest_win_jpy",
            "largest_loss_jpy",
            "payoff_ratio",
            "profit_factor",
            "expectancy_jpy",
            "sharpe_ratio",
            "sortino_ratio",
            "max_drawdown",
            "pearson_correlation",
            "spearman_correlation",
            "direction_accuracy",
            "readability_score",
        }
    )

    def save_metric_snapshot(
        self,
        *,
        ticker: str,
        as_of_date: date,
        model_version: str,
        strategy_version: str,
        evaluation_window: str,
        status: str,
        sample_status: str,
        prediction_count: int,
        trade_count: int,
        win_count: int,
        loss_count: int,
        metrics: Mapping[str, Decimal | None],
        input_manifest_hash: str,
        idempotency_key: str,
        details: Mapping[str, object] | None = None,
        computed_at: datetime | None = None,
    ) -> MetricSnapshot:
        """Store a versioned dashboard metric projection with input-manifest hash."""

        existing = self.session.scalar(
            select(MetricSnapshot).where(
                MetricSnapshot.idempotency_key == idempotency_key
            )
        )
        if existing is not None:
            identity = (
                existing.ticker,
                existing.as_of_date,
                existing.model_version,
                existing.strategy_version,
                existing.evaluation_window,
            )
            requested = (
                ticker,
                as_of_date,
                model_version,
                strategy_version,
                evaluation_window,
            )
            if identity != requested:
                raise ValueError("metric idempotency key has conflicting identity")
            return existing
        if status not in {"READY", "INSUFFICIENT_DATA", "FAILED"}:
            raise ValueError(f"invalid metric status: {status}")
        if sample_status not in {"NO_TRADES", "LOW_SAMPLE", "SUFFICIENT"}:
            raise ValueError(f"invalid metric sample status: {sample_status}")
        if min(prediction_count, trade_count, win_count, loss_count) < 0:
            raise ValueError("metric counts cannot be negative")
        if win_count + loss_count > trade_count:
            raise ValueError("wins and losses cannot exceed trade count")
        unknown_metrics = set(metrics) - self._METRIC_VALUE_FIELDS
        if unknown_metrics:
            raise ValueError(f"unknown metric fields: {sorted(unknown_metrics)}")
        values: dict[str, Any] = {
            field_name: metrics.get(field_name)
            for field_name in self._METRIC_VALUE_FIELDS
        }
        row = MetricSnapshot(
            metric_snapshot_id=str(uuid4()),
            ticker=ticker,
            as_of_date=as_of_date,
            model_version=model_version,
            strategy_version=strategy_version,
            evaluation_window=evaluation_window,
            status=status,
            sample_status=sample_status,
            prediction_count=prediction_count,
            trade_count=trade_count,
            win_count=win_count,
            loss_count=loss_count,
            input_manifest_hash=_require_hash(
                input_manifest_hash, field_name="input_manifest_hash"
            ),
            details=dict(details or {}),
            computed_at=_require_aware(
                computed_at or datetime.now(UTC), field_name="computed_at"
            ),
            idempotency_key=idempotency_key,
            **values,
        )
        self.session.add(row)
        self.session.flush()
        return row

    def create_email_log(
        self,
        *,
        prediction_set_id: str,
        recipient: str,
        template_version: str,
        subject: str,
        idempotency_key: str,
    ) -> EmailLog:
        """Register one logical delivery before any provider side effect."""

        existing = self.session.scalar(
            select(EmailLog).where(EmailLog.idempotency_key == idempotency_key)
        )
        if existing is not None:
            identity = (
                existing.prediction_set_id,
                existing.recipient,
                existing.template_version,
            )
            if identity != (prediction_set_id, recipient, template_version):
                raise ValueError("email idempotency key has conflicting identity")
            return existing
        prediction_set = self.session.get(PredictionSet, prediction_set_id)
        if prediction_set is None or prediction_set.status not in {
            "READY",
            "INSUFFICIENT_DATA",
        }:
            raise ValueError("email delivery requires a publishable prediction set")
        logical_existing = self.session.scalar(
            select(EmailLog).where(
                EmailLog.prediction_set_id == prediction_set_id,
                EmailLog.recipient == recipient,
                EmailLog.template_version == template_version,
            )
        )
        if logical_existing is not None:
            raise ValueError("email delivery exists under another idempotency key")
        row = EmailLog(
            email_log_id=str(uuid4()),
            prediction_set_id=prediction_set_id,
            recipient=recipient,
            template_version=template_version,
            subject=subject,
            status="PENDING",
            attempt_count=0,
            created_at=datetime.now(UTC),
            idempotency_key=idempotency_key,
        )
        self.session.add(row)
        self.session.flush()
        return row

    def claim_email(self, idempotency_key: str) -> bool:
        """Atomically claim a delivery.

        The caller must commit this claim before performing the SMTP side effect.
        """

        result = cast(
            CursorResult[Any],
            self.session.execute(
                update(EmailLog)
                .where(
                    EmailLog.idempotency_key == idempotency_key,
                    EmailLog.status.in_(("PENDING", "FAILED")),
                )
                .values(
                    status="SENDING",
                    attempt_count=EmailLog.attempt_count + 1,
                    last_error=None,
                )
                .execution_options(synchronize_session="fetch")
            ),
        )
        if result.rowcount == 1:
            self.session.flush()
            return True
        email = self.session.scalar(
            select(EmailLog).where(EmailLog.idempotency_key == idempotency_key)
        )
        if email is None:
            raise ValueError("email must be registered before it can be claimed")
        if email.status in {"SENDING", "SENT"}:
            return False
        raise ValueError(f"email cannot be claimed from status {email.status}")

    def mark_email_sent(
        self,
        idempotency_key: str,
        *,
        provider_message_id: str,
        sent_at: datetime,
    ) -> EmailLog:
        """Finalize a claimed delivery with sanitized provider metadata."""

        email = self._email_by_key(idempotency_key)
        if email.status == "SENT":
            if email.provider_message_id != provider_message_id:
                raise ValueError("sent email has conflicting provider message id")
            return email
        if email.status != "SENDING":
            raise ValueError("only a claimed email can be marked sent")
        normalized_sent_at = _require_aware(sent_at, field_name="sent_at")
        if normalized_sent_at < _as_utc(email.created_at):
            raise ValueError("sent_at cannot precede email creation")
        email.status = "SENT"
        email.provider_message_id = provider_message_id
        email.sent_at = normalized_sent_at
        email.last_error = None
        self.session.flush()
        return email

    def mark_email_failed(self, idempotency_key: str, *, error: str) -> EmailLog:
        """Return a claimed delivery to a retryable FAILED state."""

        email = self._email_by_key(idempotency_key)
        if email.status != "SENDING":
            raise ValueError("only a claimed email can be marked failed")
        if not error:
            raise ValueError("failed email requires a sanitized error")
        email.status = "FAILED"
        email.last_error = error
        self.session.flush()
        return email

    def _email_by_key(self, idempotency_key: str) -> EmailLog:
        email = self.session.scalar(
            select(EmailLog).where(EmailLog.idempotency_key == idempotency_key)
        )
        if email is None:
            raise ValueError(f"unknown email idempotency key: {idempotency_key}")
        return email
