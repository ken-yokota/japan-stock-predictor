"""The gap between the window and the world is the measurement, not either score.

An arm at 85% in-sample and 51% out of sample, and one at 54% and 53%, both
report "about 52% out of sample". They are not the same finding, and only the
first is a warning. These tests pin the arithmetic that tells them apart, and
the check that catches a hyperparameter grid which never actually chose.
"""

from __future__ import annotations

import pytest

from research.overfit import Setting, gap, report, settings


def _row(
    *,
    predicted: float,
    actual: float,
    train_direction: float | None = 0.55,
    train_mae: float | None = 0.011,
    parameters: dict[str, object] | None = None,
) -> dict[str, object]:
    return {
        "date": "2026-01-05",
        "ticker": "A",
        "predicted_return": predicted,
        "actual_return": actual,
        "train_direction": train_direction,
        "train_mae": train_mae,
        "estimator_parameters": parameters,
    }


# --------------------------------------------------------------------------
# The gap


def test_a_model_that_learned_its_window_is_flagged() -> None:
    rows = [
        _row(
            predicted=0.01,
            actual=(0.01 if index % 2 else -0.01),
            train_direction=0.9,
        )
        for index in range(50)
    ]

    item = gap(rows, label="tree")

    assert item is not None
    assert item.oos_direction == pytest.approx(0.5)
    assert item.direction_gap == pytest.approx(40.0)
    assert item.memorised


def test_a_model_that_is_equally_mediocre_both_sides_is_not_flagged() -> None:
    """The honest case: small in the window, small outside it."""

    rows = [
        _row(
            predicted=0.01,
            actual=(0.01 if index % 2 else -0.01),
            train_direction=0.54,
        )
        for index in range(50)
    ]

    item = gap(rows, label="ridge")

    assert item is not None
    assert item.direction_gap == pytest.approx(4.0)
    assert not item.memorised


def test_the_mae_ratio_says_how_much_larger_the_error_got() -> None:
    rows = [_row(predicted=0.0, actual=0.02, train_mae=0.01) for _ in range(20)]

    item = gap(rows, label="arm")

    assert item is not None
    assert item.oos_mae == pytest.approx(0.02)
    assert item.mae_ratio == pytest.approx(2.0)


def test_an_arm_that_recorded_no_in_sample_score_returns_nothing() -> None:
    """The linear arm has none. Reporting a zero gap would be a false all-clear."""

    rows = [_row(predicted=0.01, actual=0.01, train_direction=None, train_mae=None)]

    assert gap(rows, label="ridge") is None


def test_an_unsettled_row_is_not_scored_as_a_miss() -> None:
    rows = [
        _row(predicted=0.01, actual=0.01),
        {
            "predicted_return": 0.01,
            "actual_return": None,
            "train_direction": 0.5,
            "train_mae": 0.01,
        },
    ]

    item = gap(rows, label="arm")

    assert item is not None
    assert item.count == 1


# --------------------------------------------------------------------------
# The hyperparameters


def test_a_grid_that_always_picks_the_same_value_is_named_as_such() -> None:
    """Depth 4 in every fit means the grid stopped at 4, not that 4 was best."""

    rows = [
        _row(predicted=0.01, actual=0.01, parameters={"max_depth": 4})
        for _ in range(100)
    ]

    depth = next(item for item in settings(rows) if item.name == "max_depth")

    assert depth.dominant == ("4", 1.0)
    assert depth.pinned_at_an_edge


def test_a_grid_that_moves_between_values_is_not_flagged() -> None:
    rows = [
        _row(predicted=0.01, actual=0.01, parameters={"max_depth": 2 + index % 3})
        for index in range(90)
    ]

    depth = next(item for item in settings(rows) if item.name == "max_depth")

    assert depth.total == 90
    assert not depth.pinned_at_an_edge


def test_list_valued_parameters_are_skipped_rather_than_counted_as_strings() -> None:
    """The quantile arm records its levels; they are configuration, not a choice."""

    rows = [
        _row(
            predicted=0.01,
            actual=0.01,
            parameters={"alpha": 0.01, "quantiles": [0.1, 0.5, 0.9]},
        )
        for _ in range(10)
    ]

    names = {item.name for item in settings(rows)}

    assert names == {"alpha"}


def test_an_arm_with_no_parameters_contributes_nothing() -> None:
    assert settings([_row(predicted=0.01, actual=0.01, parameters=None)]) == []


def test_a_setting_with_no_observations_does_not_divide_by_zero() -> None:
    empty = Setting(name="max_depth", counts={})

    assert empty.total == 0
    assert empty.pinned_at_an_edge is False


# --------------------------------------------------------------------------
# The report


def test_the_report_says_which_arms_had_nothing_to_measure() -> None:
    rows = [_row(predicted=0.01, actual=0.01, train_direction=None, train_mae=None)]

    text = "\n".join(report([("ridge", rows)]))

    assert "in-sample の記録なし" in text


def test_the_report_states_that_random_folds_are_not_used() -> None:
    rows = [_row(predicted=0.01, actual=0.01, parameters={"max_depth": 3})]

    text = "\n".join(report([("tree", rows)]))

    assert "TimeSeriesSplit" in text
    assert "Random K-Fold" in text
