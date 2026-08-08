"""Numerically safe trading and prediction evaluation metrics."""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from typing import Any, cast

import numpy as np
from numpy.typing import NDArray


@dataclass(frozen=True, slots=True)
class PerformanceMetrics:
    """Complete per-ticker out-of-sample evaluation summary.

    ``gross_loss`` is stored as a positive magnitude while ``average_loss`` and
    ``largest_loss`` retain their negative signs. ``maximum_drawdown`` is a
    positive fraction (for example, ``0.12`` means a 12% drawdown).
    """

    number_of_trades: int
    wins: int
    losses: int
    win_rate: float
    gross_profit: float
    gross_loss: float
    net_profit: float
    average_win: float
    average_loss: float
    largest_win: float
    largest_loss: float
    payoff_ratio: float
    profit_factor: float
    expectancy: float
    sharpe_ratio: float
    sortino_ratio: float
    maximum_drawdown: float
    pearson_correlation: float
    spearman_correlation: float
    direction_accuracy: float

    def as_dict(self) -> dict[str, Any]:
        """Return a serialization-friendly field mapping."""

        return asdict(self)


def _finite_vector(values: object) -> NDArray[np.float64]:
    vector = np.asarray(values, dtype=float).reshape(-1)
    return cast("NDArray[np.float64]", vector[np.isfinite(vector)])


def _safe_positive_ratio(numerator: float, denominator: float) -> float:
    if denominator > 0.0:
        return numerator / denominator
    if numerator > 0.0:
        return float("inf")
    return 0.0


def profit_factor(net_profits: object) -> float:
    """Return gross wins divided by absolute gross losses."""

    values = _finite_vector(net_profits)
    gross_profit = float(values[values > 0.0].sum())
    gross_loss = float(-values[values < 0.0].sum())
    return _safe_positive_ratio(gross_profit, gross_loss)


def expectancy(net_profits: object) -> float:
    """Return expected net profit per signal, including break-even trades."""

    values = _finite_vector(net_profits)
    if len(values) == 0:
        return 0.0
    wins = values[values > 0.0]
    losses = values[values < 0.0]
    win_rate = len(wins) / len(values)
    loss_rate = len(losses) / len(values)
    average_win = float(wins.mean()) if len(wins) else 0.0
    average_loss_magnitude = float(-losses.mean()) if len(losses) else 0.0
    return win_rate * average_win - loss_rate * average_loss_magnitude


def annualized_sharpe(returns: object, *, periods_per_year: int = 252) -> float:
    """Annualized zero-risk-rate Sharpe ratio; undefined cases return zero."""

    values = _finite_vector(returns)
    if len(values) < 2 or periods_per_year <= 0:
        return 0.0
    deviation = float(np.std(values, ddof=1))
    if deviation <= np.finfo(float).eps:
        return 0.0
    return float(np.mean(values) / deviation * math.sqrt(periods_per_year))


def annualized_sortino(returns: object, *, periods_per_year: int = 252) -> float:
    """Annualized Sortino ratio using lower-partial deviation around zero."""

    values = _finite_vector(returns)
    if len(values) == 0 or periods_per_year <= 0:
        return 0.0
    downside = np.minimum(values, 0.0)
    downside_deviation = float(np.sqrt(np.mean(np.square(downside))))
    mean_return = float(np.mean(values))
    if downside_deviation <= np.finfo(float).eps:
        return float("inf") if mean_return > 0.0 else 0.0
    return mean_return / downside_deviation * math.sqrt(periods_per_year)


def maximum_drawdown(returns: object) -> float:
    """Return maximum peak-to-trough drawdown from a compounded return path."""

    values = _finite_vector(returns)
    if len(values) == 0:
        return 0.0
    if bool(np.any(values < -1.0)):
        raise ValueError("individual returns must not be below -100%")
    equity = np.concatenate(([1.0], np.cumprod(1.0 + values)))
    peaks = np.maximum.accumulate(equity)
    drawdowns = np.divide(
        peaks - equity,
        peaks,
        out=np.zeros_like(equity),
        where=peaks > 0.0,
    )
    return float(np.max(drawdowns))


def _paired_finite(predicted: object, actual: object) -> tuple[np.ndarray, np.ndarray]:
    prediction = np.asarray(predicted, dtype=float).reshape(-1)
    outcome = np.asarray(actual, dtype=float).reshape(-1)
    if len(prediction) != len(outcome):
        raise ValueError("predicted and actual values must have equal length")
    valid = np.isfinite(prediction) & np.isfinite(outcome)
    return prediction[valid], outcome[valid]


def pearson_correlation(predicted: object, actual: object) -> float:
    """Return Pearson correlation, or zero when it is undefined."""

    prediction, outcome = _paired_finite(predicted, actual)
    if len(prediction) < 2:
        return 0.0
    if np.std(prediction) <= np.finfo(float).eps:
        return 0.0
    if np.std(outcome) <= np.finfo(float).eps:
        return 0.0
    return float(np.corrcoef(prediction, outcome)[0, 1])


def spearman_correlation(predicted: object, actual: object) -> float:
    """Return rank correlation with average ranks for ties."""

    prediction, outcome = _paired_finite(predicted, actual)
    if len(prediction) < 2:
        return 0.0
    prediction_rank = _average_ranks(prediction)
    outcome_rank = _average_ranks(outcome)
    return pearson_correlation(prediction_rank, outcome_rank)


def _average_ranks(values: np.ndarray) -> np.ndarray:
    order = np.argsort(values, kind="stable")
    ranks = np.empty(len(values), dtype=float)
    position = 0
    while position < len(values):
        end = position + 1
        while end < len(values) and values[order[end]] == values[order[position]]:
            end += 1
        average_rank = (position + end - 1) / 2.0 + 1.0
        ranks[order[position:end]] = average_rank
        position = end
    return ranks


def direction_accuracy(predicted: object, actual: object) -> float:
    """Return fraction with matching up/non-up direction classifications."""

    prediction, outcome = _paired_finite(predicted, actual)
    if len(prediction) == 0:
        return 0.0
    return float(np.mean((prediction > 0.0) == (outcome > 0.0)))


def calculate_performance_metrics(
    net_profits: object,
    *,
    trade_returns: object | None = None,
    predicted_returns: object | None = None,
    actual_returns: object | None = None,
    capital_per_trade: float = 1_000_000.0,
    periods_per_year: int = 252,
) -> PerformanceMetrics:
    """Calculate all strategy and prediction metrics without in-sample state."""

    profits = _finite_vector(net_profits)
    if not math.isfinite(capital_per_trade) or capital_per_trade <= 0.0:
        raise ValueError("capital_per_trade must be positive and finite")
    returns = (
        _finite_vector(trade_returns)
        if trade_returns is not None
        else profits / capital_per_trade
    )
    wins = profits[profits > 0.0]
    losses = profits[profits < 0.0]
    trade_count = len(profits)
    gross_profit = float(wins.sum())
    gross_loss = float(-losses.sum())
    average_win = float(wins.mean()) if len(wins) else 0.0
    average_loss = float(losses.mean()) if len(losses) else 0.0

    pearson = 0.0
    spearman = 0.0
    accuracy = 0.0
    if (predicted_returns is None) != (actual_returns is None):
        raise ValueError(
            "predicted_returns and actual_returns must be supplied together"
        )
    if predicted_returns is not None and actual_returns is not None:
        pearson = pearson_correlation(predicted_returns, actual_returns)
        spearman = spearman_correlation(predicted_returns, actual_returns)
        accuracy = direction_accuracy(predicted_returns, actual_returns)

    return PerformanceMetrics(
        number_of_trades=trade_count,
        wins=len(wins),
        losses=len(losses),
        win_rate=len(wins) / trade_count if trade_count else 0.0,
        gross_profit=gross_profit,
        gross_loss=gross_loss,
        net_profit=float(profits.sum()),
        average_win=average_win,
        average_loss=average_loss,
        largest_win=float(wins.max()) if len(wins) else 0.0,
        largest_loss=float(losses.min()) if len(losses) else 0.0,
        payoff_ratio=_safe_positive_ratio(average_win, abs(average_loss)),
        profit_factor=_safe_positive_ratio(gross_profit, gross_loss),
        expectancy=expectancy(profits),
        sharpe_ratio=annualized_sharpe(returns, periods_per_year=periods_per_year),
        sortino_ratio=annualized_sortino(returns, periods_per_year=periods_per_year),
        maximum_drawdown=maximum_drawdown(returns),
        pearson_correlation=pearson,
        spearman_correlation=spearman,
        direction_accuracy=accuracy,
    )


# Short integration alias.
compute_metrics = calculate_performance_metrics
