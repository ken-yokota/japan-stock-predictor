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

from database.models import FeatureInput, FeatureSet, FeatureValue, PredictionSet

DEFAULT_KEEP_DATES = 2


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
        dates = list(
            session.scalars(
                select(PredictionSet.prediction_date)
                .distinct()
                .order_by(func.max(PredictionSet.generated_at).desc())
                .group_by(PredictionSet.prediction_date)
                .limit(keep_dates + 1)
            )
        )
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
