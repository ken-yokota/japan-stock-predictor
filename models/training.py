"""Ticker-specific rolling Ridge and Logistic model training."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, TypeAlias, cast

import numpy as np
import pandas as pd
from numpy.typing import NDArray
from sklearn.pipeline import Pipeline  # type: ignore[import-untyped]

from models.base import (
    InsufficientTrainingData,
    ModelTrainingConfig,
    TickerPrediction,
)
from models.classifier import build_logistic_pipeline
from models.optimization import (
    fit_with_weights,
    select_logistic_c,
    select_ridge_alpha,
)
from models.ridge import build_ridge_pipeline

CoefficientMap: TypeAlias = dict[str, float]  # noqa: UP040
ModelTask: TypeAlias = Literal["regression", "classification"]  # noqa: UP040


@dataclass(frozen=True, slots=True)
class ScalerStatistics:
    """Serializable ``StandardScaler`` state for one fitted task pipeline."""

    task: ModelTask
    means: CoefficientMap
    scales: CoefficientMap


def _numeric_feature_frame(
    frame: pd.DataFrame, feature_names: tuple[str, ...]
) -> pd.DataFrame:
    missing = [name for name in feature_names if name not in frame.columns]
    if missing:
        raise ValueError(f"missing feature columns: {missing}")
    numeric = frame.loc[:, feature_names].apply(pd.to_numeric, errors="coerce")
    return numeric.replace([np.inf, -np.inf], np.nan)


def recency_weights(count: int, half_life: int | None) -> NDArray[np.float64] | None:
    """Return per-session weights that halve every ``half_life`` sessions.

    Rows arrive oldest-first, so the last row is today and carries weight 1.
    ``None`` returns ``None`` rather than a vector of ones, which keeps the
    unweighted path calling plain ``fit`` and byte-identical to before.

    The weights are rescaled to sum to ``count``. Ridge's alpha and Logistic's
    C are defined against the total weight, so without that rescaling a shorter
    half-life would silently increase the effective regularization and the
    comparison would confound two changes.
    """

    if half_life is None:
        return None
    if count <= 0:
        raise ValueError("count must be positive")
    ages = np.arange(count - 1, -1, -1, dtype=float)
    weights = np.power(0.5, ages / float(half_life))
    return cast("NDArray[np.float64]", weights * (count / weights.sum()))


def _feature_map(
    feature_names: tuple[str, ...], values: NDArray[np.float64]
) -> CoefficientMap:
    return {
        name: float(value) for name, value in zip(feature_names, values, strict=True)
    }


@dataclass(slots=True)
class TickerModelBundle:
    """Fitted regression/classification pipelines for exactly one ticker."""

    ticker: str
    feature_names: tuple[str, ...]
    regressor: Pipeline
    classifier: Pipeline | None
    constant_probability_up: float
    training_sessions: int
    ridge_alpha: float
    logistic_c: float | None

    def __post_init__(self) -> None:
        if not self.ticker.strip():
            raise ValueError("ticker must not be blank")
        if not self.feature_names:
            raise ValueError("feature_names must not be empty")
        if not 0.0 <= self.constant_probability_up <= 1.0:
            raise ValueError("constant_probability_up must be between 0 and 1")

    def predict_returns(self, features: pd.DataFrame) -> NDArray[np.float64]:
        """Return Ridge predictions for one or more rows."""

        numeric = _numeric_feature_frame(features, self.feature_names)
        return cast(
            "NDArray[np.float64]",
            np.asarray(self.regressor.predict(numeric), dtype=float),
        )

    def predict_probabilities(self, features: pd.DataFrame) -> NDArray[np.float64]:
        """Return upward-move probabilities for one or more rows."""

        numeric = _numeric_feature_frame(features, self.feature_names)
        if self.classifier is None:
            return np.full(len(numeric), self.constant_probability_up, dtype=float)
        probabilities = np.asarray(self.classifier.predict_proba(numeric), dtype=float)
        return cast("NDArray[np.float64]", np.clip(probabilities[:, 1], 0.0, 1.0))

    def predict_one(self, features: pd.DataFrame) -> TickerPrediction:
        """Predict exactly one out-of-sample session."""

        if len(features) != 1:
            raise ValueError("predict_one requires exactly one row")
        predicted_return = float(self.predict_returns(features)[0])
        probability_up = float(self.predict_probabilities(features)[0])
        return TickerPrediction(
            ticker=self.ticker,
            predicted_return=predicted_return,
            probability_up=probability_up,
            training_sessions=self.training_sessions,
            ridge_alpha=self.ridge_alpha,
            logistic_c=self.logistic_c,
        )

    def regression_coefficients(self) -> CoefficientMap:
        """Return standardized Ridge coefficients keyed by feature name."""

        model = self.regressor.named_steps["model"]
        coefficients = np.asarray(model.coef_, dtype=float).reshape(-1)
        return _feature_map(self.feature_names, coefficients)

    def classification_coefficients(self) -> CoefficientMap:
        """Return standardized Logistic coefficients, or zeros on fallback."""

        if self.classifier is None:
            return dict.fromkeys(self.feature_names, 0.0)
        model = self.classifier.named_steps["model"]
        coefficients = np.asarray(model.coef_, dtype=float).reshape(-1)
        return _feature_map(self.feature_names, coefficients)

    def regression_intercept(self) -> float:
        """Return the fitted Ridge intercept for persistence/reconstruction."""

        model = self.regressor.named_steps["model"]
        return float(np.asarray(model.intercept_, dtype=float).reshape(-1)[0])

    def classification_intercept(self) -> float | None:
        """Return Logistic intercept, or ``None`` for single-class fallback."""

        if self.classifier is None:
            return None
        model = self.classifier.named_steps["model"]
        return float(np.asarray(model.intercept_, dtype=float).reshape(-1)[0])

    def scaler_statistics(self, task: ModelTask) -> ScalerStatistics | None:
        """Return feature-keyed scaler means/scales for the requested task."""

        if task not in ("regression", "classification"):
            raise ValueError("task must be 'regression' or 'classification'")
        pipeline = self.regressor if task == "regression" else self.classifier
        if pipeline is None:
            return None
        scaler = pipeline.named_steps["scaler"]
        means = np.asarray(scaler.mean_, dtype=float).reshape(-1)
        scales = np.asarray(scaler.scale_, dtype=float).reshape(-1)
        return ScalerStatistics(
            task=task,
            means=_feature_map(self.feature_names, means),
            scales=_feature_map(self.feature_names, scales),
        )

    def classification_constant_probability(self) -> float | None:
        """Expose the reproducible single-class fallback probability."""

        return self.constant_probability_up if self.classifier is None else None


def train_ticker_model(
    ticker: str,
    features: pd.DataFrame,
    intraday_returns: pd.Series | np.ndarray,
    *,
    feature_names: tuple[str, ...] | None = None,
    config: ModelTrainingConfig | None = None,
) -> TickerModelBundle:
    """Fit one ticker using only its final rolling training window.

    Missing feature values stay inside the scikit-learn pipelines, ensuring the
    imputer and scaler are fitted independently within each CV/training fold.
    The final 120 (or configured) chronological sessions are selected first;
    non-finite target rows are then discarded. This prevents missing targets
    from silently extending the intended rolling window farther into history.
    """

    if not ticker.strip():
        raise ValueError("ticker must not be blank")
    settings = config or ModelTrainingConfig()
    names = (
        tuple(str(column) for column in features.columns)
        if feature_names is None
        else feature_names
    )
    if not names:
        raise ValueError("at least one feature is required")
    numeric = _numeric_feature_frame(features, names).reset_index(drop=True)
    targets = np.asarray(intraday_returns, dtype=float).reshape(-1)
    if len(numeric) != len(targets):
        raise ValueError("features and intraday_returns must have equal length")

    if len(targets) > settings.window_size:
        numeric = numeric.iloc[-settings.window_size :].reset_index(drop=True)
        targets = targets[-settings.window_size :]
    finite_target = np.isfinite(targets)
    numeric = numeric.loc[finite_target].reset_index(drop=True)
    targets = targets[finite_target]
    if len(targets) < settings.minimum_training_sessions:
        raise InsufficientTrainingData(
            f"{ticker} has {len(targets)} usable sessions; "
            f"requires {settings.minimum_training_sessions}"
        )

    weights = recency_weights(len(targets), settings.recency_half_life_sessions)
    ridge_alpha = select_ridge_alpha(
        numeric,
        targets,
        candidates=settings.ridge_alphas,
        n_splits=settings.time_series_splits,
        sample_weight=weights,
    )
    regressor = build_ridge_pipeline(ridge_alpha)
    fit_with_weights(regressor, numeric, targets, weights)

    direction_targets = (targets > 0.0).astype(np.int64)
    constant_probability = float(np.average(direction_targets, weights=weights))
    classifier: Pipeline | None = None
    logistic_c: float | None = None
    if len(np.unique(direction_targets)) >= 2:
        logistic_c = select_logistic_c(
            numeric,
            direction_targets,
            candidates=settings.logistic_cs,
            n_splits=settings.time_series_splits,
            random_state=settings.random_state,
            sample_weight=weights,
        )
        classifier = build_logistic_pipeline(
            logistic_c, random_state=settings.random_state
        )
        fit_with_weights(classifier, numeric, direction_targets, weights)

    return TickerModelBundle(
        ticker=ticker,
        feature_names=names,
        regressor=regressor,
        classifier=classifier,
        constant_probability_up=constant_probability,
        training_sessions=len(targets),
        ridge_alpha=ridge_alpha,
        logistic_c=logistic_c,
    )


def train_models_by_ticker(
    frame: pd.DataFrame,
    *,
    feature_names: tuple[str, ...],
    ticker_column: str = "ticker",
    target_column: str = "intraday_return",
    date_column: str = "market_date",
    config: ModelTrainingConfig | None = None,
) -> dict[str, TickerModelBundle]:
    """Train independent model bundles for every ticker in a long frame."""

    required = {ticker_column, target_column, date_column, *feature_names}
    missing = sorted(required - set(frame.columns))
    if missing:
        raise ValueError(f"missing required columns: {missing}")
    models: dict[str, TickerModelBundle] = {}
    ordered = frame.sort_values([ticker_column, date_column], kind="stable")
    for raw_ticker, group in ordered.groupby(ticker_column, sort=False):
        ticker = str(raw_ticker)
        models[ticker] = train_ticker_model(
            ticker,
            group.loc[:, feature_names],
            group[target_column],
            feature_names=feature_names,
            config=config,
        )
    return models


# Concise alias for integrations that use a singular model-domain name.
TickerModel = TickerModelBundle
