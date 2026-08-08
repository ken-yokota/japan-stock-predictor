"""Ridge regression pipeline factory."""

from __future__ import annotations

from sklearn.impute import SimpleImputer  # type: ignore[import-untyped]
from sklearn.linear_model import Ridge  # type: ignore[import-untyped]
from sklearn.pipeline import Pipeline  # type: ignore[import-untyped]
from sklearn.preprocessing import StandardScaler  # type: ignore[import-untyped]


def build_ridge_pipeline(alpha: float) -> Pipeline:
    """Build a leakage-safe pipeline fitted only on a caller's training fold."""

    if alpha <= 0.0:
        raise ValueError("alpha must be positive")
    return Pipeline(
        steps=[
            ("imputer", SimpleImputer(strategy="median", keep_empty_features=True)),
            ("scaler", StandardScaler()),
            ("model", Ridge(alpha=alpha)),
        ]
    )
