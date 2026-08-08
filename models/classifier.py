"""Logistic direction-classifier pipeline factory."""

from __future__ import annotations

from sklearn.impute import SimpleImputer  # type: ignore[import-untyped]
from sklearn.linear_model import LogisticRegression  # type: ignore[import-untyped]
from sklearn.pipeline import Pipeline  # type: ignore[import-untyped]
from sklearn.preprocessing import StandardScaler  # type: ignore[import-untyped]


def build_logistic_pipeline(c_value: float, *, random_state: int) -> Pipeline:
    """Build a deterministic, training-fold-only classification pipeline."""

    if c_value <= 0.0:
        raise ValueError("c_value must be positive")
    return Pipeline(
        steps=[
            ("imputer", SimpleImputer(strategy="median", keep_empty_features=True)),
            ("scaler", StandardScaler()),
            (
                "model",
                LogisticRegression(
                    C=c_value,
                    random_state=random_state,
                    solver="liblinear",
                    max_iter=2_000,
                ),
            ),
        ]
    )
