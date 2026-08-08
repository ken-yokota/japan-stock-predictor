"""Public signal and execution-simulation API."""

from trading.strategy import (
    BuyRule,
    BuySignalConfig,
    ExecutionConfig,
    TradeResult,
    TradingConfig,
    board_lot_shares,
    is_buy_signal,
    simulate_intraday_trade,
    simulate_prediction_frame,
)

__all__ = [
    "BuyRule",
    "BuySignalConfig",
    "ExecutionConfig",
    "TradeResult",
    "TradingConfig",
    "board_lot_shares",
    "is_buy_signal",
    "simulate_intraday_trade",
    "simulate_prediction_frame",
]
