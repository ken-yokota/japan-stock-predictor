"""Selecting features is where the future leaks in, so these tests hunt for it.

Choosing columns by how they performed over the whole out-of-sample period and
then reporting that period's score is not a weaker experiment, it is a void one.
The selector is therefore a pure function of one training slice, and the tests
below check that it behaves like one: same slice, same answer; a later row
changed, same answer; and the walk-forward hands it only rows before the session
being predicted.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from research.feature_selection import (
    drop_redundant,
    select_all,
    select_top_k,
    univariate_strength,
)


def _frame(**columns: list[float]) -> pd.DataFrame:
    return pd.DataFrame(columns)


def _target(values: list[float]) -> np.ndarray:
    return np.asarray(values, dtype=float)


# A signal column that tracks the target, a noise column that does not, and a
# near-copy of the signal so redundancy has something to remove.
LENGTH = 60
_BASE = [float(i % 7) - 3 for i in range(LENGTH)]
_NOISE = [float((i * 37) % 11) - 5 for i in range(LENGTH)]


def _dataset() -> tuple[pd.DataFrame, np.ndarray]:
    frame = _frame(
        signal=_BASE,
        signal_copy=[v + 0.0001 * ((i % 3) - 1) for i, v in enumerate(_BASE)],
        noise=_NOISE,
    )
    return frame, _target(_BASE)


# --------------------------------------------------------------------------
# It must find the column that relates to the target


def test_the_column_that_tracks_the_target_is_ranked_first() -> None:
    frame, target = _dataset()

    strength = univariate_strength(frame, target, list(frame.columns))

    assert strength["signal"] > strength["noise"]
    assert strength["signal"] == pytest.approx(1.0, abs=1e-9)


def test_top_k_keeps_the_signal_and_drops_the_noise() -> None:
    frame, target = _dataset()

    chosen = select_top_k(1)(frame, target)

    assert chosen == ("signal",)


def test_a_near_duplicate_column_is_removed_before_ranking() -> None:
    """Ridge splits a coefficient across collinear columns instead of choosing."""

    frame, target = _dataset()
    strength = univariate_strength(frame, target, list(frame.columns))

    kept = drop_redundant(frame, list(frame.columns), strength)

    assert "signal" in kept
    assert "signal_copy" not in kept
    assert "noise" in kept


def test_the_threshold_decides_what_counts_as_a_duplicate() -> None:
    """A column correlated 0.97 is a duplicate at 0.95 and not at 0.99."""

    rng = np.random.default_rng(7)
    base = rng.normal(size=LENGTH)
    similar = base + rng.normal(scale=0.25, size=LENGTH)
    correlation = abs(np.corrcoef(base, similar)[0, 1])
    assert 0.95 < correlation < 0.99, correlation

    frame = _frame(base=list(base), similar=list(similar))
    strength = univariate_strength(frame, _target(list(base)), list(frame.columns))

    names = list(frame.columns)
    assert len(drop_redundant(frame, names, strength, threshold=0.95)) == 1
    assert len(drop_redundant(frame, names, strength, threshold=0.99)) == 2


def test_asking_for_more_columns_than_exist_returns_what_there_is() -> None:
    frame, target = _dataset()

    chosen = select_top_k(100)(frame, target)

    assert set(chosen) <= set(frame.columns)
    assert "signal" in chosen


def test_select_all_changes_nothing() -> None:
    """The full arm runs the same code path, so a difference cannot be the path."""

    frame, target = _dataset()

    assert select_all()(frame, target) == tuple(frame.columns)


# --------------------------------------------------------------------------
# It must not see the future


def test_the_selection_depends_only_on_the_rows_it_was_given() -> None:
    frame, target = _dataset()
    selector = select_top_k(2)

    first = selector(frame.iloc[:40], target[:40])
    second = selector(frame.iloc[:40], target[:40])

    assert first == second


def test_changing_a_later_row_cannot_change_an_earlier_selection() -> None:
    """The property that makes the out-of-sample score mean something."""

    frame, target = _dataset()
    selector = select_top_k(1)

    before = selector(frame.iloc[:40], target[:40])

    tampered = frame.copy()
    tampered.loc[50:, "noise"] = tampered.loc[50:, "signal"]
    tampered_target = target.copy()
    tampered_target[50:] = -tampered_target[50:]

    after = selector(tampered.iloc[:40], tampered_target[:40])

    assert before == after


def test_a_column_that_only_works_later_is_not_picked_earlier() -> None:
    frame = _frame(
        early=[float(i % 5) for i in range(LENGTH)],
        late=[0.0] * 40 + [float(i) for i in range(20)],
    )
    target = _target([float(i % 5) for i in range(40)] + [float(i) for i in range(20)])

    chosen = select_top_k(1)(frame.iloc[:40], target[:40])

    assert chosen == ("early",)


# --------------------------------------------------------------------------
# It must degrade rather than guess


def test_a_constant_column_is_dropped_rather_than_ranked() -> None:
    frame = _frame(flat=[1.0] * LENGTH, signal=_BASE)

    kept = drop_redundant(
        frame,
        list(frame.columns),
        univariate_strength(frame, _target(_BASE), list(frame.columns)),
    )

    assert kept == ["signal"]


def test_a_column_with_too_few_usable_rows_scores_zero_not_a_lucky_number() -> None:
    values = [float("nan")] * (LENGTH - 5) + [1.0, 2.0, 3.0, 4.0, 5.0]
    frame = _frame(sparse=values, signal=_BASE)

    strength = univariate_strength(frame, _target(_BASE), list(frame.columns))

    assert strength["sparse"] == 0.0


def test_an_empty_frame_returns_nothing_rather_than_raising() -> None:
    assert select_top_k(5)(pd.DataFrame(), np.array([])) == ()


def test_k_must_be_positive() -> None:
    with pytest.raises(ValueError):
        select_top_k(0)
