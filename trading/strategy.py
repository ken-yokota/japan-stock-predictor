"""BUY decision and same-day, 100-share-lot execution simulation."""

from __future__ import annotations

import math
from dataclasses import dataclass

import pandas as pd


@dataclass(frozen=True, slots=True)
class BuySignalConfig:
    """Strict initial BUY thresholds from the product specification."""

    return_threshold: float = 0.003
    probability_threshold: float = 0.60

    def __post_init__(self) -> None:
        if not math.isfinite(self.return_threshold):
            raise ValueError("return_threshold must be finite")
        if not 0.0 <= self.probability_threshold <= 1.0:
            raise ValueError("probability_threshold must be between 0 and 1")


@dataclass(frozen=True, slots=True)
class ExecutionConfig:
    """Capital, board-lot, and configurable round-trip execution assumptions."""

    capital_per_stock: float = 1_000_000.0
    lot_size: int = 100
    commission_bps: float = 0.0
    slippage_bps: float = 0.0
    fixed_fee_per_order: float = 0.0

    def __post_init__(self) -> None:
        if not math.isfinite(self.capital_per_stock) or self.capital_per_stock <= 0:
            raise ValueError("capital_per_stock must be positive and finite")
        if self.lot_size <= 0:
            raise ValueError("lot_size must be positive")
        for field_name in (
            "commission_bps",
            "slippage_bps",
            "fixed_fee_per_order",
        ):
            value = float(getattr(self, field_name))
            if not math.isfinite(value) or value < 0.0:
                raise ValueError(f"{field_name} must be finite and non-negative")


@dataclass(frozen=True, slots=True)
class TradeResult:
    """One intraday round trip; positions are always closed in this result."""

    is_buy: bool
    shares: int
    raw_open: float
    raw_close: float
    execution_open: float
    execution_close: float
    gross_profit: float
    commission_cost: float
    slippage_cost: float
    net_profit: float
    return_on_capital: float

    @property
    def held_overnight(self) -> bool:
        """Explicit invariant for consumers and tests."""

        return False


def is_buy_signal(
    predicted_return: float,
    probability_up: float,
    config: BuySignalConfig | None = None,
) -> bool:
    """Return BUY only for return ``>`` 0.3% and probability ``>=`` 60%."""

    settings = config or BuySignalConfig()
    if not math.isfinite(predicted_return) or not math.isfinite(probability_up):
        return False
    return (
        predicted_return > settings.return_threshold
        and probability_up >= settings.probability_threshold
    )


def board_lot_shares(
    capital: float,
    execution_price: float,
    *,
    lot_size: int = 100,
    commission_bps: float = 0.0,
    fixed_fee: float = 0.0,
) -> int:
    """Return the largest affordable share count in complete board lots."""

    values = (capital, execution_price, commission_bps, fixed_fee)
    if any(not math.isfinite(value) for value in values):
        raise ValueError("capital, price, and costs must be finite")
    if capital <= 0.0 or execution_price <= 0.0:
        raise ValueError("capital and execution_price must be positive")
    if lot_size <= 0 or commission_bps < 0.0 or fixed_fee < 0.0:
        raise ValueError("lot_size must be positive and costs non-negative")
    available = capital - fixed_fee
    if available <= 0.0:
        return 0
    commission_rate = commission_bps / 10_000.0
    effective_unit_cost = execution_price * (1.0 + commission_rate)
    lots = math.floor(available / (effective_unit_cost * lot_size))
    return max(0, lots) * lot_size


def simulate_intraday_trade(
    open_price: float,
    close_price: float,
    *,
    execute: bool = True,
    config: ExecutionConfig | None = None,
) -> TradeResult:
    """Buy at open and liquidate at close with two-sided costs/slippage.

    Slippage worsens both fills: the buy executes above the raw open and the
    sale below the raw close.  Board-lot sizing includes the entry commission
    and fixed entry fee so simulated cash usage cannot exceed configured
    capital.
    """

    settings = config or ExecutionConfig()
    if not execute:
        return TradeResult(
            is_buy=False,
            shares=0,
            raw_open=float(open_price),
            raw_close=float(close_price),
            execution_open=float(open_price),
            execution_close=float(close_price),
            gross_profit=0.0,
            commission_cost=0.0,
            slippage_cost=0.0,
            net_profit=0.0,
            return_on_capital=0.0,
        )
    if not math.isfinite(open_price) or not math.isfinite(close_price):
        raise ValueError("open_price and close_price must be finite")
    if open_price <= 0.0 or close_price <= 0.0:
        raise ValueError("open_price and close_price must be positive")

    slippage_rate = settings.slippage_bps / 10_000.0
    commission_rate = settings.commission_bps / 10_000.0
    execution_open = open_price * (1.0 + slippage_rate)
    execution_close = close_price * (1.0 - slippage_rate)
    shares = board_lot_shares(
        settings.capital_per_stock,
        execution_open,
        lot_size=settings.lot_size,
        commission_bps=settings.commission_bps,
        fixed_fee=settings.fixed_fee_per_order,
    )
    if shares == 0:
        return TradeResult(
            is_buy=False,
            shares=0,
            raw_open=open_price,
            raw_close=close_price,
            execution_open=execution_open,
            execution_close=execution_close,
            gross_profit=0.0,
            commission_cost=0.0,
            slippage_cost=0.0,
            net_profit=0.0,
            return_on_capital=0.0,
        )

    buy_notional = execution_open * shares
    sell_notional = execution_close * shares
    commission_cost = (
        buy_notional + sell_notional
    ) * commission_rate + 2.0 * settings.fixed_fee_per_order
    gross_profit = (close_price - open_price) * shares
    slippage_cost = (
        (execution_open - open_price) + (close_price - execution_close)
    ) * shares
    net_profit = gross_profit - slippage_cost - commission_cost
    return TradeResult(
        is_buy=True,
        shares=shares,
        raw_open=open_price,
        raw_close=close_price,
        execution_open=execution_open,
        execution_close=execution_close,
        gross_profit=gross_profit,
        commission_cost=commission_cost,
        slippage_cost=slippage_cost,
        net_profit=net_profit,
        return_on_capital=net_profit / settings.capital_per_stock,
    )


def simulate_prediction_frame(
    frame: pd.DataFrame,
    *,
    predicted_return_column: str = "predicted_return",
    probability_column: str = "probability_up",
    open_column: str = "open",
    close_column: str = "close",
    signal_config: BuySignalConfig | None = None,
    execution_config: ExecutionConfig | None = None,
) -> pd.DataFrame:
    """Attach BUY decisions and execution results to a prediction frame."""

    required = {
        predicted_return_column,
        probability_column,
        open_column,
        close_column,
    }
    missing = sorted(required - set(frame.columns))
    if missing:
        raise ValueError(f"missing required columns: {missing}")
    records: list[dict[str, object]] = []
    for _, row in frame.iterrows():
        signal = is_buy_signal(
            float(row[predicted_return_column]),
            float(row[probability_column]),
            signal_config,
        )
        trade = simulate_intraday_trade(
            float(row[open_column]),
            float(row[close_column]),
            execute=signal,
            config=execution_config,
        )
        records.append(
            {
                "buy_signal": signal,
                "shares": trade.shares,
                "gross_profit": trade.gross_profit,
                "commission_cost": trade.commission_cost,
                "slippage_cost": trade.slippage_cost,
                "net_profit": trade.net_profit,
                "trade_return": trade.return_on_capital,
            }
        )
    additions = pd.DataFrame.from_records(records, index=frame.index)
    return pd.concat([frame.copy(deep=True), additions], axis=1)


# Common integration aliases.
TradingConfig = ExecutionConfig
BuyRule = BuySignalConfig
