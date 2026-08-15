"""Fitting relative performance instead of return, and proving it stays honest.

A daily return is alpha plus beta times the market plus noise. For these names
the market term carries most of the variance and is not knowable at the 08:30
cutoff, so a model fitting the raw return spends part of a 120-row budget on a
quantity it cannot recover. Subtracting the session's cross-sectional mean from
the target removes that component from the question being asked.

The whole idea rests on the demeaning being contemporaneous - the mean taken
off a training row belongs to that row's own session, never to a later one - so
that property is tested directly rather than argued for in a docstring.
"""

from __future__ import annotations

import inspect

import numpy as np
import pandas as pd
import pytest

from research import walk
from research.metrics import rank_ic_series


def _frame(dates: list[str], returns: list[float]) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "market_date": pd.to_datetime(dates).date,
            "intraday_return": returns,
        }
    )


def test_the_session_mean_is_taken_across_tickers() -> None:
    means = walk.session_means(
        {
            "1605": _frame(["2026-08-03", "2026-08-04"], [0.02, -0.01]),
            "9101": _frame(["2026-08-03", "2026-08-04"], [0.04, 0.03]),
        }
    )
    assert means.loc[pd.Timestamp("2026-08-03").date()] == pytest.approx(0.03)
    assert means.loc[pd.Timestamp("2026-08-04").date()] == pytest.approx(0.01)


def test_a_ticker_that_did_not_trade_does_not_count_as_a_zero_return() -> None:
    """Reading a missing return as flat drags the mean toward zero."""

    means = walk.session_means(
        {
            "1605": _frame(["2026-08-03"], [0.02]),
            "9101": _frame(["2026-08-03"], [float("nan")]),
        }
    )
    assert means.loc[pd.Timestamp("2026-08-03").date()] == pytest.approx(0.02)


def test_each_session_is_demeaned_by_its_own_mean_only() -> None:
    """The property that keeps a demeaned target free of look-ahead.

    If a later session's mean could reach a training row, the target would
    encode the future. Two sessions with deliberately different means are
    demeaned together and each must land on its own.
    """

    frames = {
        "1605": _frame(["2026-08-03", "2026-08-04"], [0.02, 0.50]),
        "9101": _frame(["2026-08-03", "2026-08-04"], [0.04, 0.70]),
    }
    means = walk.session_means(frames)
    frame = frames["1605"]
    relative = frame["intraday_return"] - frame["market_date"].map(means)

    assert relative.iloc[0] == pytest.approx(0.02 - 0.03)
    assert relative.iloc[1] == pytest.approx(0.50 - 0.60)
    # The quiet day is untouched by the violent one.
    assert relative.iloc[0] == pytest.approx(-0.01)


def test_an_empty_universe_returns_no_means_rather_than_raising() -> None:
    assert walk.session_means({}).empty


def test_ranking_is_unchanged_by_demeaning() -> None:
    """Why this arm is comparable to the others on exactly the same scale.

    Rank IC ranks within a session, and subtracting one number from every name
    in that session cannot reorder them. Without this the relative arm would be
    scored on a different quantity and the comparison would be meaningless.
    """

    generator = np.random.default_rng(20260815)
    rows = []
    for day in range(12):
        market = generator.normal(0.0, 0.02)
        for index in range(8):
            actual = market + generator.normal(0.0, 0.01)
            rows.append(
                {
                    "date": f"2026-08-{day + 1:02d}",
                    "ticker": f"T{index}",
                    "predicted_return": generator.normal(0.0, 0.01),
                    "actual_return": actual,
                }
            )
    frame = pd.DataFrame(rows)
    demeaned = frame.copy()
    demeaned["actual_return"] = frame["actual_return"] - frame.groupby("date")[
        "actual_return"
    ].transform("mean")

    raw_ic = rank_ic_series(frame)
    demeaned_ic = rank_ic_series(demeaned)
    pd.testing.assert_series_equal(raw_ic, demeaned_ic)


def test_the_relative_arm_keeps_the_walk_forward_boundary() -> None:
    source = inspect.getsource(walk.run_cross_sectional_window)
    assert 'frame["market_date"] < target_date' in source
    assert 'frame["market_date"] <= target_date' not in source


def test_the_relative_arm_fits_the_demeaned_target() -> None:
    """It must train on the relative series, not merely compute it."""

    source = inspect.getsource(walk.run_cross_sectional_window)
    assert "relative = frame[\"intraday_return\"] - frame[" in source
    assert '"market_date"].map(session_mean)' in source
    assert "relative.loc[usable.index]" in source
    # The features are deliberately untouched; only the target changes.
    assert "usable.loc[:, list(feature_names)]" in source


def test_the_production_path_is_untouched() -> None:
    source = inspect.getsource(walk.run_window)
    assert "session_means" not in source
    assert "relative" not in source
