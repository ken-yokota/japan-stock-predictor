"""Genuine linear coefficients, contributions and past-fit stability."""

from __future__ import annotations

import numpy as np


def linear_diagnostics(
    coefficients: dict[str, float],
    means: dict[str, float],
    scales: dict[str, float],
    current: dict[str, float],
    history: list[dict[str, float]],
) -> dict[str, object]:
    ranks = {
        name: rank
        for rank, name in enumerate(
            sorted(coefficients, key=lambda n: (-abs(coefficients[n]), n)), 1
        )
    }
    historical_ranks = [
        {
            name: rank
            for rank, name in enumerate(sorted(row, key=lambda n: (-abs(row[n]), n)), 1)
        }
        for row in history
    ]
    rows = []
    for name, coefficient in coefficients.items():
        scale = scales.get(name)
        value = current.get(name)
        z = (
            (value - means[name]) / scale
            if value is not None and np.isfinite(value) and scale and name in means
            else None
        )
        # Include today's fit; callers supply only strictly earlier fits of the
        # same ticker, task and configuration version.
        series = np.asarray(
            [h[name] for h in history if name in h] + [coefficient], dtype=float
        )
        rank_series = [h[name] for h in historical_ranks if name in h] + [ranks[name]]
        positive, negative = float(np.mean(series > 0)), float(np.mean(series < 0))
        rows.append(
            {
                "feature": name,
                "standardized_coefficient": coefficient,
                "raw_coefficient": coefficient / scale if scale else None,
                "sign": int(np.sign(coefficient)),
                "absolute_rank": ranks[name],
                "today_standardized_value": z,
                "today_contribution": coefficient * z if z is not None else None,
                "rolling_mean": float(series.mean()),
                "rolling_sd": float(series.std()),
                "positive_ratio": positive,
                "negative_ratio": negative,
                "sign_stability": max(positive, negative),
                "rank_sd": float(np.std(rank_series)),
                "rolling_fits": len(series),
                "stability_status": "LOW_SAMPLE" if len(series) < 20 else "OBSERVED",
            }
        )
    finite_z = [
        abs(float(r["today_standardized_value"]))
        for r in rows
        if isinstance(r["today_standardized_value"], int | float)
    ]
    return {
        "kind": "LINEAR_COEFFICIENTS",
        "features": rows,
        "max_absolute_z": max(finite_z) if finite_z else None,
        "ood_status": "RESEARCH_DIAGNOSTIC_NOT_CALIBRATED",
        "history_fits": len(history),
    }
