"""Interchangeable estimators for the walk-forward, so models can be compared.

Production fits a ridge for the level and a logistic for the probability. Both
have now been measured over 250 out-of-sample sessions and both are weak: the
ridge reaches 52.05% direction accuracy against the 56.2% that covers the round
trip, and the logistic is actively harmful -- when it disagrees with the ridge
about the sign, the ridge is right 52.4% of the time and the logistic 47.6%.

Comparing a different model against that means changing one thing. Every
estimator here therefore sees the same rows, the same features, the same
training window and the same walk-forward boundary; only the fitting differs.

Each returns two numbers per session, because the trading layer needs both and
they can fail independently:

    predicted_return  -- the level, scored by MAE, correlation and sign
    probability_up    -- P(return > 0), scored by Brier and log loss

Hyperparameters are chosen inside the training window by time-series
cross-validation. Choosing them from the whole out-of-sample period and then
scoring on it would make the score meaningless, and the tree models are far more
capable of exploiting that than the ridge is.
"""

from __future__ import annotations

import math
from abc import ABC, abstractmethod
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd
from sklearn.ensemble import (  # type: ignore[import-untyped]
    RandomForestRegressor,
)
from sklearn.impute import SimpleImputer  # type: ignore[import-untyped]
from sklearn.linear_model import (  # type: ignore[import-untyped]
    QuantileRegressor,
)
from sklearn.model_selection import (  # type: ignore[import-untyped]
    TimeSeriesSplit,
)
from sklearn.pipeline import Pipeline  # type: ignore[import-untyped]
from sklearn.preprocessing import (  # type: ignore[import-untyped]
    StandardScaler,
)
from sklearn.tree import DecisionTreeRegressor  # type: ignore[import-untyped]

RANDOM_STATE = 42

# The quantiles the quantile arm fits. The middle one is the point forecast;
# the outer pair is an 80% interval that can be checked against how often the
# outcome actually lands inside it -- which the production interval, built from
# in-sample residuals, has never been.
QUANTILES: tuple[float, ...] = (0.1, 0.25, 0.5, 0.75, 0.9)


@dataclass(frozen=True, slots=True)
class Fitted:
    """One session's answer from one estimator."""

    predicted_return: float
    probability_up: float
    parameters: dict[str, Any]
    quantiles: dict[str, float] | None = None
    # The same two scores the out-of-sample side is judged on, measured on the
    # window the fit just saw. Alone they mean nothing -- a tree can reach 100%
    # on its own training rows and still be worthless. The gap between these
    # and the out-of-sample numbers is the overfitting measurement, and it
    # cannot be taken later because the fitted model does not survive the
    # session.
    train_mae: float | None = None
    train_direction: float | None = None


class Estimator(ABC):
    """One way of turning a training window into tomorrow's two numbers."""

    name: str

    @abstractmethod
    def fit_predict(
        self, features: pd.DataFrame, target: np.ndarray, latest: pd.DataFrame
    ) -> Fitted:
        """Fit on the window and answer for the one row that follows it."""


def _matrix(frame: pd.DataFrame) -> np.ndarray:
    return np.asarray(frame.apply(pd.to_numeric, errors="coerce"), dtype=float)


def _prepared(
    train: pd.DataFrame, latest: pd.DataFrame
) -> tuple[np.ndarray, np.ndarray]:
    """Impute and scale, fitted on the training window only."""

    pipeline = Pipeline(
        [
            ("impute", SimpleImputer(strategy="median")),
            ("scale", StandardScaler()),
        ]
    )
    fitted = pipeline.fit_transform(_matrix(train))
    return np.asarray(fitted, dtype=float), np.asarray(
        pipeline.transform(_matrix(latest)), dtype=float
    )


def _splits(rows: int) -> TimeSeriesSplit | None:
    """Forward-chaining folds, or None when the window is too short to split."""

    folds = min(5, max(2, rows // 25))
    return TimeSeriesSplit(n_splits=folds) if rows >= folds * 10 else None


def _probability_from_spread(point: float, spread: float) -> float:
    """P(up) from a point forecast and the dispersion around it.

    A normal assumption on a fat-tailed target is wrong in the tails, but the
    question here is only which side of zero the mass sits on, and near the
    centre the shape barely matters. Bounded away from 0 and 1 because no fit on
    120 rows justifies certainty.
    """

    if spread <= 0:
        return 0.5
    z = point / spread
    probability = 0.5 * (1.0 + math.erf(z / math.sqrt(2)))
    return float(min(max(probability, 0.02), 0.98))


def _in_sample(target: np.ndarray, fitted_values: np.ndarray) -> tuple[float, float]:
    """MAE and direction accuracy on the rows the model was fitted on."""

    mae = float(np.mean(np.abs(target - fitted_values)))
    direction = float(np.mean((fitted_values > 0.0) == (target > 0.0)))
    return mae, direction


class QuantileEstimator(Estimator):
    """Fit several conditional quantiles; the median is the forecast.

    Squared error follows the tails, and this target has them -- a 1.58%
    standard deviation with regular moves past 5%. A median is not dragged the
    same way, so its sign is the more robust directional estimate. The full set
    of quantiles also gives P(up) directly, by asking where zero falls among
    them, which removes the need for a separate classifier.
    """

    name = "quantile"

    def __init__(
        self,
        quantiles: Sequence[float] = QUANTILES,
        alphas: Sequence[float] = (0.001, 0.01, 0.1),
    ) -> None:
        self.quantiles = tuple(quantiles)
        self.alphas = tuple(alphas)

    def _pinball(self, actual: np.ndarray, predicted: np.ndarray, q: float) -> float:
        error = actual - predicted
        return float(np.mean(np.maximum(q * error, (q - 1) * error)))

    def _choose_alpha(self, x: np.ndarray, y: np.ndarray) -> float:
        splits = _splits(len(y))
        if splits is None:
            return self.alphas[len(self.alphas) // 2]
        scores: list[tuple[float, float]] = []
        for alpha in self.alphas:
            losses = []
            for train_index, test_index in splits.split(x):
                model = QuantileRegressor(quantile=0.5, alpha=alpha, solver="highs")
                model.fit(x[train_index], y[train_index])
                losses.append(
                    self._pinball(y[test_index], model.predict(x[test_index]), 0.5)
                )
            scores.append((float(np.mean(losses)), alpha))
        return min(scores)[1]

    def fit_predict(
        self, features: pd.DataFrame, target: np.ndarray, latest: pd.DataFrame
    ) -> Fitted:
        x, x_latest = _prepared(features, latest)
        alpha = self._choose_alpha(x, target)
        predicted: dict[str, float] = {}
        in_window = np.zeros(len(target), dtype=float)
        for q in self.quantiles:
            model = QuantileRegressor(quantile=q, alpha=alpha, solver="highs")
            model.fit(x, target)
            predicted[f"q{q:g}"] = float(model.predict(x_latest)[0])
            if q == 0.5:
                in_window = np.asarray(model.predict(x), dtype=float)
        # Quantile regressions are fitted independently and can cross; sorting
        # restores the ordering a distribution must have. Reporting a crossed
        # pair as-is would put a 90th percentile below a 10th.
        ordered = sorted(predicted.values())
        levels = dict(zip(self.quantiles, ordered, strict=True))
        median = levels[0.5]
        train_mae, train_direction = _in_sample(target, in_window)
        return Fitted(
            predicted_return=median,
            probability_up=_probability_at_zero(self.quantiles, ordered),
            parameters={"alpha": alpha, "quantiles": list(self.quantiles)},
            quantiles={f"q{q:g}": value for q, value in levels.items()},
            train_mae=train_mae,
            train_direction=train_direction,
        )


def _probability_at_zero(quantiles: Sequence[float], levels: Sequence[float]) -> float:
    """P(return > 0), read off the fitted quantile curve.

    Zero sits somewhere among the predicted levels; the quantile it corresponds
    to is P(return <= 0), so one minus it is what the trading layer wants. Both
    ends are handled explicitly rather than extrapolated: a fit on 120 rows
    cannot support a claim past its own outer quantiles.
    """

    if levels[0] >= 0.0:
        return float(1.0 - quantiles[0])
    if levels[-1] <= 0.0:
        return float(1.0 - quantiles[-1])
    for index in range(len(levels) - 1):
        low, high = levels[index], levels[index + 1]
        if low <= 0.0 <= high:
            if high == low:
                below = quantiles[index]
            else:
                weight = (0.0 - low) / (high - low)
                below = quantiles[index] + weight * (
                    quantiles[index + 1] - quantiles[index]
                )
            return float(min(max(1.0 - below, 0.02), 0.98))
    return 0.5


class TreeEstimator(Estimator):
    """A single regression tree, depth chosen inside the training window.

    Included because it is the most interpretable non-linear form available and
    the cheapest to fit, not because it is expected to win: 120 rows against 72
    columns is very little for a tree, and an unconstrained one memorises the
    window. The depth grid stops at four for that reason.
    """

    name = "tree"

    def __init__(self, depths: Sequence[int] = (2, 3, 4)) -> None:
        self.depths = tuple(depths)

    def fit_predict(
        self, features: pd.DataFrame, target: np.ndarray, latest: pd.DataFrame
    ) -> Fitted:
        x, x_latest = _prepared(features, latest)
        depth = _choose_by_cv(
            x,
            target,
            self.depths,
            lambda d: DecisionTreeRegressor(
                max_depth=d, min_samples_leaf=10, random_state=RANDOM_STATE
            ),
        )
        model = DecisionTreeRegressor(
            max_depth=depth, min_samples_leaf=10, random_state=RANDOM_STATE
        )
        model.fit(x, target)
        point = float(model.predict(x_latest)[0])
        in_window = np.asarray(model.predict(x), dtype=float)
        residual = float(np.std(target - in_window, ddof=1))
        train_mae, train_direction = _in_sample(target, in_window)
        return Fitted(
            predicted_return=point,
            probability_up=_probability_from_spread(point, residual),
            parameters={"max_depth": depth},
            train_mae=train_mae,
            train_direction=train_direction,
        )


class ForestEstimator(Estimator):
    """Many shallow trees on feature subsets.

    The one model here with a mechanism that suits the problem: averaging over
    subsets is a way of not committing to any single column, which is what a
    120-row window can support. P(up) comes from the share of trees predicting a
    rise, which is a genuine ensemble vote rather than a normal assumption laid
    over a point estimate.
    """

    name = "forest"

    def __init__(
        self, depths: Sequence[int] = (3, 4, 6), trees: int = 200
    ) -> None:
        self.depths = tuple(depths)
        self.trees = trees

    def _make(self, depth: int, *, oob: bool = False) -> RandomForestRegressor:
        return RandomForestRegressor(
            n_estimators=self.trees,
            max_depth=depth,
            min_samples_leaf=5,
            max_features="sqrt",
            oob_score=oob,
            bootstrap=True,
            random_state=RANDOM_STATE,
            n_jobs=1,
        )

    def _choose_depth(self, x: np.ndarray, y: np.ndarray) -> int:
        """Out-of-bag error rather than cross-validation folds.

        Every tree is already fitted on a bootstrap sample and can be scored on
        the rows it did not draw, so the estimate is free. Folding instead would
        mean five extra forests per depth per session -- about five hours across
        the window for the same answer.
        """

        scored: list[tuple[float, int]] = []
        for depth in self.depths:
            model = self._make(depth, oob=True)
            model.fit(x, y)
            score = getattr(model, "oob_prediction_", None)
            if score is None:
                continue
            scored.append((float(np.mean((y - score) ** 2)), depth))
        return min(scored)[1] if scored else self.depths[0]

    def fit_predict(
        self, features: pd.DataFrame, target: np.ndarray, latest: pd.DataFrame
    ) -> Fitted:
        x, x_latest = _prepared(features, latest)
        depth = self._choose_depth(x, target)
        model = self._make(depth)
        model.fit(x, target)
        point = float(model.predict(x_latest)[0])
        votes = np.array(
            [tree.predict(x_latest)[0] for tree in model.estimators_], dtype=float
        )
        share = float((votes > 0).mean())
        train_mae, train_direction = _in_sample(
            target, np.asarray(model.predict(x), dtype=float)
        )
        return Fitted(
            predicted_return=point,
            probability_up=float(min(max(share, 0.02), 0.98)),
            parameters={"max_depth": depth, "n_estimators": self.trees},
            train_mae=train_mae,
            train_direction=train_direction,
        )


def _choose_by_cv(
    x: np.ndarray,
    y: np.ndarray,
    grid: Sequence[Any],
    make: Any,
) -> Any:
    """Pick the setting with the lowest forward-chained error on this window."""

    splits = _splits(len(y))
    if splits is None:
        return grid[0]
    scored: list[tuple[float, Any]] = []
    for value in grid:
        errors = []
        for train_index, test_index in splits.split(x):
            model = make(value)
            model.fit(x[train_index], y[train_index])
            errors.append(
                float(np.mean((y[test_index] - model.predict(x[test_index])) ** 2))
            )
        scored.append((float(np.mean(errors)), value))
    return min(scored)[1]


ESTIMATORS: dict[str, Any] = {
    "quantile": QuantileEstimator,
    "tree": TreeEstimator,
    "forest": ForestEstimator,
}


def resolve(name: str) -> Estimator:
    if name not in ESTIMATORS:
        raise ValueError(
            f"未知の推定器: {name} / 使えるのは {', '.join(sorted(ESTIMATORS))}"
        )
    estimator: Estimator = ESTIMATORS[name]()
    return estimator
