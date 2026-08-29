"""The universe and the threshold must be chosen from the past, not the period.

Every rule here is a way of deciding what to trade, and every one of them can
manufacture an edge if it is allowed to see the sessions it will be scored on.
These tests pin the boundary: a rule sees the days before the session and
nothing else, a ticker with too little history gets no opinion formed about it,
and rewriting the future cannot change a past decision.
"""

from __future__ import annotations

import pytest

from research.evaluation import Prediction
from research.universe import (
    MINIMUM_HISTORY,
    AdaptiveBuy,
    above_breakeven,
    all_tickers,
    backtest,
    backtest_adaptive,
    buy_agreement,
    buy_production,
    buy_regression_only,
    top_by_accuracy,
    z_at_least,
)


def _row(
    day: str,
    ticker: str,
    predicted: float,
    actual: float,
    *,
    probability: float = 0.7,
) -> Prediction:
    return Prediction(
        date=day,
        ticker=ticker,
        predicted_return=predicted,
        actual_return=actual,
        probability_up=probability,
        signal="BUY" if predicted > 0.003 and probability >= 0.6 else "NO_BUY",
        net_profit_jpy=actual * 1_000_000 if predicted > 0.003 else None,
    )


def _series(ticker: str, sessions: int, *, correct: bool) -> list[Prediction]:
    """A ticker that is either always right or always wrong about the sign."""

    rows = []
    for index in range(sessions):
        actual = 0.01 if (index % 2 == 0) else -0.01
        predicted = 0.01 if (actual > 0) == correct else -0.01
        day = f"2026-{1 + index // 28:02d}-{1 + index % 28:02d}"
        rows.append(_row(day, ticker, predicted, actual))
    return rows


# --------------------------------------------------------------------------
# The universe rules


def test_a_ticker_with_too_little_history_gets_no_opinion() -> None:
    """A threshold on 5 predictions is reading noise, not a record."""

    from research.universe import _record

    short = [_record("A", _series("A", MINIMUM_HISTORY - 1, correct=True))]

    assert above_breakeven(short) == set()
    assert z_at_least(1.5)(short) == set()
    assert top_by_accuracy(5)(short) == set()


def test_once_there_is_enough_history_a_strong_ticker_is_admitted() -> None:
    from research.universe import _record

    records = [_record("A", _series("A", MINIMUM_HISTORY + 10, correct=True))]

    assert above_breakeven(records) == {"A"}
    assert z_at_least(2.0)(records) == {"A"}


def test_the_control_admits_everything_including_the_weak() -> None:
    from research.universe import _record

    records = [
        _record("good", _series("good", 50, correct=True)),
        _record("bad", _series("bad", 50, correct=False)),
    ]

    assert all_tickers(records) == {"good", "bad"}
    assert "bad" not in above_breakeven(records)


def test_top_n_keeps_only_the_requested_count() -> None:
    from research.universe import _record

    records = [
        _record(f"T{i}", _series(f"T{i}", 50, correct=i < 3)) for i in range(6)
    ]

    assert len(top_by_accuracy(2)(records)) == 2


# --------------------------------------------------------------------------
# The backtest cannot see the session it is trading


def test_the_first_session_trades_nothing_because_there_is_no_history_yet() -> None:
    rows = [_row("2026-01-01", "A", 0.01, 0.02)]

    result = backtest(
        rows, name="t", universe=above_breakeven, buy=buy_production()
    )

    assert result.positions == 0
    assert result.total_return == pytest.approx(0.0)


def test_rewriting_later_sessions_cannot_change_an_earlier_decision() -> None:
    rows = _series("A", 60, correct=True) + _series("B", 60, correct=False)
    tampered = [
        Prediction(
            date=r.date,
            ticker=r.ticker,
            predicted_return=r.predicted_return,
            actual_return=(
                r.actual_return * -30
                if r.date > "2026-02-15"
                else r.actual_return
            ),
            probability_up=r.probability_up,
            signal=r.signal,
            net_profit_jpy=r.net_profit_jpy,
        )
        for r in rows
    ]

    before = backtest(rows, name="a", universe=z_at_least(1.5), buy=buy_production())
    after = backtest(tampered, name="b", universe=z_at_least(1.5), buy=buy_production())

    days = sorted({x.date for x in rows})
    early = [d for d in days if d <= "2026-02-15"]
    assert before.daily_returns[: len(early)] == pytest.approx(
        after.daily_returns[: len(early)]
    )


def test_every_position_pays_the_round_trip() -> None:
    rows = _series("A", 60, correct=True)

    free = backtest(
        rows,
        name="f",
        universe=all_tickers,
        buy=buy_production(),
        cost_per_position=0.0,
    )
    costed = backtest(
        rows,
        name="c",
        universe=all_tickers,
        buy=buy_production(),
        cost_per_position=0.01,
    )

    assert costed.total_return < free.total_return


# --------------------------------------------------------------------------
# The buy rules differ in the way they are meant to


def test_removing_the_classifier_admits_more_positions() -> None:
    rows = [
        _row("2026-01-01", "A", 0.01, 0.01, probability=0.4),
        _row("2026-01-02", "A", 0.01, 0.01, probability=0.4),
    ] * 30

    with_classifier = sum(1 for r in rows if buy_production()(r))
    without = sum(1 for r in rows if buy_regression_only()(r))

    assert without > with_classifier == 0


def test_the_loose_agreement_rule_trades_far_more_than_the_strict_one() -> None:
    rows = [_row("2026-01-01", "A", 0.001, 0.01, probability=0.55)]

    assert buy_agreement()(rows[0])
    assert not buy_production()(rows[0])


# --------------------------------------------------------------------------
# The adaptive threshold


def test_the_threshold_falls_back_until_there_is_enough_history() -> None:
    adaptive = AdaptiveBuy()

    assert adaptive.choose([]) in adaptive.candidates


def test_the_threshold_is_chosen_from_history_not_from_the_session() -> None:
    """The whole point: sweeping on the scored period manufactures an edge."""

    history = [
        _row("2026-01-01", "A", 0.01, 0.02, probability=0.7) for _ in range(60)
    ] + [_row("2026-01-02", "B", 0.01, -0.02, probability=0.52) for _ in range(60)]

    adaptive = AdaptiveBuy()
    chosen = adaptive.choose(history)

    # The high-probability rows were the profitable ones, so a high cut wins.
    assert chosen >= 0.60


def test_adaptive_reports_which_threshold_it_used_each_session() -> None:
    rows = _series("A", 80, correct=True)

    result, thresholds = backtest_adaptive(
        rows, name="a", universe=all_tickers, adaptive=AdaptiveBuy()
    )

    assert len(thresholds) == result.sessions
    assert set(thresholds) <= set(AdaptiveBuy().candidates)


# --------------------------------------------------------------------------
# The cost is the one the system charges, not a friendlier one


def test_the_round_trip_cost_comes_from_the_trading_config() -> None:
    """Hard-coding it once put 0.165% against a config that charges 0.200%.

    Every rule in this module is a filter on when to pay that cost, so a cost
    understated by a fifth flatters all of them at once, and flatters the
    heaviest traders most.
    """

    from data.config import load_app_config
    from research.universe import round_trip_cost

    costs = load_app_config().trading.costs
    expected = (
        2.0
        * (
            float(costs.commission_bps_per_side)
            + float(costs.slippage_bps_per_side)
        )
        / 10_000.0
    )

    assert round_trip_cost() == pytest.approx(expected)
    assert round_trip_cost() >= 0.0


def test_the_backtest_charges_the_config_cost_when_none_is_given() -> None:
    from research.universe import round_trip_cost

    rows = _series("A", 60, correct=True)

    default = backtest(rows, name="d", universe=all_tickers, buy=buy_production())
    explicit = backtest(
        rows,
        name="e",
        universe=all_tickers,
        buy=buy_production(),
        cost_per_position=round_trip_cost(),
    )

    assert default.total_return == pytest.approx(explicit.total_return)


def test_the_breakeven_bar_is_taken_from_the_window_being_scored() -> None:
    """A bar carried over from a different window is a bar for that window.

    Thirteen live sessions averaged 1.327% absolute; the 250-session record
    averages 1.1893%. The same cost against a smaller move is a higher hurdle,
    so importing the first number understated the bar by more than two points.
    """

    from research.universe import breakeven_accuracy

    big = [_row("2026-01-01", "A", 0.01, 0.02) for _ in range(50)]
    small = [_row("2026-01-01", "A", 0.01, 0.005) for _ in range(50)]

    # Stated here rather than read from the config, which is zero since
    # 2026-08-29: at no cost every bar collapses to 50% and the relationship
    # this test is about disappears.
    cost = 0.002
    assert breakeven_accuracy(small, cost=cost) > breakeven_accuracy(big, cost=cost)
    assert breakeven_accuracy(big, cost=cost) == pytest.approx(0.5 + cost / (2 * 0.02))


def test_a_window_that_never_moves_admits_nothing() -> None:
    """No move means no edge can cover any cost; the bar is not 50%."""

    from research.universe import breakeven_accuracy

    assert breakeven_accuracy([_row("2026-01-01", "A", 0.0, 0.0)]) == 1.0


# --------------------------------------------------------------------------
# The fast path must agree with the slow one


def test_the_running_tally_matches_a_full_rescan() -> None:
    """The optimisation is only allowed if it changes nothing.

    Rebuilding every record from the whole history on every session made the
    backtest quadratic and cost 16 minutes of CPU for one study. Accumulating
    instead is the same arithmetic in a different order, and this is what says
    so.
    """

    from research.universe import _record, _Tally

    rows = _series("A", 57, correct=True) + _series("A", 13, correct=False)

    tally = _Tally("A")
    for row in rows:
        tally.add(row)

    fast, slow = tally.record(), _record("A", rows)

    assert fast.predictions == slow.predictions
    assert fast.accuracy == pytest.approx(slow.accuracy)
    assert fast.z == pytest.approx(slow.z)
    assert fast.net == pytest.approx(slow.net)
    assert fast.expectancy == pytest.approx(slow.expectancy)
    assert fast.mean_abs_move == pytest.approx(slow.mean_abs_move)


def test_an_empty_tally_answers_zero_rather_than_dividing_by_nothing() -> None:
    from research.universe import _Tally

    record = _Tally("A").record()

    assert record.predictions == 0
    assert record.accuracy == 0.0
    assert record.breakeven == 1.0


# --------------------------------------------------------------------------
# Trading less is not a finding


def test_a_coin_keeping_the_same_number_is_the_control() -> None:
    """With a 0.20% round trip, any rule that trades less improves the record.

    "Beats trading everything" is therefore not evidence about a rule. The
    control has to keep the same number of positions and choose them without
    skill, and it has to come back as a band, because one draw of 130 from
    1,141 is itself noisy.
    """

    from research.universe import random_filter_control

    rows = _series("A", 80, correct=True) + _series("B", 80, correct=False)

    low, mid, high = random_filter_control(rows, keep=40, samples=300)

    assert low <= mid <= high
    assert low < high


def test_asking_to_keep_more_than_exists_returns_no_band() -> None:
    from research.universe import random_filter_control

    low, mid, high = random_filter_control(_series("A", 10, correct=True), keep=99)

    assert low != low and mid != mid and high != high  # nan


def test_the_control_pays_the_same_cost_the_rule_pays() -> None:
    """A free control would make every costed rule look bad by construction."""

    from research.universe import random_filter_control

    rows = _series("A", 60, correct=True)

    costed = random_filter_control(rows, keep=20, samples=200, cost_per_position=0.002)
    free = random_filter_control(rows, keep=20, samples=200, cost_per_position=0.0)

    assert free[1] > costed[1]


def test_every_buy_rule_names_the_pool_its_control_draws_from() -> None:
    """A control drawn from the wrong set measures the wrong thing.

    The probability rules all share a regression threshold; drawing their
    control from every prediction would credit the regression's work to the
    probability filter.
    """

    from research.universe import BUY_RULE_POOLS, BUY_RULES

    assert set(BUY_RULE_POOLS) == set(BUY_RULES)
    assert BUY_RULE_POOLS["B 回帰のみ(予測>0.3%)"] is None


# --------------------------------------------------------------------------
# The forecast threshold, chosen the same way the probability threshold is


def test_the_return_threshold_is_chosen_from_history_not_the_period() -> None:
    """Raising 0.3% to 0.8% turns -12.42% into +20.59% on the scored period.

    Adopting 0.8% on that basis is choosing a parameter by looking at the
    sessions it will be scored on, which is the one thing this study may not do.
    """

    from research.universe import AdaptiveReturnThreshold

    history = [
        _row(f"2026-01-{i:02d}", "A", 0.02, 0.03) for i in range(1, 15)
    ] + [_row(f"2026-02-{i:02d}", "B", 0.004, -0.03) for i in range(1, 15)] * 2

    chosen = AdaptiveReturnThreshold().choose(history)

    # The big forecasts were the profitable ones, so a high cut wins.
    assert chosen >= 0.008


def test_before_there_is_history_the_loosest_candidate_is_used() -> None:
    from research.universe import AdaptiveReturnThreshold

    adaptive = AdaptiveReturnThreshold()

    assert adaptive.choose([]) == adaptive.candidates[0]


def test_the_adaptive_return_backtest_reports_what_it_chose_each_session() -> None:
    from research.universe import AdaptiveReturnThreshold, backtest_adaptive_return

    rows = _series("A", 80, correct=True)

    result, chosen = backtest_adaptive_return(
        rows, name="a", adaptive=AdaptiveReturnThreshold()
    )

    assert len(chosen) == result.sessions
    assert set(chosen) <= set(AdaptiveReturnThreshold().candidates)


def test_the_candidate_grid_changes_the_answer_and_that_is_the_point() -> None:
    """A grid picked after seeing the period is hindsight the walk-forward misses.

    Measured on the real artifacts, the same rule returns +17.03% on a grid of
    four values chosen after 0.8% was seen to work, and -4.02% on a twenty-value
    grid covering the same range. The walk-forward removes hindsight about
    *which value*; it does not remove hindsight about *which values were on
    offer*, so the grid has to be reported alongside the result.
    """

    from research.universe import AdaptiveReturnThreshold, backtest_adaptive_return

    rows = _series("A", 60, correct=True) + _series("B", 60, correct=False)

    _, narrow = backtest_adaptive_return(
        rows, name="n", adaptive=AdaptiveReturnThreshold(candidates=(0.003, 0.02))
    )
    _, wide = backtest_adaptive_return(
        rows,
        name="w",
        adaptive=AdaptiveReturnThreshold(
            candidates=tuple(round(0.001 * i, 4) for i in range(1, 21))
        ),
    )

    assert set(narrow) <= {0.003, 0.02}
    assert set(wide) - {0.003, 0.02}


def test_the_control_matches_the_trading_days_not_only_the_positions() -> None:
    """Matching the position count alone favours the concentrated rule twice.

    A rule holding 472 positions across 117 sessions was being compared with
    draws spreading the same 472 across 225. More trading days means more round
    trips paid *and* more exposure to the period's drift, and neither has
    anything to do with picking well.
    """

    from research.universe import day_matched_control

    rows = _series("A", 60, correct=True) + _series("B", 60, correct=False)

    low, mid, high = day_matched_control(rows, keep=20, sessions=10, samples=200)

    assert low <= mid <= high


def test_the_day_matched_control_uses_exactly_the_sessions_asked_for() -> None:
    """Otherwise it is the same mismatch in a different place."""

    from research.universe import day_matched_control

    rows = _series("A", 40, correct=True)

    # One session is a degenerate but legal request; the band must still exist.
    low, _, high = day_matched_control(rows, keep=1, sessions=1, samples=100)

    assert low == low and high == high  # not nan


def test_asking_for_more_sessions_than_exist_returns_no_band() -> None:
    from research.universe import day_matched_control

    rows = _series("A", 10, correct=True)

    low, mid, high = day_matched_control(rows, keep=5, sessions=999)

    assert low != low and mid != mid and high != high  # nan
