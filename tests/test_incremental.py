"""A group earns its place by changing the record, in one direction or both.

The two tests point opposite ways and are meant to: removing a group should
hurt, adding it should help. A group that fails one and passes the other is not
a contradiction, it is the finding -- redundancy in one direction, combination
in the other -- so the arithmetic that decides each verdict is pinned here, and
so is the pairing that makes the sign test informative.
"""

from __future__ import annotations

import pytest

from research.evaluation import Prediction
from research.incremental import _paired_sign_test, compare, report

DAYS = [f"2026-04-{day:02d}" for day in range(1, 26)]
TICKERS = ("A", "B", "C", "D")


def _rows(correct: dict[tuple[str, str], bool]) -> list[Prediction]:
    """One prediction per (day, ticker); ``correct`` says which got the sign right."""

    out = []
    for day in DAYS:
        for index, ticker in enumerate(TICKERS):
            actual = 0.01 if index % 2 == 0 else -0.01
            right = correct.get((day, ticker), True)
            predicted = actual if right else -actual
            out.append(
                Prediction(
                    date=day,
                    ticker=ticker,
                    predicted_return=predicted,
                    actual_return=actual,
                    probability_up=0.6,
                    signal="NO_BUY",
                )
            )
    return out


def _all(right: bool) -> dict[tuple[str, str], bool]:
    return {(day, ticker): right for day in DAYS for ticker in TICKERS}


# --------------------------------------------------------------------------
# The sign test


def test_only_the_predictions_they_disagreed_on_are_counted() -> None:
    """Both right and both wrong say nothing about which arm is better."""

    left = [True, True, False, False, True, False]
    right = [True, False, True, False, False, False]

    # Disagreements: index 1 (left wins), 2 (right wins), 4 (left wins).
    assert _paired_sign_test(left, right) == pytest.approx(
        _paired_sign_test([True, False, True], [False, True, False])
    )


def test_two_arms_that_never_disagree_have_no_p_value() -> None:
    assert _paired_sign_test([True, False], [True, False]) is None


def test_a_one_sided_run_of_disagreements_is_significant() -> None:
    left = [True] * 40
    right = [False] * 40

    p = _paired_sign_test(left, right)

    assert p is not None
    assert p < 0.001


# --------------------------------------------------------------------------
# Ablation


def test_removing_a_group_that_mattered_shows_a_negative_delta() -> None:
    control = _rows(_all(True))
    without = _rows({**_all(True), **{(DAYS[0], "A"): False, (DAYS[1], "A"): False}})

    change = compare(without, control, group="copper", kind="ablation")

    assert change.direction_delta_pp < 0
    assert change.helped


def test_removing_a_group_that_carried_nothing_is_not_credited() -> None:
    control = _rows(_all(True))

    change = compare(list(control), control, group="baltic", kind="ablation")

    assert change.direction_delta_pp == pytest.approx(0.0)
    assert not change.helped


# --------------------------------------------------------------------------
# Incremental value


def test_adding_a_group_that_helps_shows_a_positive_delta() -> None:
    control = _rows({**_all(True), **{(DAYS[0], "A"): False, (DAYS[1], "A"): False}})
    with_group = _rows(_all(True))

    change = compare(with_group, control, group="copper", kind="incremental")

    assert change.direction_delta_pp > 0
    assert change.helped


def test_the_same_delta_means_opposite_things_in_the_two_directions() -> None:
    """An ablation that improves the record is evidence *against* the group."""

    control = _rows({**_all(True), **{(DAYS[0], "A"): False}})
    better = _rows(_all(True))

    ablation = compare(better, control, group="g", kind="ablation")
    incremental = compare(better, control, group="g", kind="incremental")

    assert ablation.direction_delta_pp == incremental.direction_delta_pp
    assert not ablation.helped
    assert incremental.helped


# --------------------------------------------------------------------------
# Only shared predictions


def test_an_arm_that_failed_on_some_sessions_is_compared_only_where_both_ran() -> None:
    control = _rows(_all(True))
    partial = [row for row in control if row.date != DAYS[0]]

    change = compare(partial, control, group="g", kind="ablation")

    assert change.pairs == len(partial)
    assert change.direction_delta_pp == pytest.approx(0.0)


def test_no_overlap_returns_zeroes_rather_than_an_invented_delta() -> None:
    left = _rows(_all(True))
    right = [
        Prediction(
            date="2030-01-01",
            ticker="Z",
            predicted_return=0.01,
            actual_return=0.01,
            probability_up=0.5,
            signal="NO_BUY",
        )
    ]

    change = compare(left, right, group="g", kind="ablation")

    assert change.pairs == 0
    assert change.paired_p is None


# --------------------------------------------------------------------------
# The report


def test_the_family_is_corrected_as_one_not_test_by_test() -> None:
    """Twenty-one comparisons produce a small p by construction."""

    control = _rows(_all(True))
    noisy = [
        compare(
            _rows({**_all(True), **{(DAYS[index % 25], "A"): False}}),
            control,
            group=f"g{index}",
            kind="ablation",
        )
        for index in range(20)
    ]

    text = "\n".join(report(noisy, []))

    assert "BH-FDR補正" in text
    assert "20回の比較" in text


def test_a_disagreement_between_the_two_directions_is_explained() -> None:
    control = _rows(_all(True))
    unchanged = compare(list(control), control, group="brent", kind="ablation")
    helps_alone = compare(
        _rows(_all(True)),
        _rows({**_all(True), **{(DAYS[0], "A"): False}}),
        group="brent",
        kind="incremental",
    )

    text = "\n".join(report([unchanged], [helps_alone]))

    assert "冗長" in text


def test_agreement_in_both_directions_says_so_rather_than_leaving_it_blank() -> None:
    control = _rows(_all(True))
    same = compare(list(control), control, group="g", kind="ablation")
    also_same = compare(list(control), control, group="g", kind="incremental")

    text = "\n".join(report([same], [also_same]))

    assert "全グループで一致" in text
