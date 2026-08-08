from __future__ import annotations

import pandas as pd
import pytest

from trading import (
    BuySignalConfig,
    ExecutionConfig,
    board_lot_shares,
    is_buy_signal,
    simulate_intraday_trade,
    simulate_prediction_frame,
)


def test_buy_rule_uses_strict_return_and_inclusive_probability_thresholds() -> None:
    assert not is_buy_signal(0.003, 0.60)
    assert not is_buy_signal(0.0031, 0.5999)
    assert is_buy_signal(0.0031, 0.60)
    assert not is_buy_signal(float("nan"), 0.9)
    assert is_buy_signal(
        0.0021,
        0.55,
        BuySignalConfig(return_threshold=0.002, probability_threshold=0.55),
    )


def test_board_lot_sizing_and_round_trip_costs() -> None:
    config = ExecutionConfig(
        capital_per_stock=1_000_000.0,
        lot_size=100,
        commission_bps=10.0,
        slippage_bps=5.0,
        fixed_fee_per_order=100.0,
    )
    result = simulate_intraday_trade(1_000.0, 1_020.0, config=config)

    assert result.shares == 900
    assert result.shares % 100 == 0
    assert result.gross_profit == pytest.approx(18_000.0)
    assert result.slippage_cost > 0.0
    assert result.commission_cost > 0.0
    assert result.net_profit == pytest.approx(
        result.gross_profit - result.slippage_cost - result.commission_cost
    )
    assert result.net_profit < result.gross_profit
    assert not result.held_overnight


def test_unaffordable_board_lot_is_not_executed() -> None:
    assert board_lot_shares(50_000.0, 1_000.0) == 0
    result = simulate_intraday_trade(
        1_000.0,
        1_100.0,
        config=ExecutionConfig(capital_per_stock=50_000.0),
    )
    assert not result.is_buy
    assert result.shares == 0
    assert result.net_profit == 0.0


def test_prediction_frame_trades_buy_rows_only() -> None:
    frame = pd.DataFrame(
        {
            "predicted_return": [0.01, 0.0],
            "probability_up": [0.8, 0.8],
            "open": [1_000.0, 1_000.0],
            "close": [1_010.0, 1_010.0],
        }
    )
    result = simulate_prediction_frame(frame)
    assert list(result["buy_signal"]) == [True, False]
    assert result.iloc[0]["shares"] == 1_000
    assert result.iloc[1]["shares"] == 0
