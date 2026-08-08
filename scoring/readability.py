"""Out-of-sample readability score with explicit normalized components."""

from __future__ import annotations

import math
from dataclasses import dataclass

READABILITY_FORMULA = (
    "(Profit Factor * 35%) + (Win Rate * 25%) + "
    "(Prediction Correlation * 20%) + (Direction Accuracy * 10%) + "
    "(Coefficient Stability * 10%), then * min(Trades / 20, 1)"
)


@dataclass(frozen=True, slots=True)
class ReadabilityWeights:
    """Product-specified component weights."""

    profit_factor: float = 0.35
    win_rate: float = 0.25
    prediction_correlation: float = 0.20
    direction_accuracy: float = 0.10
    coefficient_stability: float = 0.10

    def __post_init__(self) -> None:
        values = (
            self.profit_factor,
            self.win_rate,
            self.prediction_correlation,
            self.direction_accuracy,
            self.coefficient_stability,
        )
        if any(not math.isfinite(value) or value < 0.0 for value in values):
            raise ValueError("readability weights must be finite and non-negative")
        if not math.isclose(sum(values), 1.0, abs_tol=1e-9):
            raise ValueError("readability weights must sum to 1")


@dataclass(frozen=True, slots=True)
class ReadabilityResult:
    """Score plus components required to explain it in the UI."""

    score: float
    unpenalized_score: float
    sample_penalty: float
    low_sample: bool
    trades: int
    profit_factor_score: float
    win_rate_score: float
    prediction_correlation_score: float
    direction_accuracy_score: float
    coefficient_stability_score: float
    formula: str = READABILITY_FORMULA


def _unit_interval(value: float) -> float:
    if not math.isfinite(value):
        return 0.0
    return min(1.0, max(0.0, value))


def _profit_factor_score(value: float) -> float:
    if math.isinf(value) and value > 0.0:
        return 100.0
    if not math.isfinite(value) or value <= 0.0:
        return 0.0
    # PF 2.0 or better receives full credit; PF 1.0 receives 50 points.
    return min(value / 2.0, 1.0) * 100.0


def score_readability(
    *,
    profit_factor: float,
    win_rate: float,
    prediction_correlation: float,
    direction_accuracy: float,
    coefficient_stability: float,
    number_of_trades: int,
    is_out_of_sample: bool = True,
    minimum_sample: int = 20,
    weights: ReadabilityWeights | None = None,
) -> ReadabilityResult:
    """Calculate the 0..100 score using OOS metrics only.

    Negative or undefined correlation receives zero correlation credit.  A
    linear sample penalty prevents a handful of lucky trades from appearing as
    highly readable. Passing in-sample results is rejected explicitly.
    """

    if not is_out_of_sample:
        raise ValueError("readability must be calculated from out-of-sample results")
    if number_of_trades < 0:
        raise ValueError("number_of_trades must be non-negative")
    if minimum_sample <= 0:
        raise ValueError("minimum_sample must be positive")
    selected_weights = weights or ReadabilityWeights()
    component_pf = _profit_factor_score(profit_factor)
    component_win = _unit_interval(win_rate) * 100.0
    component_correlation = _unit_interval(prediction_correlation) * 100.0
    component_direction = _unit_interval(direction_accuracy) * 100.0
    component_stability = _unit_interval(coefficient_stability) * 100.0
    unpenalized = (
        component_pf * selected_weights.profit_factor
        + component_win * selected_weights.win_rate
        + component_correlation * selected_weights.prediction_correlation
        + component_direction * selected_weights.direction_accuracy
        + component_stability * selected_weights.coefficient_stability
    )
    sample_penalty = min(number_of_trades / minimum_sample, 1.0)
    score = min(100.0, max(0.0, unpenalized * sample_penalty))
    return ReadabilityResult(
        score=score,
        unpenalized_score=unpenalized,
        sample_penalty=sample_penalty,
        low_sample=number_of_trades < minimum_sample,
        trades=number_of_trades,
        profit_factor_score=component_pf,
        win_rate_score=component_win,
        prediction_correlation_score=component_correlation,
        direction_accuracy_score=component_direction,
        coefficient_stability_score=component_stability,
    )


def calculate_readability_score(
    *,
    profit_factor: float,
    win_rate: float,
    prediction_correlation: float,
    direction_accuracy: float,
    coefficient_stability: float,
    number_of_trades: int,
    is_out_of_sample: bool = True,
    minimum_sample: int = 20,
    weights: ReadabilityWeights | None = None,
) -> float:
    """Return only the numeric readability score for ranking integrations."""

    return score_readability(
        profit_factor=profit_factor,
        win_rate=win_rate,
        prediction_correlation=prediction_correlation,
        direction_accuracy=direction_accuracy,
        coefficient_stability=coefficient_stability,
        number_of_trades=number_of_trades,
        is_out_of_sample=is_out_of_sample,
        minimum_sample=minimum_sample,
        weights=weights,
    ).score
