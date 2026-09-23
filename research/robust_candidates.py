"""Prespecified return-only challengers for an estimated-PIT OOS study.

Both models replace only the champion's return estimate. The production
logistic probability and BUY thresholds remain fixed in the comparison. These
settings are research candidates, not production model choices.
"""

from __future__ import annotations

import warnings
from dataclasses import dataclass

import numpy as np
import pandas as pd
from sklearn.ensemble import ExtraTreesRegressor  # type: ignore[import-untyped]
from sklearn.exceptions import ConvergenceWarning  # type: ignore[import-untyped]
from sklearn.impute import SimpleImputer  # type: ignore[import-untyped]
from sklearn.linear_model import HuberRegressor  # type: ignore[import-untyped]
from sklearn.pipeline import Pipeline  # type: ignore[import-untyped]
from sklearn.preprocessing import StandardScaler  # type: ignore[import-untyped]


@dataclass(frozen=True, slots=True)
class CandidateForecast:
    name: str
    predicted_return: float
    train_mae: float


def _model(name: str) -> Pipeline:
    if name == "huber":
        return Pipeline(
            [
                ("impute", SimpleImputer(strategy="median", keep_empty_features=True)),
                ("scale", StandardScaler()),
                ("regressor", HuberRegressor(epsilon=1.35, alpha=0.01, max_iter=1000)),
            ]
        )
    if name == "extra_trees":
        return Pipeline(
            [
                ("impute", SimpleImputer(strategy="median", keep_empty_features=True)),
                (
                    "regressor",
                    ExtraTreesRegressor(
                        n_estimators=100,
                        max_depth=4,
                        min_samples_leaf=10,
                        max_features="sqrt",
                        random_state=42,
                        n_jobs=1,
                    ),
                ),
            ]
        )
    raise ValueError(f"unknown candidate: {name}")


NAMES = ("huber", "extra_trees")
BASELINE_NAMES = ("zero_return", "always_up", "historical_frequency")


def fit_candidate(
    name: str, training: pd.DataFrame, target: pd.Series, current: pd.DataFrame
) -> CandidateForecast:
    """Fit on earlier rows only and return one next-session point forecast."""

    if list(training.columns) != list(current.columns):
        raise ValueError("candidate feature columns differ from training")
    x = training.apply(pd.to_numeric, errors="coerce").to_numpy(dtype=float)
    next_x = current.apply(pd.to_numeric, errors="coerce").to_numpy(dtype=float)
    x[~np.isfinite(x)] = np.nan
    next_x[~np.isfinite(next_x)] = np.nan
    y = target.to_numpy(dtype=float)
    if len(x) != len(y) or len(next_x) != 1 or not np.isfinite(y).all():
        raise ValueError("candidate training target or current row is invalid")
    if x.shape[1] == 0 or not np.isfinite(x).any():
        raise ValueError("candidate has no observed training features")
    if not np.isfinite(next_x).all():
        raise ValueError("candidate current features missing at cutoff")

    model = _model(name)
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", ConvergenceWarning)
            model.fit(x, y)
    except ConvergenceWarning as error:
        raise ValueError("candidate fit did not converge") from error
    predicted = float(model.predict(next_x)[0])
    fitted = np.asarray(model.predict(x), dtype=float)
    if not np.isfinite(predicted) or not np.isfinite(fitted).all():
        raise ValueError("candidate produced a nonfinite prediction")
    return CandidateForecast(
        name=name,
        predicted_return=predicted,
        train_mae=float(np.mean(np.abs(fitted - y))),
    )
