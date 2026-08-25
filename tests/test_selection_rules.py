"""Selection rules must be scored on sessions, and must not smuggle in a choice.

The trading layer is where a plausible-looking rule can quietly become an
in-sample fit. These tests pin the arithmetic on cases small enough to check by
hand, and pin the two properties that stop the table lying: the cost is charged
to every position, and the session is the unit of aggregation.
"""

from __future__ import annotations

import pytest

from research.evaluation import Prediction
from research.selection_rules import evaluate_rule, standard_rules


def _p(
    date: str,
    ticker: str,
    predicted: float,
    actual: float,
    *,
    signal: str = "NO_BUY",
    sector: str | None = None,
    probability: float | None = None,
) -> Prediction:
    return Prediction(
        date=date,
        ticker=ticker,
        predicted_return=predicted,
        actual_return=actual,
        probability_up=probability,
        signal=signal,
        sector=sector,
    )


ONE_DAY = [
    _p("2026-08-03", "A", 0.030, 0.020, sector="auto"),
    _p("2026-08-03", "B", 0.020, 0.010, sector="auto"),
    _p("2026-08-03", "C", 0.010, 0.000, sector="bank"),
    _p("2026-08-03", "D", -0.010, -0.020, sector="bank"),
]


def test_top_n_takes_the_strongest_forecasts() -> None:
    result = evaluate_rule(ONE_DAY, name="Top2", top_n=2, cost_per_position=0.0)

    # A and B: (0.020 + 0.010) / 2
    assert result.daily_mean_return == pytest.approx(0.015)
    assert result.positions == 2


def test_every_position_is_charged_the_round_trip_cost() -> None:
    """A rule that quietly trades for free is the easiest way to fake an edge."""

    free = evaluate_rule(ONE_DAY, name="free", top_n=2, cost_per_position=0.0)
    costed = evaluate_rule(ONE_DAY, name="costed", top_n=2, cost_per_position=0.001)

    assert costed.daily_mean_return == pytest.approx(free.daily_mean_return - 0.001)


def test_the_control_holds_the_whole_universe() -> None:
    result = evaluate_rule(ONE_DAY, name="all", top_n=None, cost_per_position=0.0)

    assert result.positions == 4
    assert result.daily_mean_return == pytest.approx((0.020 + 0.010 + 0.0 - 0.020) / 4)


def test_a_sector_cap_stops_one_bet_being_bought_four_times() -> None:
    result = evaluate_rule(
        ONE_DAY, name="cap", top_n=2, sector_cap=1, cost_per_position=0.0
    )

    # A (auto, strongest) and C (bank, strongest of its sector).
    assert result.positions == 2
    assert result.daily_mean_return == pytest.approx((0.020 + 0.000) / 2)


def test_predicted_weighting_leans_on_the_stronger_forecast() -> None:
    result = evaluate_rule(
        ONE_DAY, name="w", top_n=2, weighting="predicted", cost_per_position=0.0
    )

    # weights 0.03/0.05 and 0.02/0.05 on returns 0.020 and 0.010
    assert result.daily_mean_return == pytest.approx(0.6 * 0.020 + 0.4 * 0.010)


def test_confidence_weighting_uses_the_distance_from_a_coin_toss() -> None:
    rows = [
        _p("2026-08-03", "A", 0.03, 0.02, probability=0.70),
        _p("2026-08-03", "B", 0.02, 0.00, probability=0.60),
    ]

    result = evaluate_rule(
        rows, name="c", top_n=2, weighting="confidence", cost_per_position=0.0
    )

    # 0.20 and 0.10 above a coin toss -> two thirds and one third.
    assert result.daily_mean_return == pytest.approx(2 / 3 * 0.02 + 1 / 3 * 0.00)


def test_weights_fall_back_to_equal_when_every_forecast_is_negative() -> None:
    rows = [
        _p("2026-08-03", "A", -0.03, 0.02),
        _p("2026-08-03", "B", -0.02, 0.00),
    ]

    result = evaluate_rule(
        rows, name="w", top_n=2, weighting="predicted", cost_per_position=0.0
    )

    assert result.daily_mean_return == pytest.approx(0.01)


def test_a_short_book_earns_the_negative_of_the_move_and_pays_the_same_cost() -> None:
    result = evaluate_rule(
        ONE_DAY, name="ls", top_n=1, short_n=1, cost_per_position=0.001
    )

    # Long A (+0.020 - 0.001) and short D (+0.020 - 0.001), each on half the book.
    assert result.daily_mean_return == pytest.approx((0.019 + 0.019) / 2)


def test_the_stored_signal_rule_reproduces_production() -> None:
    rows = [
        _p("2026-08-03", "A", 0.030, 0.020, signal="BUY"),
        _p("2026-08-03", "B", 0.900, -0.500, signal="NO_BUY"),
    ]

    result = evaluate_rule(rows, name="prod", signal_only=True, cost_per_position=0.0)

    assert result.positions == 1
    assert result.daily_mean_return == pytest.approx(0.020)


def test_sessions_are_the_unit_of_aggregation_not_positions() -> None:
    """One day with ten names must not outvote nine days with one name each."""

    rows = [_p("2026-08-03", f"T{i}", 0.01, 0.10) for i in range(10)]
    rows += [_p(f"2026-08-{4 + i:02d}", "X", 0.01, -0.01) for i in range(9)]

    result = evaluate_rule(rows, name="s", top_n=None, cost_per_position=0.0)

    assert result.sessions == 10
    assert result.winning_sessions == 1
    assert result.losing_sessions == 9
    assert result.daily_mean_return == pytest.approx((0.10 + 9 * -0.01) / 10)


def test_a_day_with_no_selection_contributes_zero_not_a_gap() -> None:
    rows = [_p("2026-08-03", "A", 0.01, 0.05)]

    result = evaluate_rule(rows, name="none", signal_only=True, cost_per_position=0.0)

    assert result.sessions == 1
    assert result.total_return == pytest.approx(0.0)


def test_drawdown_is_measured_on_the_session_equity_curve() -> None:
    rows = [
        _p("2026-08-03", "A", 0.01, 0.02),
        _p("2026-08-04", "A", 0.01, -0.05),
        _p("2026-08-05", "A", 0.01, 0.01),
    ]

    result = evaluate_rule(rows, name="dd", top_n=1, cost_per_position=0.0)

    assert result.max_drawdown == pytest.approx(-0.05)


def test_the_standard_table_leads_with_the_control() -> None:
    """Any rule that cannot beat holding everything destroyed value."""

    rules = standard_rules(ONE_DAY)

    assert rules[0].name.startswith("対照")
    assert {r.name for r in rules} >= {"Top1", "Top3", "Top5", "Top10"}


def test_an_unknown_weighting_scheme_is_refused_rather_than_defaulted() -> None:
    with pytest.raises(ValueError):
        evaluate_rule(ONE_DAY, name="bad", top_n=2, weighting="nonsense")
