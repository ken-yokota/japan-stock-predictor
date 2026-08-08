from __future__ import annotations

import pandas as pd
import pytest

from scoring import (
    READABILITY_FORMULA,
    aggregate_coefficient_stability,
    calculate_coefficient_stability,
    calculate_confidence_score,
    calculate_readability_score,
    score_readability,
)


def test_coefficient_stability_uses_latest_20_fits() -> None:
    history = pd.DataFrame(
        {
            "stable_positive": [-99.0, *([0.3] * 20)],
            "alternating": [1.0, *([1.0, -1.0] * 10)],
        }
    )
    report = calculate_coefficient_stability(history)

    stable = report["stable_positive"]
    assert stable.observation_count == 20
    assert stable.mean_coefficient == pytest.approx(0.3)
    assert stable.standard_deviation == pytest.approx(0.0)
    assert stable.sign_consistency == 1.0
    assert stable.stability_score == 1.0
    assert report["alternating"].sign_consistency == 0.5
    assert aggregate_coefficient_stability(report) < 1.0


def test_readability_is_bounded_explained_and_sample_penalized() -> None:
    full = score_readability(
        profit_factor=2.0,
        win_rate=1.0,
        prediction_correlation=1.0,
        direction_accuracy=1.0,
        coefficient_stability=1.0,
        number_of_trades=20,
    )
    low_sample = score_readability(
        profit_factor=2.0,
        win_rate=1.0,
        prediction_correlation=1.0,
        direction_accuracy=1.0,
        coefficient_stability=1.0,
        number_of_trades=10,
    )

    assert full.score == 100.0
    assert full.formula == READABILITY_FORMULA
    assert not full.low_sample
    assert low_sample.score == 50.0
    assert low_sample.low_sample
    assert calculate_readability_score(
        profit_factor=2.0,
        win_rate=1.0,
        prediction_correlation=1.0,
        direction_accuracy=1.0,
        coefficient_stability=1.0,
        number_of_trades=20,
    ) == pytest.approx(100.0)

    with pytest.raises(ValueError, match="out-of-sample"):
        score_readability(
            profit_factor=1.0,
            win_rate=0.5,
            prediction_correlation=0.1,
            direction_accuracy=0.5,
            coefficient_stability=0.5,
            number_of_trades=20,
            is_out_of_sample=False,
        )


def test_confidence_is_bounded_and_penalizes_model_disagreement() -> None:
    agreeing = calculate_confidence_score(
        predicted_return=0.01,
        probability_up=0.9,
        readability_score=80.0,
        feature_coverage=1.0,
    )
    disagreeing = calculate_confidence_score(
        predicted_return=-0.01,
        probability_up=0.9,
        readability_score=80.0,
        feature_coverage=1.0,
    )

    assert 0.0 <= disagreeing < agreeing <= 100.0
    assert (
        calculate_confidence_score(
            predicted_return=float("nan"),
            probability_up=0.9,
            readability_score=80.0,
        )
        == 0.0
    )
