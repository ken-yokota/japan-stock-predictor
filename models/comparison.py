"""Chronological regression-candidate comparison.

The production regressor stays Ridge (``config/model.yaml``
``regression_primary``). This module answers a separate question: on one
ticker's training window, would ElasticNet, OLS, or Lasso have validated better
under the same ``TimeSeriesSplit`` folds?

Every number here is computed inside the caller's training window only, so a
comparison run at prediction date ``t`` never sees row ``t``. Results are
diagnostic and must not be presented as investment performance -- promoting a
candidate on the strength of this score and then reporting the same score as
out-of-sample performance would be selection bias.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from functools import partial
from typing import Any

import numpy as np
import pandas as pd
from sklearn.pipeline import Pipeline  # type: ignore[import-untyped]

from models.base import (
    REGRESSION_CANDIDATES,
    ModelTrainingConfig,
    RegressionCandidate,
)
from models.linear import (
    build_elastic_net_pipeline,
    build_lasso_pipeline,
    build_ols_pipeline,
)
from models.optimization import chronological_splitter
from models.ridge import build_ridge_pipeline


@dataclass(frozen=True, slots=True)
class CandidateScore:
    """One regression candidate's chronological validation result."""

    candidate: RegressionCandidate
    mean_squared_error: float
    root_mean_squared_error: float
    mean_absolute_error: float
    hyperparameters: dict[str, float]
    folds_evaluated: int
    status: str

    def as_dict(self) -> dict[str, Any]:
        """Return a serialization-friendly mapping for reports and DB rows."""

        return {
            "candidate": self.candidate,
            "mean_squared_error": self.mean_squared_error,
            "root_mean_squared_error": self.root_mean_squared_error,
            "mean_absolute_error": self.mean_absolute_error,
            "hyperparameters": dict(self.hyperparameters),
            "folds_evaluated": self.folds_evaluated,
            "status": self.status,
        }


@dataclass(frozen=True, slots=True)
class RegressionComparison:
    """Ranked candidate scores plus the configured production choice."""

    production_candidate: RegressionCandidate
    scores: tuple[CandidateScore, ...]

    @property
    def best_candidate(self) -> RegressionCandidate | None:
        """Return the lowest-error evaluated candidate, or ``None`` if none ran."""

        evaluated = [score for score in self.scores if score.status == "OK"]
        if not evaluated:
            return None
        return min(evaluated, key=lambda score: score.mean_squared_error).candidate

    def score_for(self, candidate: RegressionCandidate) -> CandidateScore | None:
        """Return one candidate's score, or ``None`` when it was not evaluated."""

        for score in self.scores:
            if score.candidate == candidate:
                return score
        return None

    def as_dict(self) -> dict[str, Any]:
        """Return a serialization-friendly mapping for reports and artifacts."""

        return {
            "production_candidate": self.production_candidate,
            "best_candidate": self.best_candidate,
            "scores": [score.as_dict() for score in self.scores],
        }


def _candidate_grid(
    candidate: RegressionCandidate, config: ModelTrainingConfig
) -> tuple[dict[str, float], ...]:
    if candidate == "ridge":
        return tuple({"alpha": float(value)} for value in config.ridge_alphas)
    if candidate == "lasso":
        return tuple({"alpha": float(value)} for value in config.lasso_alphas)
    if candidate == "elastic_net":
        return tuple(
            {"alpha": float(alpha), "l1_ratio": float(l1_ratio)}
            for alpha in config.elastic_net_alphas
            for l1_ratio in config.elastic_net_l1_ratios
        )
    return ({},)


def _build_candidate(
    candidate: RegressionCandidate,
    hyperparameters: dict[str, float],
    config: ModelTrainingConfig,
) -> Pipeline:
    if candidate == "ridge":
        return build_ridge_pipeline(hyperparameters["alpha"])
    if candidate == "lasso":
        return build_lasso_pipeline(
            hyperparameters["alpha"], random_state=config.random_state
        )
    if candidate == "elastic_net":
        return build_elastic_net_pipeline(
            hyperparameters["alpha"],
            hyperparameters["l1_ratio"],
            random_state=config.random_state,
        )
    return build_ols_pipeline()


def _score_grid_point(
    pipeline_factory: Callable[[], Pipeline],
    features: pd.DataFrame,
    targets: np.ndarray,
    fold_indices: Sequence[tuple[np.ndarray, np.ndarray]],
) -> tuple[float, float, int]:
    squared_errors: list[float] = []
    absolute_errors: list[float] = []
    for train_positions, validation_positions in fold_indices:
        pipeline = pipeline_factory()
        pipeline.fit(features.iloc[train_positions], targets[train_positions])
        prediction = np.asarray(
            pipeline.predict(features.iloc[validation_positions]), dtype=float
        )
        error = prediction - targets[validation_positions]
        squared_errors.append(float(np.mean(np.square(error))))
        absolute_errors.append(float(np.mean(np.abs(error))))
    if not squared_errors:
        return float("inf"), float("inf"), 0
    return (
        float(np.mean(squared_errors)),
        float(np.mean(absolute_errors)),
        len(squared_errors),
    )


def compare_regression_candidates(
    features: pd.DataFrame,
    targets: np.ndarray,
    *,
    candidates: Sequence[RegressionCandidate] = REGRESSION_CANDIDATES,
    production_candidate: RegressionCandidate = "ridge",
    config: ModelTrainingConfig | None = None,
) -> RegressionComparison:
    """Rank regression candidates by mean chronological validation error.

    Each candidate's own hyperparameter grid is searched inside the same folds,
    so the reported error is that candidate's best chronologically validated
    setting. Candidates that cannot be evaluated (too few rows to split) are
    returned with ``status`` ``NOT_EVALUATED`` rather than a fabricated score.
    """

    settings = config or ModelTrainingConfig()
    if not candidates:
        raise ValueError("candidates must not be empty")
    unknown = sorted(set(candidates) - set(REGRESSION_CANDIDATES))
    if unknown:
        raise ValueError(f"unknown regression candidates: {unknown}")
    outcomes = np.asarray(targets, dtype=float).reshape(-1)
    if len(features) != len(outcomes):
        raise ValueError("features and targets must have equal length")

    splitter = chronological_splitter(len(features), settings.time_series_splits)
    fold_indices: tuple[tuple[np.ndarray, np.ndarray], ...] = (
        ()
        if splitter is None
        else tuple(
            (train, validation) for train, validation in splitter.split(features)
        )
    )

    scores: list[CandidateScore] = []
    for candidate in candidates:
        if not fold_indices:
            scores.append(
                CandidateScore(
                    candidate=candidate,
                    mean_squared_error=float("inf"),
                    root_mean_squared_error=float("inf"),
                    mean_absolute_error=float("inf"),
                    hyperparameters={},
                    folds_evaluated=0,
                    status="NOT_EVALUATED",
                )
            )
            continue
        best_mse = float("inf")
        best_mae = float("inf")
        best_hyperparameters: dict[str, float] = {}
        folds = 0
        for grid_point in _candidate_grid(candidate, settings):
            mse, mae, evaluated = _score_grid_point(
                partial(_build_candidate, candidate, grid_point, settings),
                features,
                outcomes,
                fold_indices,
            )
            if mse < best_mse:
                best_mse = mse
                best_mae = mae
                best_hyperparameters = dict(grid_point)
                folds = evaluated
        status = "OK" if np.isfinite(best_mse) else "NOT_EVALUATED"
        scores.append(
            CandidateScore(
                candidate=candidate,
                mean_squared_error=best_mse,
                root_mean_squared_error=(
                    float(np.sqrt(best_mse)) if np.isfinite(best_mse) else float("inf")
                ),
                mean_absolute_error=best_mae,
                hyperparameters=best_hyperparameters,
                folds_evaluated=folds,
                status=status,
            )
        )

    ranked = tuple(
        sorted(
            scores,
            key=lambda score: (score.status != "OK", score.mean_squared_error),
        )
    )
    return RegressionComparison(
        production_candidate=production_candidate, scores=ranked
    )
