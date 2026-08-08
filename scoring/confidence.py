"""Bounded confidence presentation score; never a certainty claim."""

from __future__ import annotations

import math


def _bounded(value: float, upper: float = 1.0) -> float:
    if not math.isfinite(value):
        return 0.0
    return min(upper, max(0.0, value))


def calculate_confidence_score(
    *,
    predicted_return: float,
    probability_up: float,
    readability_score: float,
    feature_coverage: float = 1.0,
    reference_return: float = 0.01,
) -> float:
    """Return a transparent 0..100 confidence indicator.

    The score combines probability conviction (35%), predicted magnitude
    (20%), historical OOS readability (25%), and available feature coverage
    (20%). Regression/classification directional disagreement halves the
    result. It is a relative data-quality indicator, not a probability that a
    trade will be profitable.
    """

    if not math.isfinite(reference_return) or reference_return <= 0.0:
        raise ValueError("reference_return must be positive and finite")
    if not math.isfinite(predicted_return) or not math.isfinite(probability_up):
        return 0.0
    probability = _bounded(probability_up)
    conviction = abs(probability - 0.5) * 2.0
    magnitude = _bounded(abs(predicted_return) / reference_return)
    readability = _bounded(readability_score / 100.0)
    coverage = _bounded(feature_coverage)
    raw = 0.35 * conviction + 0.20 * magnitude + 0.25 * readability + 0.20 * coverage
    agrees = (predicted_return > 0.0 and probability >= 0.5) or (
        predicted_return <= 0.0 and probability < 0.5
    )
    agreement_factor = 1.0 if agrees else 0.5
    return _bounded(raw * agreement_factor) * 100.0
