"""Reading a series as a level, not only as a change.

Every one of the 37 indicators is a rate of change, and a rate of change is
what the opening auction prices: last night's move is in this morning's open by
construction. How far a series now sits from its own trailing average is a
different quantity. It accumulates across days that each look unremarkable as a
return, and reverting out of it is something that happens during a session
rather than at its start.

The swap arm matters more than the addition. On a 120-session window an added
column is charged against the budget whether or not it earns anything, and
three additions have already been measured and lost here. Swapping asks the
narrower question - of two ways to read the same series, which predicts - and
cannot lose on dimensionality alone.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from research.dataset import _series_features
from research.feature_sets import PRODUCTION, IndicatorSpec, with_deviations


def _closes(values: list[float]) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "market_date": pd.date_range("2026-01-01", periods=len(values)).date,
            "close": values,
        }
    )


def test_deviation_is_the_gap_from_the_trailing_mean() -> None:
    spec = IndicatorSpec("wti", "CL=F", windows=(), deviations=(3,))
    frame = _series_features(_closes([10.0, 20.0, 30.0, 80.0]), spec)
    # Mean of the last three closes at the final row is 40/… -> (20+30+80)/3.
    expected = 80.0 / ((20.0 + 30.0 + 80.0) / 3.0) - 1.0
    assert frame["wti_deviation_3d"].iloc[-1] == pytest.approx(expected)


def test_a_flat_series_has_no_deviation() -> None:
    spec = IndicatorSpec("gold", "GC=F", windows=(), deviations=(3,))
    frame = _series_features(_closes([50.0] * 5), spec)
    assert frame["gold_deviation_3d"].dropna().abs().max() == pytest.approx(0.0)


def test_the_window_is_not_short_changed_at_the_start() -> None:
    """A mean over fewer days than asked for is a different statistic."""

    spec = IndicatorSpec("copper", "HG=F", windows=(), deviations=(4,))
    frame = _series_features(_closes([1.0, 2.0, 3.0, 4.0, 5.0]), spec)
    values = frame["copper_deviation_4d"]
    assert values.iloc[:3].isna().all(), "no value before the window is full"
    assert values.iloc[3:].notna().all()


def test_a_drift_invisible_to_daily_returns_is_visible_as_deviation() -> None:
    """The case the addition exists for.

    Five sessions of +1% each are unremarkable one at a time and add up to a
    level well above the average. Returns see five small numbers; the deviation
    sees the accumulation.
    """

    closes = [100.0 * (1.01**index) for index in range(8)]
    spec = IndicatorSpec("brent", "BZ=F", windows=(1,), deviations=(5,))
    frame = _series_features(_closes(closes), spec)

    assert frame["brent_return_1d"].iloc[-1] == pytest.approx(0.01, abs=1e-6)
    assert frame["brent_deviation_5d"].iloc[-1] > 0.019


def test_swapping_keeps_the_predictor_count_fixed() -> None:
    swapped = with_deviations(
        PRODUCTION, (20, 60), name="x", label="x", replace_returns=True
    )
    before = sum(len(s.all_column_names()) for s in PRODUCTION.indicators)
    after = sum(len(s.all_column_names()) for s in swapped.indicators)
    assert after == before, "a swap must not spend budget the baseline did not"


def test_adding_doubles_it() -> None:
    added = with_deviations(PRODUCTION, (20, 60), name="y", label="y")
    before = sum(len(s.all_column_names()) for s in PRODUCTION.indicators)
    after = sum(len(s.all_column_names()) for s in added.indicators)
    assert after == before * 2


def test_deviation_uses_the_same_close_as_the_returns() -> None:
    """It inherits the existing lag and visibility rather than adding a source."""

    spec = IndicatorSpec("vix", "^VIX", windows=(1,), deviations=(3,))
    frame = _series_features(_closes([10.0, 11.0, 12.0, 13.0]), spec)
    assert set(frame.columns) == {"market_date", "vix_return_1d", "vix_deviation_3d"}
    assert not np.isinf(frame.select_dtypes("number").to_numpy()).any()
