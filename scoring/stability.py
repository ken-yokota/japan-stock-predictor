"""Rolling coefficient stability summaries for model explainability."""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

import numpy as np
import pandas as pd


@dataclass(frozen=True, slots=True)
class CoefficientStability:
    """Last-N standardized coefficient statistics for one feature."""

    feature_name: str
    mean_coefficient: float
    standard_deviation: float
    sign_consistency: float
    stability_score: float
    observation_count: int


def _history_frame(
    history: pd.DataFrame | Sequence[Mapping[str, float]],
) -> pd.DataFrame:
    if isinstance(history, pd.DataFrame):
        return history.copy(deep=True)
    return pd.DataFrame.from_records(list(history))


def calculate_coefficient_stability(
    history: pd.DataFrame | Sequence[Mapping[str, float]],
    *,
    lookback: int = 20,
) -> dict[str, CoefficientStability]:
    """Summarize the newest 20 rolling fits by feature.

    Sign consistency is the majority sign's share across finite observations.
    Coefficients that are consistently zero receive perfect sign consistency;
    alternating signs or high variation around a near-zero mean are penalized.
    """

    if lookback <= 0:
        raise ValueError("lookback must be positive")
    frame = _history_frame(history).tail(lookback)
    report: dict[str, CoefficientStability] = {}
    for raw_name in frame.columns:
        feature_name = str(raw_name)
        numeric = pd.to_numeric(frame[raw_name], errors="coerce").to_numpy(dtype=float)
        values = numeric[np.isfinite(numeric)]
        if len(values) == 0:
            report[feature_name] = CoefficientStability(
                feature_name=feature_name,
                mean_coefficient=0.0,
                standard_deviation=0.0,
                sign_consistency=0.0,
                stability_score=0.0,
                observation_count=0,
            )
            continue
        mean = float(np.mean(values))
        standard_deviation = float(np.std(values, ddof=0))
        positive_count = int(np.count_nonzero(values > 0.0))
        negative_count = int(np.count_nonzero(values < 0.0))
        if positive_count == 0 and negative_count == 0:
            sign_consistency = 1.0
        else:
            sign_consistency = max(positive_count, negative_count) / len(values)
        scale = float(np.mean(np.abs(values)))
        relative_variation = (
            standard_deviation / scale
            if scale > np.finfo(float).eps and standard_deviation > np.finfo(float).eps
            else 0.0
        )
        stability_score = sign_consistency / (1.0 + relative_variation)
        report[feature_name] = CoefficientStability(
            feature_name=feature_name,
            mean_coefficient=mean,
            standard_deviation=standard_deviation,
            sign_consistency=sign_consistency,
            stability_score=float(np.clip(stability_score, 0.0, 1.0)),
            observation_count=len(values),
        )
    return report


def aggregate_coefficient_stability(
    report: Mapping[str, CoefficientStability],
) -> float:
    """Return an equal-weight 0..1 stability score across observed features."""

    scores = [
        value.stability_score
        for value in report.values()
        if value.observation_count > 0 and math.isfinite(value.stability_score)
    ]
    return float(np.mean(scores)) if scores else 0.0


# UI-friendly synonym.
summarize_coefficient_stability = calculate_coefficient_stability
