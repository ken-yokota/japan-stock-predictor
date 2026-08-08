from __future__ import annotations

import numpy as np
import pandas as pd
import pytest
from sklearn.impute import SimpleImputer  # type: ignore[import-untyped]
from sklearn.linear_model import (  # type: ignore[import-untyped]
    LogisticRegression,
    Ridge,
)
from sklearn.preprocessing import StandardScaler  # type: ignore[import-untyped]

from models import ModelTrainingConfig, train_models_by_ticker, train_ticker_model


def _training_config(window_size: int = 120) -> ModelTrainingConfig:
    return ModelTrainingConfig(
        window_size=window_size,
        minimum_training_sessions=10,
        time_series_splits=3,
        ridge_alphas=(0.1, 1.0),
        logistic_cs=(0.1, 1.0),
        random_state=123,
    )


def test_ticker_model_uses_pipeline_and_last_120_sessions_deterministically() -> None:
    rng = np.random.default_rng(11)
    features = pd.DataFrame(
        {
            "market": rng.normal(size=145),
            "fx": rng.normal(size=145),
        }
    )
    features.loc[10, "fx"] = np.nan
    targets = 0.012 * features["market"].fillna(0.0) - 0.004 * features["fx"].fillna(
        0.0
    )

    first = train_ticker_model("1605", features, targets, config=_training_config())
    second = train_ticker_model("1605", features, targets, config=_training_config())
    changed_outside_window = targets.copy()
    changed_outside_window.iloc[:25] = 999.0
    without_old_history = train_ticker_model(
        "1605", features, changed_outside_window, config=_training_config()
    )
    current = pd.DataFrame({"market": [0.8], "fx": [np.nan]})

    assert first.training_sessions == 120
    assert isinstance(first.regressor.named_steps["imputer"], SimpleImputer)
    assert isinstance(first.regressor.named_steps["scaler"], StandardScaler)
    assert isinstance(first.regressor.named_steps["model"], Ridge)
    assert first.classifier is not None
    assert isinstance(first.classifier.named_steps["model"], LogisticRegression)
    assert first.predict_one(current).predicted_return == pytest.approx(
        second.predict_one(current).predicted_return
    )
    assert first.predict_one(current).probability_up == pytest.approx(
        second.predict_one(current).probability_up
    )
    assert first.predict_one(current).predicted_return == pytest.approx(
        without_old_history.predict_one(current).predicted_return
    )
    assert set(first.regression_coefficients()) == {"market", "fx"}
    assert isinstance(first.regression_intercept(), float)
    assert isinstance(first.classification_intercept(), float)
    regression_scaler = first.scaler_statistics("regression")
    classification_scaler = first.scaler_statistics("classification")
    assert regression_scaler is not None
    assert classification_scaler is not None
    assert set(regression_scaler.means) == {"market", "fx"}
    assert set(regression_scaler.scales) == {"market", "fx"}
    assert first.classification_constant_probability() is None


def test_single_class_training_uses_safe_constant_probability() -> None:
    features = pd.DataFrame({"x": np.arange(30.0)})
    targets = np.full(30, 0.01)
    model = train_ticker_model(
        "9101", features, targets, config=_training_config(window_size=20)
    )

    assert model.classifier is None
    assert model.predict_one(pd.DataFrame({"x": [31.0]})).probability_up == 1.0
    assert model.classification_coefficients() == {"x": 0.0}
    assert model.classification_intercept() is None
    assert model.scaler_statistics("classification") is None
    assert model.classification_constant_probability() == 1.0


def test_models_are_trained_independently_by_ticker() -> None:
    dates = pd.date_range("2026-01-01", periods=30, freq="B")
    feature = np.linspace(-1.0, 1.0, 30)
    frame = pd.concat(
        [
            pd.DataFrame(
                {
                    "ticker": "A",
                    "market_date": dates,
                    "x": feature,
                    "intraday_return": feature * 0.01,
                }
            ),
            pd.DataFrame(
                {
                    "ticker": "B",
                    "market_date": dates,
                    "x": feature,
                    "intraday_return": feature * -0.01,
                }
            ),
        ],
        ignore_index=True,
    )
    trained = train_models_by_ticker(
        frame,
        feature_names=("x",),
        config=_training_config(window_size=20),
    )

    assert set(trained) == {"A", "B"}
    assert trained["A"].regression_coefficients()["x"] > 0.0
    assert trained["B"].regression_coefficients()["x"] < 0.0
