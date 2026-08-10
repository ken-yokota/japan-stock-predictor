"""Recover audit records left RUNNING after an external process kill."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, cast

from sqlalchemy import update
from sqlalchemy.engine import CursorResult, Result
from sqlalchemy.orm import Session, sessionmaker

from database.models import (
    DailyRun,
    FeatureSet,
    IngestionBatch,
    ModelRun,
    PredictionSet,
    RunStep,
)


@dataclass(frozen=True, slots=True)
class RecoveryReport:
    """Counts of stale rows moved to an explicit terminal state."""

    daily_runs: int = 0
    ingestion_batches: int = 0
    run_steps: int = 0
    feature_sets: int = 0
    model_runs: int = 0
    prediction_sets: int = 0

    @property
    def recovered(self) -> int:
        return sum(
            (
                self.daily_runs,
                self.ingestion_batches,
                self.run_steps,
                self.feature_sets,
                self.model_runs,
                self.prediction_sets,
            )
        )


def _count(result: Result[Any]) -> int:
    rowcount = cast(CursorResult[Any], result).rowcount
    return max(int(rowcount or 0), 0)


def reconcile_stale_runs(
    factory: sessionmaker[Session],
    *,
    now: datetime | None = None,
    stale_after: timedelta = timedelta(hours=2),
) -> RecoveryReport:
    """Fail stale in-progress rows without deleting their audit evidence.

    A workflow timeout sends an external kill, so application exception
    handlers never run.  The next invocation repairs only rows older than the
    concurrency guard; an actually running retry remains untouched.
    """

    observed_at = now or datetime.now(UTC)
    if observed_at.tzinfo is None or observed_at.utcoffset() is None:
        raise ValueError("now must be timezone-aware")
    if stale_after <= timedelta(0):
        raise ValueError("stale_after must be positive")
    observed_at = observed_at.astimezone(UTC)
    stale_before = observed_at - stale_after
    reason = "external process ended before recording a terminal state"

    with factory() as session:
        run_steps = _count(
            session.execute(
                update(RunStep)
                .where(
                    RunStep.status == "RUNNING",
                    RunStep.started_at <= stale_before,
                )
                .values(
                    status="FAILED",
                    finished_at=observed_at,
                    error_message=reason,
                )
            )
        )
        batches = _count(
            session.execute(
                update(IngestionBatch)
                .where(
                    IngestionBatch.status == "RUNNING",
                    IngestionBatch.started_at <= stale_before,
                )
                .values(status="FAILED", finished_at=observed_at)
            )
        )
        model_runs = _count(
            session.execute(
                update(ModelRun)
                .where(
                    ModelRun.status == "RUNNING",
                    ModelRun.started_at <= stale_before,
                )
                .values(
                    status="FAILED",
                    finished_at=observed_at,
                    error_message=reason,
                )
            )
        )
        feature_sets = _count(
            session.execute(
                update(FeatureSet)
                .where(
                    FeatureSet.status == "BUILDING",
                    FeatureSet.created_at <= stale_before,
                )
                .values(status="FAILED", finalized_at=observed_at)
            )
        )
        prediction_sets = _count(
            session.execute(
                update(PredictionSet)
                .where(
                    PredictionSet.status == "BUILDING",
                    PredictionSet.generated_at <= stale_before,
                )
                .values(status="FAILED")
            )
        )
        daily_runs = _count(
            session.execute(
                update(DailyRun)
                .where(
                    DailyRun.status == "RUNNING",
                    DailyRun.started_at <= stale_before,
                )
                .values(
                    status="FAILED",
                    current_step="FAILED",
                    finished_at=observed_at,
                    error_message=reason,
                )
            )
        )
        session.commit()

    return RecoveryReport(
        daily_runs=daily_runs,
        ingestion_batches=batches,
        run_steps=run_steps,
        feature_sets=feature_sets,
        model_runs=model_runs,
        prediction_sets=prediction_sets,
    )
