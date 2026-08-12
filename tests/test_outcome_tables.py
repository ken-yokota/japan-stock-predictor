"""One prediction must read the same on Today, Stock Detail and History.

The three pages answer the same question - what was predicted and what
happened - so they share a builder rather than each assembling columns. An
unsettled session keeps its outcome columns empty: a zero there would read as
a flat day rather than an unknown one.
"""

from __future__ import annotations

from dashboard.outcomes import outcome_table_rows

SETTLED_HIT = {
    "prediction_date": "2026-08-12",
    "ticker": "9107",
    "signal": "BUY",
    "status": "SUCCESS",
    "predicted_intraday_return": 0.0065,
    "probability_up": 0.62,
    "actual_open": 2879.0,
    "actual_close": 2900.0,
    "actual_intraday_return": 0.0073,
    "net_profit_jpy": 2100.0,
}
SETTLED_MISS = {
    **SETTLED_HIT,
    "ticker": "9101",
    "predicted_intraday_return": 0.0044,
    "actual_intraday_return": -0.0031,
    "net_profit_jpy": -900.0,
}
UNSETTLED = {
    "prediction_date": "2026-08-13",
    "ticker": "7267",
    "signal": "HOLD",
    "status": "SUCCESS",
    "predicted_intraday_return": 0.0012,
    "probability_up": 0.51,
}


def test_a_settled_prediction_shows_the_outcome_beside_it() -> None:
    row = outcome_table_rows([SETTLED_HIT])[0]
    assert row["予測日"] == "2026-08-12"
    assert row["判定"] == "BUY"
    assert row["方向"] == "的中"
    assert row["実績リターン"] != "—"
    assert row["損益"] != "—"


def test_a_wrong_direction_is_named_as_such() -> None:
    assert outcome_table_rows([SETTLED_MISS])[0]["方向"] == "外れ"


def test_an_unsettled_session_shows_no_outcome_rather_than_a_zero() -> None:
    row = outcome_table_rows([UNSETTLED])[0]
    assert row["実績リターン"] == "—"
    assert row["方向"] == "—"
    assert row["損益"] == "—"
    assert row["予測リターン"] != "—"


def test_buy_only_keeps_the_buys() -> None:
    rows = outcome_table_rows([SETTLED_HIT, UNSETTLED], buy_only=True)
    assert [row["判定"] for row in rows] == ["BUY"]


def test_every_row_carries_the_same_columns() -> None:
    """Shared columns are what keeps the three pages consistent."""

    settled = outcome_table_rows([SETTLED_HIT])[0]
    unsettled = outcome_table_rows([UNSETTLED])[0]
    assert settled.keys() == unsettled.keys()


def test_a_flat_session_is_not_reported_as_a_hit() -> None:
    flat = {**SETTLED_HIT, "actual_intraday_return": 0.0}
    assert outcome_table_rows([flat])[0]["方向"] == "±0"


def test_the_buy_hit_ratio_counts_every_signal_in_the_denominator() -> None:
    """A signal whose day has not closed is still a signal that was issued."""

    from dashboard.outcomes import buy_hit_ratio

    rows = [
        SETTLED_HIT,                                   # BUY, rose
        SETTLED_MISS,                                  # BUY, fell
        {**SETTLED_HIT, "actual_intraday_return": None},  # BUY, unsettled
        UNSETTLED,                                     # not a BUY
    ]
    positive, issued, unsettled = buy_hit_ratio(rows)
    assert (positive, issued, unsettled) == (1, 3, 1)


def test_a_flat_close_does_not_count_as_positive() -> None:
    from dashboard.outcomes import buy_hit_ratio

    flat = {**SETTLED_HIT, "actual_intraday_return": 0.0}
    assert buy_hit_ratio([flat]) == (0, 1, 0)


def test_no_buys_gives_a_zero_denominator_rather_than_a_crash() -> None:
    from dashboard.outcomes import buy_hit_ratio

    assert buy_hit_ratio([UNSETTLED]) == (0, 0, 0)
