"""Every model family this project runs, behind one interface.

The operator asked for six families to be run each morning rather than the two
that were in production. They do not answer the same question in the same way,
and flattening that would be the dishonest part of the job, so each arm answers
in the way that is natural to it and says which way that was:

    linear (ridge / lasso / elastic_net)
        The level comes from the fitted linear model. The spread comes from
        that arm's own *out-of-fold* residuals inside the training window --
        never the in-sample ones, which are the errors the fit already
        minimised and would make every linear arm look more certain than it is.
        The width therefore does not vary with today's inputs, and that is
        stated wherever it is shown.

    logistic
        A direction probability and nothing else. It does not estimate a
        return, so it has no distribution, and one is not invented for it.

    random_forest
        The level is the mean over the trees and the spread is the spread
        *between* them, so the distribution is a real ensemble disagreement
        that varies with the inputs rather than a band laid over a point.

    lightgbm / xgboost
        Fitted directly against pinball loss at each reported quantile, so the
        curve is a conditional distribution in the same sense the production
        quantile arm's is.

    neural (lstm / transformer)
        Same pinball objective, several outputs. Available only when a backend
        is installed; absent, the arm reports UNAVAILABLE rather than silently
        not appearing.

Nothing here changes what is traded. The buy rule still reads the production
Ridge and Logistic, and these arms are reported beside it. Promoting one is a
separate decision that needs its own out-of-sample evidence -- this repository
has already measured that the forest ranks below production on the six-arm
comparison, and running it every morning does not change that.
"""

from __future__ import annotations

import warnings
from dataclasses import dataclass, field
from typing import Any, Protocol

import numpy as np
import pandas as pd
from numpy.typing import NDArray
from sklearn.ensemble import RandomForestRegressor  # type: ignore[import-untyped]

from models.base import DEFAULT_QUANTILE_LEVELS, DEFAULT_RANDOM_STATE
from models.classifier import build_logistic_pipeline
from models.distribution import ReturnDistribution
from models.linear import build_elastic_net_pipeline, build_lasso_pipeline
from models.neural import build_mlp_pipeline
from models.optimization import chronological_splitter, fit_with_weights
from models.ridge import build_ridge_pipeline

OK = "OK"
FAILED = "FAILED"
UNAVAILABLE = "UNAVAILABLE"

# How a distribution was arrived at, named so two of them are never read as
# equally informative. ``conditional`` widths react to today's inputs;
# ``residual`` widths are the same for every session of the same ticker.
CONDITIONAL = "conditional"
ENSEMBLE = "ensemble"
RESIDUAL = "residual"


@dataclass(frozen=True, slots=True)
class ArmForecast:
    """One model family's answer for one ticker-session."""

    name: str
    label: str
    status: str = OK
    predicted_return: float | None = None
    probability_up: float | None = None
    distribution: ReturnDistribution | None = None
    # conditional / ensemble / residual, or None when the arm has no spread.
    spread_kind: str | None = None
    parameters: dict[str, Any] = field(default_factory=dict)
    detail: str = ""

    def to_payload(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "label": self.label,
            "status": self.status,
            "predicted_return": self.predicted_return,
            "probability_up": self.probability_up,
            "spread_kind": self.spread_kind,
            "parameters": self.parameters,
            "detail": self.detail,
            "distribution": (
                self.distribution.to_payload()
                if self.distribution is not None
                else None
            ),
        }


class Arm(Protocol):
    """One model family, fitted on a window and asked about the next session.

    ``name`` and ``label`` are read-only on purpose: the sequence arms are
    frozen dataclasses, and a protocol demanding settable attributes would
    exclude them for no reason a caller cares about.
    """

    @property
    def name(self) -> str:
        """Stable identifier, as stored in ``arm_predictions``."""

    @property
    def label(self) -> str:
        """What a reader of the morning mail sees."""

    def forecast(
        self,
        features: pd.DataFrame,
        target: NDArray[np.float64],
        latest: pd.DataFrame,
        *,
        levels: tuple[float, ...],
        n_splits: int,
    ) -> ArmForecast:
        """Answer for ``latest``, or return a FAILED forecast rather than raise."""


def _matrix(frame: pd.DataFrame) -> NDArray[np.float64]:
    numeric = frame.apply(pd.to_numeric, errors="coerce")
    return np.asarray(numeric.replace([np.inf, -np.inf], np.nan), dtype=float)


def _curve_from(
    centre: float,
    offsets: NDArray[np.float64],
    levels: tuple[float, ...],
    training_sessions: int,
    method: str,
) -> ReturnDistribution | None:
    """Build a curve by placing ``offsets``' quantiles around ``centre``."""

    finite = offsets[np.isfinite(offsets)]
    if len(finite) < 2 or not np.isfinite(centre):
        return None
    values = tuple(sorted(float(centre + v) for v in np.quantile(finite, levels)))
    return ReturnDistribution(
        levels=levels,
        values=values,
        alpha=0.0,
        training_sessions=training_sessions,
        method=method,
    )


def out_of_fold_residuals(
    make_pipeline: Any,
    matrix: NDArray[np.float64],
    target: NDArray[np.float64],
    n_splits: int,
) -> NDArray[np.float64]:
    """Residuals from folds that had not seen the row being scored.

    This is the whole difference between a band worth printing and one that
    flatters the model. An in-sample residual is an error the fit has already
    been optimised against; the width it implies is narrower than the width the
    next session will actually land in, and no amount of care elsewhere repairs
    a spread built from it.
    """

    splitter = chronological_splitter(len(target), n_splits)
    if splitter is None:
        return np.asarray([], dtype=float)
    residuals: list[float] = []
    for train_index, test_index in splitter.split(matrix):
        pipeline = make_pipeline()
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            fit_with_weights(pipeline, matrix[train_index], target[train_index], None)
            predicted = np.asarray(pipeline.predict(matrix[test_index]), dtype=float)
        residuals.extend(float(v) for v in (target[test_index] - predicted))
    return np.asarray(residuals, dtype=float)


class PointArm:
    """An estimator of the conditional mean, with its spread from out-of-fold error.

    Ridge, Lasso, ElasticNet and the MLP differ in how they constrain the fit,
    not in what they estimate: all four answer with a conditional *mean*. So
    all four get their spread the same way, from their own walk-forward
    residuals, and the width does not move with today's inputs. That is a real
    limitation and it is reported as ``residual`` so it is never mistaken for
    the conditional curve the quantile and boosting arms produce.
    """

    def __init__(
        self,
        name: str,
        label: str,
        make: Any,
        grid: dict[str, tuple[Any, ...]],
    ) -> None:
        self.name = name
        self.label = label
        self._make = make
        self._grid = grid

    def _select(
        self, matrix: NDArray[np.float64], target: NDArray[np.float64], n_splits: int
    ) -> dict[str, Any]:
        """Choose hyperparameters inside the window, never across it."""

        keys = list(self._grid)
        combinations: list[dict[str, Any]] = [{}]
        for key in keys:
            combinations = [
                {**base, key: value}
                for base in combinations
                for value in self._grid[key]
            ]
        splitter = chronological_splitter(len(target), n_splits)
        if splitter is None:
            return combinations[0]
        best, best_loss = combinations[0], float("inf")
        for choice in combinations:
            losses: list[float] = []
            for train_index, test_index in splitter.split(matrix):
                with warnings.catch_warnings():
                    warnings.simplefilter("ignore")
                    pipeline = self._make(**choice)
                    pipeline.fit(matrix[train_index], target[train_index])
                    predicted = np.asarray(
                        pipeline.predict(matrix[test_index]), dtype=float
                    )
                losses.append(float(np.mean((target[test_index] - predicted) ** 2)))
            loss = float(np.mean(losses))
            if loss < best_loss:
                best, best_loss = choice, loss
        return best

    def forecast(
        self,
        features: pd.DataFrame,
        target: NDArray[np.float64],
        latest: pd.DataFrame,
        *,
        levels: tuple[float, ...],
        n_splits: int,
    ) -> ArmForecast:
        try:
            matrix, current = _matrix(features), _matrix(latest)
            choice = self._select(matrix, target, n_splits)
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                pipeline = self._make(**choice)
                pipeline.fit(matrix, target)
                point = float(np.asarray(pipeline.predict(current), dtype=float)[0])
            residuals = out_of_fold_residuals(
                lambda: self._make(**choice), matrix, target, n_splits
            )
            curve = _curve_from(
                point, residuals, levels, len(target), f"{self.name}_oof_residuals"
            )
            return ArmForecast(
                name=self.name,
                label=self.label,
                predicted_return=point,
                probability_up=(
                    curve.probability_above(0.0) if curve is not None else None
                ),
                distribution=curve,
                spread_kind=RESIDUAL if curve is not None else None,
                parameters={k: float(v) for k, v in choice.items()},
            )
        except Exception as error:  # one arm must not cost the morning
            return ArmForecast(
                self.name, self.label, status=FAILED, detail=type(error).__name__
            )


class LogisticArm:
    """Direction only. It estimates no return, so it is given no distribution.

    Filling one in -- by borrowing another arm's width, say -- would present a
    classifier as if it had opinions about magnitude. It does not.
    """

    name = "logistic"
    label = "ロジスティック回帰"

    def __init__(self, cs: tuple[float, ...] = (0.01, 0.1, 1.0, 10.0, 100.0)) -> None:
        self.cs = cs

    def forecast(
        self,
        features: pd.DataFrame,
        target: NDArray[np.float64],
        latest: pd.DataFrame,
        *,
        levels: tuple[float, ...],
        n_splits: int,
    ) -> ArmForecast:
        try:
            from models.optimization import select_logistic_c

            matrix, current = _matrix(features), _matrix(latest)
            direction = (target > 0.0).astype(np.int64)
            if len(np.unique(direction)) < 2:
                share = float(np.mean(direction))
                return ArmForecast(
                    self.name,
                    self.label,
                    probability_up=share,
                    parameters={"fallback_constant": share},
                    detail="学習窓の値動きが一方向のみ",
                )
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                chosen = select_logistic_c(
                    pd.DataFrame(matrix),
                    direction,
                    candidates=self.cs,
                    n_splits=n_splits,
                    random_state=DEFAULT_RANDOM_STATE,
                )
                pipeline = build_logistic_pipeline(
                    chosen, random_state=DEFAULT_RANDOM_STATE
                )
                pipeline.fit(matrix, direction)
                probability = float(
                    np.clip(pipeline.predict_proba(current)[:, 1], 0.0, 1.0)[0]
                )
            return ArmForecast(
                self.name,
                self.label,
                probability_up=probability,
                parameters={"C": chosen},
                detail="方向の確率のみ。水準は推定しません。",
            )
        except Exception as error:
            return ArmForecast(
                self.name, self.label, status=FAILED, detail=type(error).__name__
            )


def _impute(
    matrix: NDArray[np.float64], current: NDArray[np.float64]
) -> tuple[NDArray[np.float64], NDArray[np.float64]]:
    """Fill gaps with the training window's column medians, as the pipelines do.

    Every other arm gets this from ``SimpleImputer(strategy="median")`` inside
    its pipeline. scikit-learn's random forest cannot take NaN, so it needs the
    same fill applied by hand -- and it has to be the *same* fill. The point of
    running ten families on one morning is that they see identical inputs; a
    family with its own imputation is a family whose difference cannot be
    attributed to the model.

    The first version filled with zero, which is not a neutral value: for a
    price level, a moving average or a yield it sits outside the data entirely.
    Measured on 2026-08-28 that affected 19 cells in 29,760, so it changed
    nothing then -- but missingness is not constant. The day a provider is down
    is the day it would matter, and that is the day the forest would quietly
    diverge from everything it is being compared against.

    Medians come from the training rows only, then are applied to the row being
    predicted, so the session being forecast never influences its own fill.
    """

    finite = np.where(np.isfinite(matrix), matrix, np.nan)
    medians = np.nanmedian(finite, axis=0)
    medians = np.where(np.isfinite(medians), medians, 0.0)
    filled = np.where(np.isfinite(matrix), matrix, medians)
    filled_current = np.where(np.isfinite(current), current, medians)
    return filled, filled_current


class RandomForestArm:
    """Many shallow trees; the spread between them is the distribution.

    This is the one arm whose width is earned rather than assumed: the trees
    disagree more on some inputs than others, so the band genuinely narrows and
    widens with the session. Depth is chosen on out-of-bag error, which every
    bootstrap already provides for free.
    """

    name = "random_forest"
    label = "ランダムフォレスト"

    def __init__(self, depths: tuple[int, ...] = (3, 4, 6), trees: int = 200) -> None:
        self.depths = depths
        self.trees = trees

    def _make(self, depth: int, *, oob: bool = False) -> RandomForestRegressor:
        return RandomForestRegressor(
            n_estimators=self.trees,
            max_depth=depth,
            min_samples_leaf=5,
            max_features="sqrt",
            oob_score=oob,
            bootstrap=True,
            random_state=DEFAULT_RANDOM_STATE,
            n_jobs=1,
        )

    def forecast(
        self,
        features: pd.DataFrame,
        target: NDArray[np.float64],
        latest: pd.DataFrame,
        *,
        levels: tuple[float, ...],
        n_splits: int,
    ) -> ArmForecast:
        try:
            matrix, current = _impute(_matrix(features), _matrix(latest))
            scored: list[tuple[float, int]] = []
            for depth in self.depths:
                model = self._make(depth, oob=True)
                with warnings.catch_warnings():
                    warnings.simplefilter("ignore")
                    model.fit(matrix, target)
                prediction = getattr(model, "oob_prediction_", None)
                if prediction is not None:
                    scored.append((float(np.mean((target - prediction) ** 2)), depth))
            depth = min(scored)[1] if scored else self.depths[0]
            model = self._make(depth)
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                model.fit(matrix, target)
            votes = np.array(
                [tree.predict(current)[0] for tree in model.estimators_], dtype=float
            )
            point = float(np.mean(votes))
            curve = ReturnDistribution(
                levels=levels,
                values=tuple(sorted(float(v) for v in np.quantile(votes, levels))),
                alpha=0.0,
                training_sessions=len(target),
                method="forest_tree_spread",
            )
            return ArmForecast(
                name=self.name,
                label=self.label,
                predicted_return=point,
                probability_up=float(min(max((votes > 0).mean(), 0.02), 0.98)),
                distribution=curve,
                spread_kind=ENSEMBLE,
                parameters={"max_depth": float(depth), "n_estimators": self.trees},
            )
        except Exception as error:
            return ArmForecast(
                self.name, self.label, status=FAILED, detail=type(error).__name__
            )


class GradientBoostingArm:
    """LightGBM or XGBoost, fitted straight against pinball loss.

    Boosting is the family most able to exploit a leak, so nothing here sees a
    row it should not: the number of trees is fixed rather than chosen by early
    stopping on the row being predicted, and 120 sessions is too few to spend
    on a validation split as well.

    One model is fitted per reported quantile, which is what makes the output a
    conditional distribution rather than a point with a band: the width comes
    from the data at today's feature values, and it moves.
    """

    def __init__(self, backend: str, leaves: int = 7, trees: int = 120) -> None:
        self.backend = backend
        self.name = backend
        self.label = "LightGBM" if backend == "lightgbm" else "XGBoost"
        self.leaves = leaves
        self.trees = trees

    def _fit_quantile(
        self,
        matrix: NDArray[np.float64],
        target: NDArray[np.float64],
        current: NDArray[np.float64],
        quantile: float,
    ) -> float:
        if self.backend == "lightgbm":
            import lightgbm as lgb

            model = lgb.LGBMRegressor(
                objective="quantile",
                alpha=quantile,
                num_leaves=self.leaves,
                n_estimators=self.trees,
                learning_rate=0.05,
                min_child_samples=10,
                subsample=0.8,
                subsample_freq=1,
                colsample_bytree=0.8,
                reg_lambda=1.0,
                random_state=DEFAULT_RANDOM_STATE,
                n_jobs=1,
                verbose=-1,
            )
        else:
            import xgboost as xgb

            model = xgb.XGBRegressor(
                objective="reg:quantileerror",
                quantile_alpha=quantile,
                max_depth=3,
                n_estimators=self.trees,
                learning_rate=0.05,
                min_child_weight=5,
                subsample=0.8,
                colsample_bytree=0.8,
                reg_lambda=1.0,
                random_state=DEFAULT_RANDOM_STATE,
                n_jobs=1,
                verbosity=0,
            )
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            model.fit(matrix, target)
            return float(np.asarray(model.predict(current), dtype=float)[0])

    def forecast(
        self,
        features: pd.DataFrame,
        target: NDArray[np.float64],
        latest: pd.DataFrame,
        *,
        levels: tuple[float, ...],
        n_splits: int,
    ) -> ArmForecast:
        try:
            matrix, current = _matrix(features), _matrix(latest)
            predicted = [
                self._fit_quantile(matrix, target, current, level) for level in levels
            ]
            # Boosted quantiles cross more readily than linear ones; sorting is
            # what keeps the result a distribution.
            curve = ReturnDistribution(
                levels=levels,
                values=tuple(sorted(predicted)),
                alpha=0.0,
                training_sessions=len(target),
                method=f"{self.backend}_quantile",
            )
            return ArmForecast(
                name=self.name,
                label=self.label,
                predicted_return=curve.median,
                probability_up=curve.probability_above(0.0),
                distribution=curve,
                spread_kind=CONDITIONAL,
                parameters={"n_estimators": self.trees, "leaves": self.leaves},
            )
        except ImportError as error:
            return ArmForecast(
                self.name,
                self.label,
                status=UNAVAILABLE,
                detail=f"{error.name} が導入されていません",
            )
        except Exception as error:
            return ArmForecast(
                self.name, self.label, status=FAILED, detail=type(error).__name__
            )


def default_arms(*, include_sequence: bool = False) -> tuple[Arm, ...]:
    """Every family the operator asked to be run each morning.

    ``include_sequence`` is off by default and deliberately so. The LSTM and
    the Transformer live in a second interpreter and cost about 19 minutes
    across the universe against roughly 8 seconds a ticker for everything else.
    Spending that on every morning before anything has measured whether they
    are any good would be paying for a result in advance. Turn it on in
    ``config/model.yaml`` once there is a reason to.
    """

    return _core_arms() + (sequence_family() if include_sequence else ())


def sequence_family() -> tuple[Arm, ...]:
    """LSTM and Transformer, or nothing if the sibling interpreter is absent."""

    from models.sequence import sequence_arms

    return tuple(sequence_arms())


def _core_arms() -> tuple[Arm, ...]:
    return (
        PointArm(
            "ridge",
            "Ridge回帰",
            lambda alpha=1.0: build_ridge_pipeline(alpha),
            {"alpha": (0.01, 0.1, 1.0, 10.0, 100.0)},
        ),
        LogisticArm(),
        RandomForestArm(),
        GradientBoostingArm("lightgbm"),
        GradientBoostingArm("xgboost"),
        PointArm(
            "lasso",
            "Lasso回帰",
            lambda alpha=0.01: build_lasso_pipeline(
                alpha, random_state=DEFAULT_RANDOM_STATE
            ),
            {"alpha": (0.0001, 0.001, 0.01, 0.1)},
        ),
        PointArm(
            "elastic_net",
            "ElasticNet回帰",
            lambda alpha=0.01, l1_ratio=0.5: build_elastic_net_pipeline(
                alpha, l1_ratio, random_state=DEFAULT_RANDOM_STATE
            ),
            {"alpha": (0.0001, 0.001, 0.01, 0.1), "l1_ratio": (0.1, 0.5, 0.9)},
        ),
        # Named MLP, never LSTM or Transformer. See models/neural.py: those
        # need a backend this interpreter has no distribution for, and calling
        # this one by their name would be a lie that outlives the excuse.
        PointArm(
            "mlp",
            "MLP（ニューラルネット）",
            lambda alpha=1.0, hidden=16: build_mlp_pipeline(alpha, int(hidden)),
            {"alpha": (0.1, 1.0, 10.0), "hidden": (8, 16)},
        ),
    )


def run_arms(
    features: pd.DataFrame,
    target: NDArray[np.float64],
    latest: pd.DataFrame,
    *,
    arms: tuple[Arm, ...] | None = None,
    levels: tuple[float, ...] = DEFAULT_QUANTILE_LEVELS,
    n_splits: int = 5,
    include_sequence: bool = False,
) -> tuple[ArmForecast, ...]:
    """Run every arm on one ticker-session, collecting failures as results.

    No arm is allowed to raise past this point. A morning that loses one family
    to a solver is still a morning worth publishing; a morning that publishes
    nothing because of one is not.
    """

    return tuple(
        arm.forecast(features, target, latest, levels=levels, n_splits=n_splits)
        for arm in (
            arms
            if arms is not None
            else default_arms(include_sequence=include_sequence)
        )
    )
