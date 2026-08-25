"""Calibration must fix the level, must not see the future, and must not
pretend to have improved the ranking.

The last one is the point most likely to be misread. An affine transform applied
to every ticker on a day cannot reorder them, so rank IC and top-N selection are
unchanged by construction. A report that showed calibration "improving the
model" without saying that would be claiming credit for arithmetic that cannot
have done it.
"""

from __future__ import annotations

import pytest

from research.calibration import MINIMUM_PAIRS, SLOPE_BOUNDS, calibrate
from research.evaluation import Prediction, model_quality, session_selection


def _rows(sessions: int, per_session: int, *, scale: float) -> list[Prediction]:
    """Forecasts that are ``scale`` times the outcome, so the true slope is known."""

    rows = []
    for day in range(sessions):
        for index in range(per_session):
            actual = ((index % 7) - 3) / 100
            rows.append(
                Prediction(
                    date=f"2026-{1 + day // 28:02d}-{1 + day % 28:02d}",
                    ticker=f"T{index}",
                    predicted_return=actual * scale,
                    actual_return=actual,
                )
            )
    return rows


# --------------------------------------------------------------------------
# It fixes the level


def test_a_forecast_five_times_too_large_is_brought_back_to_slope_one() -> None:
    rows = _rows(40, 22, scale=5.0)

    result = calibrate(rows)

    assert result.applied_sessions > 0
    calibrated = [r for r in result.predictions if r.date >= "2026-01-15"]
    slope = model_quality(calibrated).calibration_slope
    assert slope == pytest.approx(1.0, abs=0.01)


def test_the_fitted_slope_is_the_reciprocal_of_the_inflation() -> None:
    result = calibrate(_rows(40, 22, scale=5.0))

    assert result.mean_slope == pytest.approx(0.2, abs=0.01)


def test_an_already_calibrated_forecast_is_left_alone() -> None:
    rows = _rows(40, 22, scale=1.0)

    result = calibrate(rows)

    assert result.mean_slope == pytest.approx(1.0, abs=0.01)


# --------------------------------------------------------------------------
# It cannot reorder a day, and must not be credited with doing so


def test_the_within_day_ranking_is_untouched_by_construction() -> None:
    """An affine transform applied to every name on a day preserves their order."""

    rows = _rows(40, 22, scale=5.0)

    before = {s.date: s.rank_ic for s in session_selection(rows)}
    after = {s.date: s.rank_ic for s in session_selection(calibrate(rows).predictions)}

    for day, value in before.items():
        assert after[day] == pytest.approx(value)


def test_top_n_selection_picks_the_same_names_after_calibration() -> None:
    rows = _rows(40, 22, scale=5.0)
    calibrated = calibrate(rows).predictions

    def top(rows_in: list[Prediction], day: str) -> list[str]:
        same = [r for r in rows_in if r.date == day]
        return [
            r.ticker for r in sorted(same, key=lambda r: -r.predicted_return)[:5]
        ]

    day = sorted({r.date for r in rows})[-1]
    assert top(rows, day) == top(calibrated, day)


# --------------------------------------------------------------------------
# It cannot see the future


def test_the_first_sessions_pass_through_uncalibrated() -> None:
    """There is nothing to fit on yet, and inventing a fit would be the leak."""

    rows = _rows(40, 22, scale=5.0)

    result = calibrate(rows)

    assert result.fits[0].applied is False
    assert result.fits[0].pairs == 0


def test_a_fit_never_uses_a_pair_from_its_own_session_or_later() -> None:
    rows = _rows(40, 22, scale=5.0)

    result = calibrate(rows)

    sessions = sorted({r.date for r in rows})
    for index, fit in enumerate(result.fits):
        # Every pair available to this fit came from an earlier session.
        assert fit.pairs == index * 22
        assert fit.date == sessions[index]


def test_changing_a_later_session_cannot_change_an_earlier_calibration() -> None:
    rows = _rows(40, 22, scale=5.0)
    tampered = [
        Prediction(
            date=r.date,
            ticker=r.ticker,
            predicted_return=r.predicted_return * (-40 if r.date > "2026-01-25" else 1),
            actual_return=r.actual_return,
        )
        for r in rows
    ]

    before = calibrate(rows).fits
    after = calibrate(tampered).fits

    for a, b in zip(before, after, strict=True):
        if a.date <= "2026-01-25":
            assert a.slope == pytest.approx(b.slope)


# --------------------------------------------------------------------------
# It degrades rather than guesses


def test_too_few_pairs_means_no_calibration_rather_than_a_noisy_one() -> None:
    rows = _rows(3, 22, scale=5.0)

    result = calibrate(rows)

    assert all(not fit.applied for fit in result.fits)
    assert all(fit.pairs < MINIMUM_PAIRS for fit in result.fits)


def test_a_wild_slope_is_clamped_rather_than_applied() -> None:
    """One strange stretch must not invert or explode every forecast after it."""

    rows = []
    for day in range(40):
        for index in range(22):
            actual = ((index % 7) - 3) / 100
            rows.append(
                Prediction(
                    date=f"2026-01-{1 + day % 28:02d}",
                    ticker=f"T{index}",
                    # Predicted barely moves while actual swings: the raw fit
                    # would be enormous.
                    predicted_return=actual / 1000,
                    actual_return=actual,
                )
            )

    result = calibrate(rows)

    low, high = SLOPE_BOUNDS
    assert all(low <= fit.slope <= high for fit in result.fits)


def test_a_trailing_window_uses_only_the_most_recent_sessions() -> None:
    rows = _rows(40, 22, scale=5.0)

    result = calibrate(rows, trailing_sessions=10)

    applied = [fit for fit in result.fits if fit.applied]
    assert applied
    assert max(fit.pairs for fit in applied) <= 10 * 22


def test_every_row_survives_calibration() -> None:
    rows = _rows(40, 22, scale=5.0)

    result = calibrate(rows)

    assert len(result.predictions) == len(rows)
    assert {(r.date, r.ticker) for r in result.predictions} == {
        (r.date, r.ticker) for r in rows
    }
