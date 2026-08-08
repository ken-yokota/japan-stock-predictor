"""Benchmark and regularized linear regression pipeline factories.

These pipelines exist so ``config/model.yaml``'s ``regression_candidates``
(``ridge``, ``elastic_net``, ``ols``, ``lasso``) can all be fitted and compared
under the same preprocessing contract as the production Ridge model: the
imputer and scaler are pipeline steps, so they are fitted only inside whichever
training fold scikit-learn hands them.
"""

from __future__ import annotations

from sklearn.impute import SimpleImputer  # type: ignore[import-untyped]
from sklearn.linear_model import (  # type: ignore[import-untyped]
    ElasticNet,
    Lasso,
    LinearRegression,
)
from sklearn.pipeline import Pipeline  # type: ignore[import-untyped]
from sklearn.preprocessing import StandardScaler  # type: ignore[import-untyped]


def _preprocessing_steps() -> list[tuple[str, object]]:
    return [
        ("imputer", SimpleImputer(strategy="median", keep_empty_features=True)),
        ("scaler", StandardScaler()),
    ]


def build_elastic_net_pipeline(
    alpha: float,
    l1_ratio: float,
    *,
    random_state: int,
    max_iter: int = 10_000,
) -> Pipeline:
    """Build a deterministic ElasticNet pipeline for candidate comparison."""

    if alpha <= 0.0:
        raise ValueError("alpha must be positive")
    if not 0.0 <= l1_ratio <= 1.0:
        raise ValueError("l1_ratio must be between 0 and 1")
    return Pipeline(
        steps=[
            *_preprocessing_steps(),
            (
                "model",
                ElasticNet(
                    alpha=alpha,
                    l1_ratio=l1_ratio,
                    random_state=random_state,
                    max_iter=max_iter,
                    selection="cyclic",
                ),
            ),
        ]
    )


def build_lasso_pipeline(
    alpha: float,
    *,
    random_state: int,
    max_iter: int = 10_000,
) -> Pipeline:
    """Build a deterministic Lasso benchmark pipeline."""

    if alpha <= 0.0:
        raise ValueError("alpha must be positive")
    return Pipeline(
        steps=[
            *_preprocessing_steps(),
            (
                "model",
                Lasso(
                    alpha=alpha,
                    random_state=random_state,
                    max_iter=max_iter,
                    selection="cyclic",
                ),
            ),
        ]
    )


def build_ols_pipeline() -> Pipeline:
    """Build the unregularized OLS benchmark pipeline.

    OLS is a diagnostic baseline only. With 120 training sessions it is prone to
    variance inflation, so it is never promoted to the production regressor.
    """

    return Pipeline(steps=[*_preprocessing_steps(), ("model", LinearRegression())])
