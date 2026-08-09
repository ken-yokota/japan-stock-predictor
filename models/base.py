"""Typed model-layer contracts."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Protocol, TypeAlias

import pandas as pd

DEFAULT_RANDOM_STATE = 42

RegressionCandidate: TypeAlias = Literal[  # noqa: UP040
    "ridge", "elastic_net", "ols", "lasso"
]

REGRESSION_CANDIDATES: tuple[RegressionCandidate, ...] = (
    "ridge",
    "elastic_net",
    "ols",
    "lasso",
)


@dataclass(frozen=True, slots=True)
class ModelTrainingConfig:
    """Deterministic configuration for one ticker's rolling training window."""

    window_size: int = 120
    minimum_training_sessions: int = 20
    time_series_splits: int = 5
    ridge_alphas: tuple[float, ...] = (0.01, 0.1, 1.0, 10.0, 100.0)
    logistic_cs: tuple[float, ...] = (0.01, 0.1, 1.0, 10.0, 100.0)
    elastic_net_alphas: tuple[float, ...] = (0.001, 0.01, 0.1, 1.0)
    elastic_net_l1_ratios: tuple[float, ...] = (0.1, 0.5, 0.9)
    lasso_alphas: tuple[float, ...] = (0.001, 0.01, 0.1, 1.0)
    # Sessions after which a training row counts half as much. ``None``
    # weights every session in the window equally, which is what the
    # production pipeline has always done.
    recency_half_life_sessions: int | None = None
    random_state: int = DEFAULT_RANDOM_STATE

    def __post_init__(self) -> None:
        if self.window_size < 2:
            raise ValueError("window_size must be at least 2")
        if not 2 <= self.minimum_training_sessions <= self.window_size:
            raise ValueError(
                "minimum_training_sessions must be between 2 and window_size"
            )
        if self.time_series_splits < 2:
            raise ValueError("time_series_splits must be at least 2")
        if not self.ridge_alphas or any(value <= 0 for value in self.ridge_alphas):
            raise ValueError("ridge_alphas must contain only positive values")
        if not self.logistic_cs or any(value <= 0 for value in self.logistic_cs):
            raise ValueError("logistic_cs must contain only positive values")
        if not self.elastic_net_alphas or any(
            value <= 0 for value in self.elastic_net_alphas
        ):
            raise ValueError("elastic_net_alphas must contain only positive values")
        if not self.elastic_net_l1_ratios or any(
            not 0.0 <= value <= 1.0 for value in self.elastic_net_l1_ratios
        ):
            raise ValueError("elastic_net_l1_ratios must be between 0 and 1")
        if not self.lasso_alphas or any(value <= 0 for value in self.lasso_alphas):
            raise ValueError("lasso_alphas must contain only positive values")
        if (
            self.recency_half_life_sessions is not None
            and self.recency_half_life_sessions <= 0
        ):
            raise ValueError("recency_half_life_sessions must be positive")


@dataclass(frozen=True, slots=True)
class TickerPrediction:
    """Regression and direction-classification output for one ticker/session."""

    ticker: str
    predicted_return: float
    probability_up: float
    training_sessions: int
    ridge_alpha: float
    logistic_c: float | None

    def __post_init__(self) -> None:
        if not self.ticker.strip():
            raise ValueError("ticker must not be blank")
        if not 0.0 <= self.probability_up <= 1.0:
            raise ValueError("probability_up must be between 0 and 1")
        if self.training_sessions < 1:
            raise ValueError("training_sessions must be positive")


class PredictiveTickerModel(Protocol):
    """Interface implemented by trained, ticker-specific model bundles."""

    ticker: str
    feature_names: tuple[str, ...]

    def predict_one(self, features: pd.DataFrame) -> TickerPrediction:
        """Predict one session using the already fitted training pipelines."""


class InsufficientTrainingData(ValueError):
    """Raised when a ticker has too few finite target observations."""
