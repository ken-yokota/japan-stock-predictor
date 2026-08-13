"""Bound how much feature history one hosted database has to hold.

A morning writes about 133 MB of feature cells and their lineage. The hosted
project stops writes at 512 MB, so three mornings fill it and the fourth dies
mid-transaction with DiskFull -- which is exactly how production spent its
first week never publishing anything.

Only the derived feature rows are pruned. Predictions, actuals, simulated
trades, model runs and coefficients are the track record and stay forever;
they are small. The raw market rows the features were computed from also stay,
so a pruned day can be rebuilt exactly if it is ever needed again.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from typing import Any, cast

from sqlalchemy import delete, func, select
from sqlalchemy.engine import CursorResult
from sqlalchemy.orm import Session, sessionmaker

from database.models import (
    DailyRun,
    FeatureInput,
    FeatureSet,
    FeatureValue,
    ModelCoefficient,
    ModelRun,
    PredictionSet,
)

DEFAULT_KEEP_DATES = 2

# Coefficients are the model's explanation of itself, so they outlive the
# cells they were fitted on. 8,156 rows a day is 549 MB a year against a
# 512 MB ceiling, though, so the window is finite - and the first run of
# each month is kept for good, which is what makes a year-long drift still
# visible after the daily rows age out.
DEFAULT_KEEP_COEFFICIENT_DATES = 90


def _rows(result: object) -> int:
    """Row count of a DELETE, narrowed for static analysis."""

    return max(int(cast(CursorResult[Any], result).rowcount or 0), 0)


@dataclass(frozen=True, slots=True)
class PruneReport:
    """What one prune removed, for the run's warnings."""

    kept_dates: tuple[date, ...] = ()
    pruned_dates: tuple[date, ...] = ()
    feature_values: int = 0
    feature_inputs: int = 0

    @property
    def pruned(self) -> bool:
        return bool(self.pruned_dates)


def _feature_set_ids(session: Session, kept: Sequence[date]) -> list[str]:
    """Every feature set whose date is not one of the kept ones."""

    return list(
        session.scalars(
            select(FeatureSet.feature_set_id).where(
                FeatureSet.prediction_date.notin_(kept)
            )
        )
    )


@dataclass(frozen=True, slots=True)
class CoefficientPruneReport:
    """What one coefficient prune removed."""

    kept_dates: tuple[date, ...] = ()
    pruned_dates: tuple[date, ...] = ()
    coefficients: int = 0

    @property
    def pruned(self) -> bool:
        return bool(self.pruned_dates)


def _monthly_anchors(dates: Sequence[date]) -> set[date]:
    """The earliest date seen in each calendar month.

    These survive the window so that coefficient stability stays measurable
    over a year without keeping every day of it.
    """

    anchors: dict[tuple[int, int], date] = {}
    for day in dates:
        key = (day.year, day.month)
        if key not in anchors or day < anchors[key]:
            anchors[key] = day
    return set(anchors.values())


def prune_model_coefficients(
    factory: sessionmaker[Session],
    *,
    keep_dates: int = DEFAULT_KEEP_COEFFICIENT_DATES,
) -> CoefficientPruneReport:
    """Drop per-feature coefficients outside the window and the monthly anchors.

    Only the coefficient rows go. ``model_runs`` keeps its parameters, its
    intercept and its cross-validation results, so which model ran on a day and
    how it was fitted is still answerable after its per-feature rows age out.
    """

    if keep_dates < 1:
        raise ValueError("keep_dates must be at least 1")
    with factory() as session:
        dates = list(
            session.scalars(
                select(DailyRun.prediction_date)
                .join(ModelRun, ModelRun.run_id == DailyRun.run_id)
                .distinct()
                .order_by(DailyRun.prediction_date.desc())
            )
        )
        if len(dates) <= keep_dates:
            return CoefficientPruneReport(kept_dates=tuple(dates))

        kept = set(dates[:keep_dates]) | _monthly_anchors(dates)
        stale = [day for day in dates if day not in kept]
        if not stale:
            return CoefficientPruneReport(kept_dates=tuple(sorted(kept)))

        run_ids = list(
            session.scalars(
                select(ModelRun.model_run_id)
                .join(DailyRun, DailyRun.run_id == ModelRun.run_id)
                .where(DailyRun.prediction_date.in_(stale))
            )
        )
        removed = 0
        # Chunked for the same reason the feature prune is: a driver will
        # refuse an unbounded parameter list, and a long delete should not
        # hold every page at once.
        for offset in range(0, len(run_ids), 200):
            result = session.execute(
                delete(ModelCoefficient).where(
                    ModelCoefficient.model_run_id.in_(run_ids[offset : offset + 200])
                )
            )
            removed += _rows(result)
        session.commit()
        return CoefficientPruneReport(
            kept_dates=tuple(sorted(kept)),
            pruned_dates=tuple(sorted(stale)),
            coefficients=removed,
        )


def prune_feature_history(
    factory: sessionmaker[Session],
    *,
    keep_dates: int = DEFAULT_KEEP_DATES,
) -> PruneReport:
    """Delete feature cells and lineage outside the newest ``keep_dates`` days.

    Deleting rather than vacuuming is deliberate: the freed pages are reused by
    the next morning's insert, which is what keeps the project under its
    ceiling. Reclaiming them to the filesystem would need an exclusive lock the
    morning cannot afford.
    """

    if keep_dates < 1:
        raise ValueError("keep_dates must be at least 1")
    with factory() as session:
        # Ordered by when the set was *generated*, not by the session it names.
        # Keying off prediction_date breaks the moment a past day is replayed:
        # the replayed date sorts below the two most recent live days, nothing
        # is evicted, and the run writes its 133 MB straight into the ceiling.
        # Newest-generated is what actually tracks "the last N runs of work".
        # GROUP BY already yields one row per date, so DISTINCT is redundant --
        # and PostgreSQL rejects DISTINCT beside an ORDER BY on an aggregate that
        # is not in the select list. SQLite accepts it, which is precisely how
        # this shipped broken once.
        dates = [
            row[0]
            for row in session.execute(
                select(
                    PredictionSet.prediction_date,
                    func.max(PredictionSet.generated_at).label("latest"),
                )
                .group_by(PredictionSet.prediction_date)
                .order_by(func.max(PredictionSet.generated_at).desc())
                .limit(keep_dates + 1)
            )
        ]
        if len(dates) <= keep_dates:
            return PruneReport(kept_dates=tuple(dates))
        kept = tuple(dates[:keep_dates])

        stale = _feature_set_ids(session, kept)
        if not stale:
            return PruneReport(kept_dates=kept)

        pruned_dates = tuple(
            session.scalars(
                select(FeatureSet.prediction_date)
                .distinct()
                .where(FeatureSet.prediction_date.notin_(kept))
                .order_by(FeatureSet.prediction_date)
            )
        )
        inputs = 0
        values = 0
        # Chunked so one prune never builds a parameter list the driver
        # refuses, and so a long delete cannot hold every page at once.
        for offset in range(0, len(stale), 200):
            batch = stale[offset : offset + 200]
            value_ids = list(
                session.scalars(
                    select(FeatureValue.feature_value_id).where(
                        FeatureValue.feature_set_id.in_(batch)
                    )
                )
            )
            for inner in range(0, len(value_ids), 500):
                result = session.execute(
                    delete(FeatureInput).where(
                        FeatureInput.feature_value_id.in_(
                            value_ids[inner : inner + 500]
                        )
                    )
                )
                inputs += _rows(result)
            result = session.execute(
                delete(FeatureValue).where(FeatureValue.feature_set_id.in_(batch))
            )
            values += _rows(result)
        session.commit()
        return PruneReport(
            kept_dates=kept,
            pruned_dates=pruned_dates,
            feature_values=values,
            feature_inputs=inputs,
        )
