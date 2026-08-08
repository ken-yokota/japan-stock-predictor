from __future__ import annotations

import math

import pytest

from metrics import (
    calculate_performance_metrics,
    direction_accuracy,
    maximum_drawdown,
    pearson_correlation,
    profit_factor,
    spearman_correlation,
)


def test_complete_performance_metrics() -> None:
    profits = [100.0, -50.0, 200.0, -100.0]
    predicted = [0.01, -0.01, 0.02, -0.02]
    actual = [0.02, -0.005, 0.01, -0.03]
    result = calculate_performance_metrics(
        profits,
        trade_returns=[0.01, -0.005, 0.02, -0.01],
        predicted_returns=predicted,
        actual_returns=actual,
    )

    assert result.number_of_trades == 4
    assert result.wins == 2
    assert result.losses == 2
    assert result.win_rate == 0.5
    assert result.gross_profit == 300.0
    assert result.gross_loss == 150.0
    assert result.net_profit == 150.0
    assert result.payoff_ratio == 2.0
    assert result.profit_factor == 2.0
    assert result.expectancy == pytest.approx(37.5)
    assert result.direction_accuracy == 1.0
    assert result.pearson_correlation > 0.0
    assert result.spearman_correlation > 0.0
    assert math.isfinite(result.sharpe_ratio)
    assert result.maximum_drawdown >= 0.0


def test_drawdown_and_correlations_have_safe_edge_cases() -> None:
    assert maximum_drawdown([0.10, -0.20, 0.10]) == pytest.approx(0.20)
    assert pearson_correlation([1.0, 1.0], [2.0, 3.0]) == 0.0
    assert spearman_correlation([1.0, 2.0, 3.0], [3.0, 2.0, 1.0]) == -1.0
    assert direction_accuracy([], []) == 0.0
    with pytest.raises(ValueError):
        maximum_drawdown([-1.01])


def test_empty_and_no_loss_metrics_do_not_divide_by_zero() -> None:
    empty = calculate_performance_metrics([])
    assert empty.number_of_trades == 0
    assert empty.profit_factor == 0.0
    assert empty.expectancy == 0.0
    assert profit_factor([1.0, 2.0]) == float("inf")
