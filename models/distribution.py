"""Conditional return distributions, fitted as a set of quantile regressions.

Each morning used to be answered with one number, plus a 95% band derived from
the residuals of the very fit that produced it. That band was never checked
against outcomes and could not usefully be: in-sample residuals are the errors
the fit already minimised, so the interval they imply is narrower than the one
a future session lands in.

A quantile regression answers the distributional question directly. Fitting
the 5th, 10th, 25th, 50th, 75th, 90th and 95th conditional quantiles of the
intraday return produces a curve that *is* the forecast distribution, and
every claim read off it -- an interval, a probability, the centre -- is
checkable against how often outcomes actually landed there.

On this repository's own out-of-sample window (5,500 ticker-sessions,
``artifacts/oos/quantile_study.txt``) that check has been run: the nominal 80%
band covered 75.5% and the nominal 50% band covered 46.3%. The fitted
distribution is therefore mildly too narrow, by about 4 points, and that is a
measured figure rather than an assumption. Anything read off these quantiles
inherits that optimism and should be read as slightly overconfident.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd
from numpy.typing import NDArray
from sklearn.impute import SimpleImputer  # type: ignore[import-untyped]
from sklearn.linear_model import QuantileRegressor  # type: ignore[import-untyped]
from sklearn.pipeline import Pipeline  # type: ignore[import-untyped]
from sklearn.preprocessing import StandardScaler  # type: ignore[import-untyped]

from models.base import DEFAULT_QUANTILE_ALPHAS, DEFAULT_QUANTILE_LEVELS
from models.optimization import as_model_matrix, chronological_splitter

DISTRIBUTION_METHOD = "quantile_regression_l1"

# What the measured coverage was, and on how many observations. Carried with
# every persisted distribution so a reader of one morning does not have to go
# looking for the study to know how much to trust the band.
COVERAGE_EVIDENCE: dict[str, Any] = {
    "source": "artifacts/oos/quantile_study.txt",
    "samples": 5500,
    "intervals": [
        {"nominal": 0.80, "observed": 0.755},
        {"nominal": 0.50, "observed": 0.463},
    ],
}


def build_quantile_pipeline(quantile: float, alpha: float) -> Pipeline:
    """Build one conditional-quantile pipeline under the shared contract.

    The imputer and scaler are pipeline steps for the same reason they are in
    ``build_ridge_pipeline``: scikit-learn then fits them inside whichever
    training fold it is given, so no validation row can influence the median
    or the scale used to standardize it.
    """

    if not 0.0 < quantile < 1.0:
        raise ValueError("quantile must be strictly between 0 and 1")
    if alpha <= 0.0:
        raise ValueError("alpha must be positive")
    return Pipeline(
        steps=[
            ("imputer", SimpleImputer(strategy="median", keep_empty_features=True)),
            ("scaler", StandardScaler()),
            (
                "model",
                QuantileRegressor(quantile=quantile, alpha=alpha, solver="highs"),
            ),
        ]
    )


def pinball_loss(
    actual: NDArray[np.float64], predicted: NDArray[np.float64], quantile: float
) -> float:
    """The loss a quantile regression minimises, used to score its alpha."""

    error = np.asarray(actual, dtype=float) - np.asarray(predicted, dtype=float)
    return float(np.mean(np.maximum(quantile * error, (quantile - 1.0) * error)))


def select_quantile_alpha(
    features: pd.DataFrame | np.ndarray,
    targets: NDArray[np.float64],
    *,
    candidates: Sequence[float] = DEFAULT_QUANTILE_ALPHAS,
    n_splits: int,
    sample_weight: NDArray[np.float64] | None = None,
) -> float:
    """Choose one alpha for the whole curve by median pinball loss.

    One alpha is selected for every level rather than one per level. Selecting
    each level's own penalty on 120 rows would make the outer quantiles -- the
    ones with the fewest observations either side of them -- the most heavily
    tuned, which is exactly backwards, and it multiplies the number of choices
    made on one small window by seven.
    """

    if not candidates:
        raise ValueError("candidates must not be empty")
    splitter = chronological_splitter(len(targets), n_splits)
    if splitter is None:
        return float(candidates[0])

    matrix = as_model_matrix(features)
    best_value = float(candidates[0])
    best_loss = float("inf")
    for candidate in candidates:
        fold_losses: list[float] = []
        for train_positions, validation_positions in splitter.split(matrix):
            pipeline = build_quantile_pipeline(0.5, float(candidate))
            _fit(
                pipeline,
                matrix[train_positions],
                targets[train_positions],
                None if sample_weight is None else sample_weight[train_positions],
            )
            predicted = np.asarray(
                pipeline.predict(matrix[validation_positions]), dtype=float
            )
            fold_losses.append(
                pinball_loss(targets[validation_positions], predicted, 0.5)
            )
        mean_loss = float(np.mean(fold_losses))
        if mean_loss < best_loss:
            best_value = float(candidate)
            best_loss = mean_loss
    return best_value


def _fit(
    pipeline: Pipeline,
    features: pd.DataFrame | np.ndarray,
    targets: NDArray[np.float64],
    weights: NDArray[np.float64] | None,
) -> None:
    """Fit weighting only the final estimator, as ``fit_with_weights`` does."""

    if weights is None:
        pipeline.fit(features, targets)
        return
    total = float(np.sum(weights))
    if total <= 0.0:
        raise ValueError("sample weights must sum to a positive value")
    balanced = np.asarray(weights, dtype=float) * (len(targets) / total)
    pipeline.fit(features, targets, model__sample_weight=balanced)


@dataclass(frozen=True, slots=True)
class DensityBin:
    """One equal-mass slice of a forecast distribution."""

    low: float
    high: float
    mass: float
    density: float


@dataclass(frozen=True, slots=True)
class ReturnDistribution:
    """One session's forecast distribution, as a monotone quantile curve.

    ``values`` is non-decreasing by construction. Quantile regressions are
    fitted independently and can cross on a small window; a crossed pair would
    put a 90th percentile below a 10th, which is not a distribution at all, so
    the fitted values are sorted before they are stored.
    """

    levels: tuple[float, ...]
    values: tuple[float, ...]
    alpha: float
    training_sessions: int
    method: str = DISTRIBUTION_METHOD

    def __post_init__(self) -> None:
        if len(self.levels) < 2:
            raise ValueError("a distribution needs at least two quantile levels")
        if len(self.levels) != len(self.values):
            raise ValueError("levels and values must have equal length")
        if any(not 0.0 < level < 1.0 for level in self.levels):
            raise ValueError("levels must be strictly between 0 and 1")
        if list(self.levels) != sorted(self.levels):
            raise ValueError("levels must be ascending")
        if len(set(self.levels)) != len(self.levels):
            raise ValueError("levels must be unique")
        if list(self.values) != sorted(self.values):
            raise ValueError("values must be non-decreasing")
        if not all(np.isfinite(self.values)):
            raise ValueError("values must all be finite")
        if self.training_sessions < 1:
            raise ValueError("training_sessions must be positive")

    def quantile(self, level: float) -> float:
        """Return a fitted level, or raise rather than interpolate silently."""

        for candidate, value in zip(self.levels, self.values, strict=True):
            if abs(candidate - level) < 1e-9:
                return value
        raise KeyError(f"quantile {level} was not fitted")

    def has(self, level: float) -> bool:
        return any(abs(candidate - level) < 1e-9 for candidate in self.levels)

    @property
    def median(self) -> float:
        """The centre of the distribution: the 50th percentile if it is fitted.

        Falls back to interpolating the curve at 0.5 so a caller configured
        without a median level still gets the centre rather than an error.
        """

        if self.has(0.5):
            return self.quantile(0.5)
        return float(np.interp(0.5, self.levels, self.values))

    def cumulative(self, threshold: float) -> float:
        """P(return <= threshold), read off the curve by linear interpolation.

        Outside the fitted range the answer is pinned to the outer level. A fit
        on 120 sessions cannot support a claim past its own 5th or 95th
        percentile, and extrapolating one would manufacture confidence the
        data has not earned.
        """

        if threshold <= self.values[0]:
            return float(self.levels[0])
        if threshold >= self.values[-1]:
            return float(self.levels[-1])
        for index in range(len(self.values) - 1):
            low, high = self.values[index], self.values[index + 1]
            if low <= threshold <= high:
                if high == low:
                    return float(self.levels[index])
                weight = (threshold - low) / (high - low)
                return float(
                    self.levels[index]
                    + weight * (self.levels[index + 1] - self.levels[index])
                )
        return 0.5

    def probability_above(self, threshold: float = 0.0) -> float:
        """P(return > threshold). ``0.0`` gives the direction probability."""

        return float(1.0 - self.cumulative(threshold))

    def interval(self, coverage: float) -> tuple[float, float] | None:
        """The central band with the requested nominal coverage, if fitted."""

        if not 0.0 < coverage < 1.0:
            raise ValueError("coverage must be strictly between 0 and 1")
        tail = (1.0 - coverage) / 2.0
        if not (self.has(tail) and self.has(1.0 - tail)):
            return None
        return self.quantile(tail), self.quantile(1.0 - tail)

    def prices(self, reference_price: float) -> tuple[tuple[float, float], ...]:
        """The same curve expressed as prices, given the reference close."""

        if reference_price <= 0.0:
            raise ValueError("reference_price must be positive")
        return tuple(
            (level, reference_price * (1.0 + value))
            for level, value in zip(self.levels, self.values, strict=True)
        )

    def pairs(self) -> tuple[tuple[float, float], ...]:
        """(level, return) pairs, for layers that must not import this module."""

        return tuple(zip(self.levels, self.values, strict=True))

    def density_bins(self) -> tuple[DensityBin, ...]:
        """The forecast density, as equal-mass bins read straight off the curve.

        Between two adjacent fitted levels sits exactly their difference in
        probability, so a bin's height is that mass divided by its width. No
        shape is assumed anywhere: a narrow gap between two quantiles *is* the
        model saying outcomes bunch there, and a wide one *is* it saying they
        do not.

        Ties are merged rather than divided by. Two quantiles fitted to the
        same value produce a zero-width bin whose density is infinite, which is
        arithmetic rather than a claim about the market; the mass is folded
        into the next bin that has width.
        """

        bins: list[DensityBin] = []
        pending_mass = 0.0
        for index in range(len(self.values) - 1):
            low, high = self.values[index], self.values[index + 1]
            mass = self.levels[index + 1] - self.levels[index]
            width = high - low
            if width <= 0.0:
                pending_mass += mass
                continue
            total = mass + pending_mass
            pending_mass = 0.0
            bins.append(DensityBin(low, high, total, total / width))
        return tuple(bins)

    def density_profile(
        self, low: float, high: float, columns: int
    ) -> tuple[float, ...]:
        """Probability mass per equal-width column across ``[low, high]``.

        Equal-mass bins have unequal widths, which makes two tickers hard to
        compare by eye. Resampling onto one shared, evenly spaced axis fixes
        that: every column is the same width, so the bar heights are directly
        the density, and the same axis can be used for every row of a table.

        Columns outside the fitted range come back at zero, because the
        cumulative function is pinned at the outermost fitted level. That is
        the honest picture -- the 5% in each tail is mass this window cannot
        place, so it is shown as absent rather than drawn as a shape.
        """

        if columns < 1:
            raise ValueError("columns must be positive")
        if high <= low:
            raise ValueError("high must exceed low")
        edges = [low + (high - low) * index / columns for index in range(columns + 1)]
        cumulative = [self.cumulative(edge) for edge in edges]
        return tuple(
            max(cumulative[index + 1] - cumulative[index], 0.0)
            for index in range(columns)
        )

    def to_payload(self) -> dict[str, Any]:
        """A JSON-safe record of the curve and how much to trust it."""

        return {
            "method": self.method,
            "alpha": self.alpha,
            "training_sessions": self.training_sessions,
            "levels": [
                {"quantile": level, "return": value}
                for level, value in zip(self.levels, self.values, strict=True)
            ],
            "coverage_evidence": COVERAGE_EVIDENCE,
        }

    @classmethod
    def from_payload(cls, payload: Mapping[str, Any]) -> ReturnDistribution:
        """Rebuild a curve persisted by :meth:`to_payload`."""

        rows = payload.get("levels") or []
        levels = tuple(float(row["quantile"]) for row in rows)
        values = tuple(float(row["return"]) for row in rows)
        return cls(
            levels=levels,
            values=values,
            alpha=float(payload.get("alpha", 0.0)),
            training_sessions=int(payload.get("training_sessions", 1)),
            method=str(payload.get("method", DISTRIBUTION_METHOD)),
        )


@dataclass(frozen=True, slots=True)
class QuantileEnsemble:
    """The fitted conditional-quantile pipelines for exactly one ticker."""

    levels: tuple[float, ...]
    pipelines: tuple[Pipeline, ...]
    alpha: float
    training_sessions: int

    def predict_distribution(self, numeric: pd.DataFrame) -> ReturnDistribution:
        """Answer one out-of-sample row with a monotone quantile curve."""

        if len(numeric) != 1:
            raise ValueError("predict_distribution requires exactly one row")
        predicted = [
            float(np.asarray(pipeline.predict(numeric), dtype=float)[0])
            for pipeline in self.pipelines
        ]
        return ReturnDistribution(
            levels=self.levels,
            values=tuple(sorted(predicted)),
            alpha=self.alpha,
            training_sessions=self.training_sessions,
        )


def fit_quantile_ensemble(
    numeric: pd.DataFrame,
    targets: NDArray[np.float64],
    *,
    levels: Sequence[float] = DEFAULT_QUANTILE_LEVELS,
    alphas: Sequence[float] = DEFAULT_QUANTILE_ALPHAS,
    n_splits: int,
    sample_weight: NDArray[np.float64] | None = None,
) -> QuantileEnsemble:
    """Fit one pipeline per requested level on the caller's training window."""

    ordered = tuple(sorted(float(level) for level in levels))
    if len(ordered) < 2:
        raise ValueError("at least two quantile levels are required")
    alpha = select_quantile_alpha(
        numeric,
        targets,
        candidates=alphas,
        n_splits=n_splits,
        sample_weight=sample_weight,
    )
    pipelines: list[Pipeline] = []
    for level in ordered:
        pipeline = build_quantile_pipeline(level, alpha)
        _fit(pipeline, numeric, targets, sample_weight)
        pipelines.append(pipeline)
    return QuantileEnsemble(
        levels=ordered,
        pipelines=tuple(pipelines),
        alpha=alpha,
        training_sessions=len(targets),
    )


def empirical_distribution(
    centre: float,
    residuals: NDArray[np.float64],
    *,
    levels: Sequence[float] = DEFAULT_QUANTILE_LEVELS,
    training_sessions: int,
) -> ReturnDistribution | None:
    """A residual-quantile fallback when the quantile fit cannot be made.

    Used only when the LP solver fails or the window is too short for a
    quantile fit. It is weaker than the fitted curve in a specific way worth
    naming: the residuals are in-sample, so the band it produces is narrower
    than the outcomes will be, and it is the same width for every ticker on
    every day because it cannot vary the spread with the inputs. It is
    recorded under its own method name so a reader never mistakes one for the
    other.
    """

    finite = np.asarray(residuals, dtype=float)
    finite = finite[np.isfinite(finite)]
    if len(finite) < 2 or not np.isfinite(centre):
        return None
    ordered = tuple(sorted(float(level) for level in levels))
    values = tuple(
        sorted(float(centre + value) for value in np.quantile(finite, ordered))
    )
    return ReturnDistribution(
        levels=ordered,
        values=values,
        alpha=0.0,
        training_sessions=training_sessions,
        method="residual_quantiles",
    )


def distribution_from_pairs(
    pairs: Sequence[tuple[float, float]],
) -> ReturnDistribution | None:
    """Rebuild a curve from (level, value) pairs, or ``None`` if unusable."""

    if len(pairs) < 2:
        return None
    ordered = sorted(pairs)
    return ReturnDistribution(
        levels=tuple(level for level, _ in ordered),
        values=tuple(sorted(value for _, value in ordered)),
        alpha=0.0,
        training_sessions=1,
    )


__all__ = [
    "COVERAGE_EVIDENCE",
    "DISTRIBUTION_METHOD",
    "DensityBin",
    "QuantileEnsemble",
    "ReturnDistribution",
    "build_quantile_pipeline",
    "distribution_from_pairs",
    "empirical_distribution",
    "fit_quantile_ensemble",
    "pinball_loss",
    "select_quantile_alpha",
]
