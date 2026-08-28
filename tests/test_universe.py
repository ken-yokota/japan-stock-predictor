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
