"""Score the selection rule, not just the numbers it was built from.

Rank IC says whether the ordering is right. It cannot say whether acting on
that ordering makes money, and the two can disagree: an ordering that is right
about the middle of the cross-section and wrong about its top earns nothing
while scoring well. So every feature-set comparison reports both, and a set is
only preferred when the ranking metric and the selection result agree.

Selection is evaluated four ways because the current BUY threshold was tuned
for absolute-return predictions and is not neutral between candidates. Top-1,
top-3 and top-5 ask the question the ranking metric is actually about - take the
best names this morning offers - and are directly comparable across arms.

Returns here are in return space, with the round trip's commission and slippage
subtracted. Lot rounding is deliberately not modelled: it would add a
per-ticker artefact of share price that has nothing to do with the predictor
set. Every arm carries the same approximation, so comparisons are unaffected;
absolute yen figures from this module are therefore indicative, and the
production simulator remains the authority on those.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

# 252 JPX sessions is the usual convention and is only used for annualising.
SESSIONS_PER_YEAR = 252


@dataclass(frozen=True, slots=True)
class StrategyResult:
    """One selection rule's record over the window."""

    rule: str
    trades: int
    sessions: int
    win_rate: float
    gross_profit: float
    gross_loss: float
    net_profit: float
    profit_factor: float
    expectancy: float
    average_return: float
    max_drawdown: float
    sharpe: float
    sortino: float
    daily_returns: tuple[float, ...] = field(default=(), repr=False)

    @classmethod
    def empty(cls, rule: str, sessions: int, trades: int = 0) -> StrategyResult:
        """A rule that selected nothing, stated rather than counted by hand."""

        return cls(
            rule=rule,
            trades=trades,
            sessions=sessions,
            win_rate=0.0,
            gross_profit=0.0,
            gross_loss=0.0,
            net_profit=0.0,
            profit_factor=0.0,
            expectancy=0.0,
            average_return=0.0,
            max_drawdown=0.0,
            sharpe=0.0,
            sortino=0.0,
        )

    @property
    def is_measurable(self) -> bool:
        """Below about 20 trades, report the count and conclude nothing."""

        return self.trades >= 20


def _round_trip_cost(commission_bps: float, slippage_bps: float) -> float:
    """Both legs of both costs, expressed as a fraction of notional."""

    return 2.0 * (commission_bps + slippage_bps) / 10_000.0


def _max_drawdown(equity: np.ndarray) -> float:
    """Deepest peak-to-trough fall of the cumulative return path."""

    if equity.size == 0:
        return 0.0
    peak = np.maximum.accumulate(equity)
    return float(np.min(equity - peak))


def _sharpe(daily: np.ndarray) -> float:
    if daily.size < 2:
        return 0.0
    deviation = float(daily.std(ddof=1))
    if deviation == 0.0:
        return 0.0
    return float(daily.mean() / deviation * np.sqrt(SESSIONS_PER_YEAR))


def _sortino(daily: np.ndarray) -> float:
    """Sharpe's denominator counts good days as risk; this one does not."""

    if daily.size < 2:
        return 0.0
    downside = daily[daily < 0.0]
    if downside.size == 0:
        # No losing session in the window. That is a statement about the
        # window's length, not about a risk-free strategy, so it is left at
        # zero rather than reported as infinite.
        return 0.0
    deviation = float(np.sqrt(np.mean(np.square(downside))))
    if deviation == 0.0:
        return 0.0
    return float(daily.mean() / deviation * np.sqrt(SESSIONS_PER_YEAR))


def _selected(frame: pd.DataFrame, rule: str, top_k: int | None) -> pd.DataFrame:
    if top_k is None:
        return frame.loc[frame["signal"].astype(str).str.upper() == "BUY"]
    ranked = frame.sort_values("predicted_return", ascending=False)
    return ranked.groupby("date", sort=False, group_keys=False).head(top_k)


def evaluate(
    predictions: pd.DataFrame,
    *,
    rule: str = "threshold",
    top_k: int | None = None,
    commission_bps: float = 0.0,
    slippage_bps: float = 0.0,
) -> StrategyResult:
    """Run one selection rule over a window of predictions.

    ``top_k`` picks that many highest-predicted names each session; ``None``
    uses the stored BUY signal. Positions within a session are equally weighted,
    so a day holding one name is not compared against a day holding five as
    though the exposure were the same.
    """

    frame = predictions
    if "date" not in frame.columns:
        frame = frame.reset_index()
    required = {"date", "predicted_return", "actual_return", "signal"}
    missing = required - set(frame.columns)
    if missing:
        raise KeyError(f"predictions are missing {sorted(missing)}")

    sessions = int(frame["date"].nunique())
    chosen = _selected(frame, rule, top_k)
    cost = _round_trip_cost(commission_bps, slippage_bps)
    if chosen.empty:
        return StrategyResult.empty(rule, sessions)

    net = chosen["actual_return"].astype(float) - cost
    wins = net.loc[net > 0.0]
    losses = net.loc[net < 0.0]
    gross_profit = float(wins.sum())
    gross_loss = float(abs(losses.sum()))

    # One number per session, equally weighted inside it, so the equity path is
    # what a fixed capital base would actually have experienced.
    daily = net.groupby(chosen["date"]).mean().sort_index()
    values = np.asarray(daily, dtype=float)
    equity = np.cumsum(values)

    return StrategyResult(
        rule=rule,
        trades=int(len(chosen)),
        sessions=sessions,
        win_rate=float((net > 0.0).mean()),
        gross_profit=gross_profit,
        gross_loss=gross_loss,
        net_profit=float(net.sum()),
        profit_factor=(gross_profit / gross_loss) if gross_loss > 0 else float("inf"),
        expectancy=float(net.mean()),
        average_return=float(net.mean()),
        max_drawdown=_max_drawdown(equity),
        sharpe=_sharpe(values),
        sortino=_sortino(values),
        daily_returns=tuple(values.tolist()),
    )


def evaluate_all(
    predictions: pd.DataFrame,
    *,
    commission_bps: float = 0.0,
    slippage_bps: float = 0.0,
) -> dict[str, StrategyResult]:
    """The four selection rules every arm is scored on."""

    costs = {"commission_bps": commission_bps, "slippage_bps": slippage_bps}
    results = {
        "threshold": evaluate(predictions, rule="threshold", **costs),
    }
    for k in (1, 3, 5):
        results[f"top{k}"] = evaluate(
            predictions, rule=f"top{k}", top_k=k, **costs
        )
    return results
