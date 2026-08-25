"""Correct the level of a forecast using only forecasts already scored.

The missing layer. Actual returns do reach the model -- yesterday's outcome is
in tomorrow's 120-session training target -- but nothing has ever looked at the
relationship between what was *predicted* and what happened. That relationship
has a name and a number: regress actual on predicted and the slope should be 1.
Measured over 250 out-of-sample sessions it is 0.18, which says the forecasts
are roughly five times too large, and no part of the system was in a position to
notice.

This adds the layer:

    raw prediction -> calibration fitted on past OOS pairs -> calibrated

The fit at session ``t`` sees only pairs from sessions strictly before ``t``.
Fitting on the whole out-of-sample period and then scoring on it would produce a
slope of exactly 1 and prove nothing, which is the failure mode this file is
most at risk of.

What it can and cannot do is worth stating before any result: an affine
transform applied to every ticker on a day cannot change their order. So the
rank IC and the top-N selection are untouched *by construction*. Anything this
improves is the level, and only the level.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np

from research.evaluation import Prediction

# Below this many scored pairs the fitted slope is noise, and applying it would
# add error rather than remove it. Those sessions pass through uncalibrated and
# are counted so the report can say how many.
MINIMUM_PAIRS = 200

# Slopes outside this range come from a window where predicted and actual barely
# co-vary; clamping keeps one strange stretch from inverting or exploding every
# forecast that follows it.
SLOPE_BOUNDS = (0.0, 3.0)


@dataclass(frozen=True, slots=True)
class Fit:
    """One session's calibration, and the sample it was fitted on."""

    date: str
    pairs: int
    intercept: float
    slope: float
    applied: bool


@dataclass(frozen=True, slots=True)
class CalibrationResult:
    predictions: list[Prediction]
    fits: list[Fit]

    @property
    def applied_sessions(self) -> int:
        return sum(1 for fit in self.fits if fit.applied)

    @property
    def mean_slope(self) -> float | None:
        used = [fit.slope for fit in self.fits if fit.applied]
        return float(np.mean(used)) if used else None


def _fit(predicted: np.ndarray, actual: np.ndarray) -> tuple[float, float] | None:
    if len(predicted) < MINIMUM_PAIRS or predicted.std() == 0:
        return None
    slope, intercept = np.polyfit(predicted, actual, 1)
    low, high = SLOPE_BOUNDS
    return float(intercept), float(min(max(slope, low), high))


def calibrate(
    predictions: Sequence[Prediction], *, trailing_sessions: int | None = None
) -> CalibrationResult:
    """Rescale each session's forecasts from the sessions that came before it.

    ``trailing_sessions`` limits the fit to a rolling window; ``None`` uses
    everything already scored, which is the larger sample and the one that
    changes least from day to day.
    """

    by_date: dict[str, list[Prediction]] = {}
    for row in predictions:
        by_date.setdefault(row.date, []).append(row)
    order = sorted(by_date)

    out: list[Prediction] = []
    fits: list[Fit] = []
    history: list[tuple[str, float, float]] = []
    for day in order:
        window = history
        if trailing_sessions is not None:
            keep = set(sorted({stamp for stamp, _, _ in history})[-trailing_sessions:])
            window = [item for item in history if item[0] in keep]
        parameters = None
        if window:
            parameters = _fit(
                np.array([p for _, p, _ in window], dtype=float),
                np.array([a for _, _, a in window], dtype=float),
            )
        rows = by_date[day]
        if parameters is None:
            fits.append(Fit(day, len(window), 0.0, 1.0, applied=False))
            out.extend(rows)
        else:
            intercept, slope = parameters
            fits.append(Fit(day, len(window), intercept, slope, applied=True))
            for row in rows:
                out.append(
                    Prediction(
                        date=row.date,
                        ticker=row.ticker,
                        predicted_return=intercept + slope * row.predicted_return,
                        actual_return=row.actual_return,
                        probability_up=row.probability_up,
                        signal=row.signal,
                        net_profit_jpy=row.net_profit_jpy,
                        gross_profit_jpy=row.gross_profit_jpy,
                        cost_jpy=row.cost_jpy,
                        sector=row.sector,
                    )
                )
        for row in rows:
            history.append((day, row.predicted_return, row.actual_return))
    return CalibrationResult(out, fits)
