"""Score the selection rule, and refuse to over-read a handful of trades.

Rank IC says the ordering is right; it cannot say that acting on the ordering
pays. This module is the other half, and these pin the parts most likely to
flatter a candidate: costs must actually be subtracted, a day holding one name
must not be compared against a day holding five as though the exposure matched,
and a window with no losing session must not report infinite risk-adjusted
return.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from research.trading_metrics import StrategyResult, evaluate, evaluate_all


def _rows(records: list[tuple[str, str, float, float, str]]) -> pd.DataFrame:
    return pd.DataFrame(
        records,
        columns=[
            "date",
            "ticker",
            "predicted_return",
            "actual_return",
            "signal",
        ],
    )


def test_top_k_takes_the_highest_predicted_names_each_session() -> None:
    frame = _rows(
        [
            ("2026-08-03", "A", 0.03, 0.05, "NO_TRADE"),
            ("2026-08-03", "B", 0.02, -0.01, "NO_TRADE"),
            ("2026-08-03", "C", 0.01, 0.02, "NO_TRADE"),
            ("2026-08-04", "A", 0.01, 0.01, "NO_TRADE"),
            ("2026-08-04", "B", 0.04, 0.03, "NO_TRADE"),
            ("2026-08-04", "C", 0.02, -0.02, "NO_TRADE"),
        ]
    )
    result = evaluate(frame, rule="top1", top_k=1)
    assert result.trades == 2
    # A on the 3rd (+0.05) and B on the 4th (+0.03).
    assert result.net_profit == pytest.approx(0.08)
    assert result.win_rate == pytest.approx(1.0)


def test_the_threshold_rule_uses_the_stored_signal() -> None:
    frame = _rows(
        [
            ("2026-08-03", "A", 0.03, 0.05, "BUY"),
            ("2026-08-03", "B", 0.02, -0.01, "NO_TRADE"),
        ]
    )
    result = evaluate(frame, rule="threshold")
    assert result.trades == 1
    assert result.net_profit == pytest.approx(0.05)


def test_costs_are_charged_on_both_legs() -> None:
    """A rule that is profitable only before costs is not profitable."""

    frame = _rows([("2026-08-03", "A", 0.01, 0.0010, "BUY")])
    free = evaluate(frame, rule="threshold")
    charged = evaluate(frame, rule="threshold", commission_bps=5.0, slippage_bps=5.0)

    assert free.net_profit == pytest.approx(0.0010)
    # 2 x (5 + 5) bps = 20bps = 0.0020, which turns the winner into a loser.
    assert charged.net_profit == pytest.approx(-0.0010)
    assert charged.win_rate == 0.0


def test_a_session_is_one_observation_however_many_names_it_held() -> None:
    """Otherwise a five-name day counts five times the exposure it carried."""

    frame = _rows(
        [
            ("2026-08-03", "A", 0.03, 0.10, "BUY"),
            ("2026-08-03", "B", 0.02, 0.00, "BUY"),
            ("2026-08-04", "C", 0.04, 0.02, "BUY"),
        ]
    )
    result = evaluate(frame, rule="threshold")
    # Daily returns are 0.05 (the mean of 0.10 and 0.00) and 0.02.
    assert result.daily_returns == pytest.approx((0.05, 0.02))


def test_profit_factor_and_expectancy() -> None:
    frame = _rows(
        [
            ("2026-08-03", "A", 0.03, 0.06, "BUY"),
            ("2026-08-04", "B", 0.03, -0.02, "BUY"),
            ("2026-08-05", "C", 0.03, 0.02, "BUY"),
        ]
    )
    result = evaluate(frame, rule="threshold")
    assert result.gross_profit == pytest.approx(0.08)
    assert result.gross_loss == pytest.approx(0.02)
    assert result.profit_factor == pytest.approx(4.0)
    assert result.expectancy == pytest.approx((0.06 - 0.02 + 0.02) / 3)


def test_max_drawdown_is_the_deepest_fall_not_the_final_loss() -> None:
    frame = _rows(
        [
            ("2026-08-03", "A", 0.03, 0.10, "BUY"),
            ("2026-08-04", "A", 0.03, -0.08, "BUY"),
            ("2026-08-05", "A", 0.03, 0.05, "BUY"),
        ]
    )
    result = evaluate(frame, rule="threshold")
    # Path is +0.10, +0.02, +0.07: the trough is 0.08 below the peak.
    assert result.max_drawdown == pytest.approx(-0.08)


def test_a_window_without_a_losing_session_is_not_infinitely_good() -> None:
    """No downside in 3 sessions is a fact about the window, not the strategy."""

    frame = _rows(
        [
            ("2026-08-03", "A", 0.03, 0.01, "BUY"),
            ("2026-08-04", "A", 0.03, 0.02, "BUY"),
            ("2026-08-05", "A", 0.03, 0.03, "BUY"),
        ]
    )
    result = evaluate(frame, rule="threshold")
    assert result.sortino == 0.0
    assert np.isfinite(result.sharpe)


def test_a_rule_that_selects_nothing_reports_zero_rather_than_raising() -> None:
    frame = _rows([("2026-08-03", "A", 0.03, 0.05, "NO_TRADE")])
    result = evaluate(frame, rule="threshold")
    assert result.trades == 0
    assert result.net_profit == 0.0
    assert not result.is_measurable


def test_small_samples_are_flagged_rather_than_concluded_from() -> None:
    """Below about 20 trades, say the count and stop."""

    few = StrategyResult.empty("top1", 63, trades=13)
    enough = StrategyResult.empty("top1", 63, trades=20)
    assert not few.is_measurable
    assert enough.is_measurable


def test_every_rule_is_scored_on_the_same_window() -> None:
    frame = _rows(
        [
            ("2026-08-03", "A", 0.05, 0.03, "BUY"),
            ("2026-08-03", "B", 0.04, 0.01, "NO_TRADE"),
            ("2026-08-03", "C", 0.03, -0.01, "NO_TRADE"),
            ("2026-08-03", "D", 0.02, 0.02, "NO_TRADE"),
            ("2026-08-03", "E", 0.01, -0.03, "NO_TRADE"),
        ]
    )
    results = evaluate_all(frame)
    assert set(results) == {"threshold", "top1", "top3", "top5"}
    assert results["top1"].trades == 1
    assert results["top3"].trades == 3
    assert results["top5"].trades == 5
    assert all(result.sessions == 1 for result in results.values())


def test_missing_columns_raise_rather_than_score() -> None:
    frame = pd.DataFrame({"date": ["2026-08-03"], "predicted_return": [0.01]})
    with pytest.raises(KeyError, match="actual_return"):
        evaluate(frame)
