"""Daily accuracy, cumulative result, and the per-group breakdowns.

The thing worth pinning here is the zero point. A direction call has two
outcomes, so 50% is what knowing nothing scores; every series on this page is
plotted as distance from that. Getting the sign or the centre wrong would turn
a coin flip into an apparent edge, which is the one mistake this page exists to
prevent.
"""

from __future__ import annotations

from typing import Any

import pytest

from dashboard.history_progress import (
    COIN_FLIP,
    GROUP_BY_SECTOR,
    GROUP_BY_TICKER,
    accuracy_series,
    cumulative_profit,
    grouped_accuracy,
    grouped_returns,
    pivot,
)


def _row(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "date": "2026-09-07",
        "ticker": "7203",
        "signal": "BUY",
        "predicted_return": 0.02,
        "actual_return": 0.03,
        "direction_correct": True,
        "net_profit_jpy": 1000.0,
    }
    base.update(overrides)
    return base


# --- the zero point ---------------------------------------------------------


def test_a_coin_flip_is_the_origin() -> None:
    assert COIN_FLIP == 0.5


def test_the_operators_worked_example() -> None:
    # "買いの的中率が75%だったらその日は+25%".
    rows = [
        _row(ticker="7203", direction_correct=True),
        _row(ticker="7267", direction_correct=True),
        _row(ticker="7201", direction_correct=True),
        _row(ticker="7269", direction_correct=False),
    ]
    (point,) = accuracy_series(rows, buy_only=True)
    assert point.accuracy == pytest.approx(0.75)
    assert point.deviation == pytest.approx(0.25)
    assert point.count == 4


def test_a_day_worse_than_guessing_goes_below_zero() -> None:
    rows = [
        _row(ticker="7203", direction_correct=False),
        _row(ticker="7267", direction_correct=False),
    ]
    (point,) = accuracy_series(rows, buy_only=True)
    assert point.deviation == pytest.approx(-0.5)


# --- what counts ------------------------------------------------------------


def test_an_unsettled_row_is_not_a_miss() -> None:
    # A day that has not closed is not a wrong prediction. Counting it either
    # way would move the line for a reason unrelated to the model.
    rows = [
        _row(direction_correct=True),
        _row(ticker="7267", actual_return=None, direction_correct=None),
    ]
    (point,) = accuracy_series(rows, buy_only=False)
    assert point.count == 1
    assert point.accuracy == pytest.approx(1.0)


def test_buy_only_and_all_are_different_questions() -> None:
    rows = [
        _row(ticker="7203", signal="BUY", direction_correct=True),
        _row(ticker="7267", signal="NO_BUY", direction_correct=False),
        _row(ticker="7201", signal="NO_BUY", direction_correct=False),
    ]
    (buy,) = accuracy_series(rows, buy_only=True)
    (every,) = accuracy_series(rows, buy_only=False)
    assert buy.accuracy == pytest.approx(1.0)
    assert buy.count == 1
    assert every.accuracy == pytest.approx(1 / 3)
    assert every.count == 3


def test_series_are_oldest_first() -> None:
    rows = [
        _row(date="2026-09-07"),
        _row(date="2026-09-01"),
        _row(date="2026-09-04"),
    ]
    assert [point.date for point in accuracy_series(rows, buy_only=False)] == [
        "2026-09-01",
        "2026-09-04",
        "2026-09-07",
    ]


def test_no_settled_rows_is_an_empty_series_not_a_zero() -> None:
    assert accuracy_series([], buy_only=False) == []
    assert (
        accuracy_series(
            [_row(actual_return=None, direction_correct=None)], buy_only=True
        )
        == []
    )


# --- cumulative result ------------------------------------------------------


def test_profit_accumulates_across_sessions() -> None:
    rows = [
        _row(date="2026-09-01", net_profit_jpy=1000.0),
        _row(date="2026-09-01", ticker="7267", net_profit_jpy=-400.0),
        _row(date="2026-09-04", net_profit_jpy=250.0),
    ]
    assert cumulative_profit(rows) == [("2026-09-01", 600.0), ("2026-09-04", 850.0)]


def test_unsettled_rows_do_not_contribute_to_the_running_total() -> None:
    rows = [
        _row(date="2026-09-01", net_profit_jpy=1000.0),
        _row(
            date="2026-09-04",
            actual_return=None,
            direction_correct=None,
            net_profit_jpy=99999.0,
        ),
    ]
    assert cumulative_profit(rows) == [("2026-09-01", 1000.0)]


# --- groupings --------------------------------------------------------------


def test_sector_grouping_folds_tickers_together() -> None:
    rows = [
        _row(ticker="7203", direction_correct=True),
        _row(ticker="7267", direction_correct=False),
        _row(ticker="8306", direction_correct=True),
    ]
    points = grouped_accuracy(rows, grouping=GROUP_BY_SECTOR)
    keyed = {point["group"]: point for point in points}
    assert keyed["自動車"]["accuracy"] == pytest.approx(0.5)
    assert keyed["自動車"]["deviation"] == pytest.approx(0.0)
    assert keyed["自動車"]["count"] == 2
    assert keyed["金融"]["accuracy"] == pytest.approx(1.0)


def test_ticker_grouping_keeps_them_apart() -> None:
    rows = [
        _row(ticker="7203", direction_correct=True),
        _row(ticker="7267", direction_correct=False),
    ]
    points = grouped_accuracy(rows, grouping=GROUP_BY_TICKER)
    assert len(points) == 2
    assert all(point["count"] == 1 for point in points)


def test_the_breakdown_covers_every_prediction_not_only_the_buys() -> None:
    rows = [
        _row(ticker="7203", signal="BUY", direction_correct=True),
        _row(ticker="7267", signal="NO_BUY", direction_correct=True),
    ]
    points = grouped_accuracy(rows, grouping=GROUP_BY_SECTOR)
    assert points[0]["count"] == 2


def test_grouped_returns_average_the_same_rows_on_both_sides() -> None:
    # A gap between the two lines has to be the model's bias, not one side
    # quietly including sessions the other dropped.
    rows = [
        _row(ticker="7203", predicted_return=0.02, actual_return=0.01),
        _row(ticker="7267", predicted_return=0.04, actual_return=0.03),
    ]
    (point,) = grouped_returns(rows, grouping=GROUP_BY_SECTOR)
    assert point["predicted_mean"] == pytest.approx(0.03)
    assert point["actual_mean"] == pytest.approx(0.02)
    assert point["count"] == 2


def test_an_unknown_grouping_is_refused() -> None:
    with pytest.raises(ValueError):
        grouped_accuracy([_row()], grouping="なにか")
    with pytest.raises(ValueError):
        grouped_returns([_row()], grouping="なにか")


def test_pivot_produces_one_entry_per_date_and_group() -> None:
    rows = [
        _row(date="2026-09-01", ticker="7203", direction_correct=True),
        _row(date="2026-09-01", ticker="8306", direction_correct=False),
        _row(date="2026-09-04", ticker="7203", direction_correct=True),
    ]
    table = pivot(grouped_accuracy(rows, grouping=GROUP_BY_SECTOR), value="deviation")
    assert set(table) == {"2026-09-01", "2026-09-04"}
    assert table["2026-09-01"]["自動車"] == pytest.approx(0.5)
    assert table["2026-09-01"]["金融"] == pytest.approx(-0.5)
    assert set(table["2026-09-04"]) == {"自動車"}
