"""Public feature-engineering API."""

from features.builder import (
    PRICE_FEATURE_COLUMNS,
    RETURN_WINDOWS,
    add_intraday_targets,
    build_price_features,
)
from features.domain import (
    FeatureLineage,
    PointInTimeFeatureSet,
    PointInTimeViolation,
    assert_point_in_time_safe,
)

__all__ = [
    "PRICE_FEATURE_COLUMNS",
    "RETURN_WINDOWS",
    "FeatureLineage",
    "PointInTimeFeatureSet",
    "PointInTimeViolation",
    "add_intraday_targets",
    "assert_point_in_time_safe",
    "build_price_features",
]
