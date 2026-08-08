"""Point-in-time lineage contracts for model features."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime


def _require_aware(value: datetime, field_name: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} must be timezone-aware")


class PointInTimeViolation(ValueError):
    """Raised when a feature uses information unavailable at prediction time."""


@dataclass(frozen=True, slots=True)
class FeatureLineage:
    """Availability evidence for one source contribution to one feature.

    Multi-source features are represented by multiple records with the same
    ``feature_name``.  This keeps the assertion explicit: every contributing
    observation must have been available no later than the prediction cutoff.
    """

    feature_name: str
    source_symbol: str
    source_market_date: date
    available_timestamp: datetime
    prediction_timestamp: datetime

    def __post_init__(self) -> None:
        if not self.feature_name.strip():
            raise ValueError("feature_name must not be blank")
        if not self.source_symbol.strip():
            raise ValueError("source_symbol must not be blank")
        _require_aware(self.available_timestamp, "available_timestamp")
        _require_aware(self.prediction_timestamp, "prediction_timestamp")
        self.assert_available()

    def assert_available(self) -> None:
        """Assert that the source was available by the prediction cutoff."""

        if self.available_timestamp > self.prediction_timestamp:
            raise PointInTimeViolation(
                f"{self.feature_name!r} uses {self.source_symbol!r} at "
                f"{self.available_timestamp.isoformat()}, after prediction cutoff "
                f"{self.prediction_timestamp.isoformat()}"
            )


@dataclass(frozen=True, slots=True)
class PointInTimeFeatureSet:
    """Numeric feature values bundled with their auditable source lineage."""

    values: Mapping[str, float]
    lineage: tuple[FeatureLineage, ...]
    prediction_timestamp: datetime

    def __post_init__(self) -> None:
        _require_aware(self.prediction_timestamp, "prediction_timestamp")
        if not self.values:
            raise ValueError("values must not be empty")
        assert_point_in_time_safe(self.lineage, self.prediction_timestamp)
        covered = {item.feature_name for item in self.lineage}
        missing = set(self.values) - covered
        if missing:
            raise ValueError(f"features have no lineage: {sorted(missing)}")


def assert_point_in_time_safe(
    lineage: tuple[FeatureLineage, ...] | list[FeatureLineage],
    prediction_timestamp: datetime | None = None,
) -> None:
    """Reject lineage containing a source unavailable at prediction time.

    When ``prediction_timestamp`` is supplied, all records must refer to that
    exact cutoff.  This catches accidentally mixed prediction batches as well
    as ordinary look-ahead leakage.
    """

    if prediction_timestamp is not None:
        _require_aware(prediction_timestamp, "prediction_timestamp")
    for item in lineage:
        if (
            prediction_timestamp is not None
            and item.prediction_timestamp != prediction_timestamp
        ):
            raise PointInTimeViolation(
                "lineage prediction_timestamp does not match requested cutoff"
            )
        item.assert_available()
