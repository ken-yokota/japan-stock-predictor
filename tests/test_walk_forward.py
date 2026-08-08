from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from backtest import WalkForwardConfig, assert_walk_forward_oos, walk_forward_validate
from models import ModelTrainingConfig


def _walk_config() -> WalkForwardConfig:
    return WalkForwardConfig(
        model=ModelTrainingConfig(
            window_size=20,
            minimum_training_sessions=10,
            time_series_splits=2,
            ridge_alphas=(1.0,),
            logistic_cs=(1.0,),
            random_state=42,
        )
    )


def _history() -> pd.DataFrame:
    x_value = np.sin(np.arange(24) / 2.0)
    return pd.DataFrame(
        {
            "ticker": "1605",
            "market_date": pd.date_range("2026-01-01", periods=24, freq="B"),
            "x": x_value,
            "intraday_return": x_value * 0.01,
        }
    )


def test_walk_forward_predictions_are_strictly_out_of_sample() -> None:
    result = walk_forward_validate(
        _history(), feature_names=("x",), config=_walk_config()
    )

    assert len(result) == 4
    assert set(result["status"]) == {"OK"}
    assert (result["training_end"] < result["prediction_date"]).all()
    assert set(result["training_sessions"]) == {20}
    assert_walk_forward_oos(result)


def test_future_targets_cannot_change_first_walk_forward_prediction() -> None:
    original = _history()
    changed_future = original.copy()
    changed_future.loc[21:, "intraday_return"] = 999.0

    baseline = walk_forward_validate(
        original, feature_names=("x",), config=_walk_config()
    )
    changed = walk_forward_validate(
        changed_future, feature_names=("x",), config=_walk_config()
    )

    assert changed.iloc[0]["predicted_return"] == pytest.approx(
        baseline.iloc[0]["predicted_return"]
    )
    assert changed.iloc[0]["probability_up"] == pytest.approx(
        baseline.iloc[0]["probability_up"]
    )


def test_walk_forward_rejects_ambiguous_duplicate_sessions() -> None:
    duplicated = pd.concat([_history(), _history().iloc[[0]]], ignore_index=True)
    with pytest.raises(ValueError, match="duplicate"):
        walk_forward_validate(duplicated, feature_names=("x",), config=_walk_config())
