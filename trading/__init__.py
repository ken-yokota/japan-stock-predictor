"""Public signal and execution-simulation API."""

from trading.post_open import PostOpenProjection, project_predicted_close
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
    "PostOpenProjection",
    "TradeResult",
    "TradingConfig",
    "board_lot_shares",
    "is_buy_signal",
    "project_predicted_close",
    "simulate_intraday_trade",
    "simulate_prediction_frame",
]
