"""Re-simulate stored walk-forward predictions under changed trading rules.

The Backtest dashboard page lets a user move the BUY thresholds, capital, costs,
and Top-N cap and see the resulting out-of-sample numbers. This module does that
recomputation and nothing else: it consumes walk-forward predictions that were
already produced one step ahead, so no model is refitted and no new information
enters the past.

Two limits are deliberate rather than incidental:

* Changing the model family or the training window changes what was *predicted*,
  not just how a prediction was traded. Those require rerunning
  ``scripts.run_walk_forward``; this module cannot honour them and refuses to
  pretend otherwise.
* Every scenario is evaluated on the same stored prediction history. Scanning
  thresholds here and then reporting the best one as expected performance is
  selection bias. ``ScenarioResult.scenarios_evaluated`` exists so the UI can
  surface how many variants a user has already tried.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

import pandas as pd

from metrics.performance import PerformanceMetrics, calculate_performance_metrics
from trading.strategy import BuySignalConfig, ExecutionConfig, simulate_intraday_trade

SCENARIO_INPUT_COLUMNS: tuple[str, ...] = (
    "ticker",
    "prediction_date",
    "predicted_return",
    "probability_up",
    "actual_open",
    "actual_close",
)

SCENARIO_TRADE_COLUMNS: tuple[str, ...] = (
    "ticker",
    "prediction_date",
    "predicted_return",
    "probability_up",
    "actual_open",
    "actual_close",
    "actual_return",
    "rank",
    "selected",
    "shares",
    "gross_profit",
    "commission_cost",
    "slippage_cost",
    "net_profit",
    "trade_return",
)


@dataclass(frozen=True, slots=True)
class ScenarioConfig:
    """User-adjustable trading rules applied to stored OOS predictions."""

    return_threshold: float = 0.003
    probability_threshold: float = 0.60
    capital_per_stock: float = 1_000_000.0
    commission_bps: float = 5.0
    slippage_bps: float = 5.0
    lot_size: int = 100
    top_n: int | None = None
    date_from: str | None = None
    date_to: str | None = None

    def __post_init__(self) -> None:
        # -inf is the explicit "no return filter" control case; NaN and +inf
        # would silently trade nothing or everything for the wrong reason.
        if math.isnan(self.return_threshold) or self.return_threshold == math.inf:
            raise ValueError("return_threshold must be finite or -inf")
        if not 0.0 <= self.probability_threshold <= 1.0:
            raise ValueError("probability_threshold must be between 0 and 1")
        if self.top_n is not None and self.top_n < 1:
            raise ValueError("top_n must be at least 1 when supplied")
        if (
            self.date_from is not None
            and self.date_to is not None
            and self.date_from > self.date_to
        ):
            raise ValueError("date_from must not be after date_to")

    @classmethod
    def buy_everything(
        cls,
        *,
        date_from: str | None = None,
        date_to: str | None = None,
        **overrides: Any,
    ) -> ScenarioConfig:
        """Build the "no filter" control case: every prediction is traded.

        This is a reference point, not a strategy. Comparing the real rule
        against buying everything shows whether the BUY filter added anything;
        without it, a profitable filtered result may just mean the whole market
        rose during the window.
        """

        return cls(
            return_threshold=float("-inf"),
            probability_threshold=0.0,
            date_from=date_from,
            date_to=date_to,
            **overrides,
        )

    @property
    def signal_config(self) -> BuySignalConfig:
        """Return the equivalent BUY rule used by the production strategy."""

        return BuySignalConfig(
            return_threshold=self.return_threshold,
            probability_threshold=self.probability_threshold,
        )

    @property
    def execution_config(self) -> ExecutionConfig:
        """Return the equivalent execution assumptions."""

        return ExecutionConfig(
            capital_per_stock=self.capital_per_stock,
            lot_size=self.lot_size,
            commission_bps=self.commission_bps,
            slippage_bps=self.slippage_bps,
        )

    def as_dict(self) -> dict[str, Any]:
        """Return a serialization-friendly mapping for captions and artifacts."""

        return {
            "return_threshold": self.return_threshold,
            "probability_threshold": self.probability_threshold,
            "capital_per_stock": self.capital_per_stock,
            "commission_bps": self.commission_bps,
            "slippage_bps": self.slippage_bps,
            "lot_size": self.lot_size,
            "top_n": self.top_n,
        }


@dataclass(frozen=True, slots=True)
class ScenarioResult:
    """Recomputed portfolio metrics plus the trades that produced them."""

    config: ScenarioConfig
    trades: pd.DataFrame
    portfolio: PerformanceMetrics
    per_ticker: pd.DataFrame
    daily_returns: pd.Series
    rows_considered: int
    rows_skipped: int
    scenarios_evaluated: int = 1
    warnings: tuple[str, ...] = field(default_factory=tuple)

    @property
    def is_low_sample(self) -> bool:
        """Return whether the trade count is below the 20-trade sample floor."""

        return self.portfolio.number_of_trades < 20


def _numeric(frame: pd.DataFrame, column: str) -> pd.Series:
    return pd.to_numeric(frame[column], errors="coerce")


def prepare_scenario_frame(rows: object) -> pd.DataFrame:
    """Normalize stored OOS rows into the scenario input contract.

    Rows lacking a finite prediction or a usable Open/Close pair are dropped
    rather than defaulted, so an unpriced session can never be scored as a
    break-even trade.
    """

    frame = pd.DataFrame(rows) if not isinstance(rows, pd.DataFrame) else rows.copy()
    if frame.empty:
        return pd.DataFrame(columns=SCENARIO_INPUT_COLUMNS)
    missing = sorted(set(SCENARIO_INPUT_COLUMNS) - set(frame.columns))
    if missing:
        raise ValueError(f"missing scenario columns: {missing}")

    prepared = frame.loc[:, list(SCENARIO_INPUT_COLUMNS)].copy()
    for column in (
        "predicted_return",
        "probability_up",
        "actual_open",
        "actual_close",
    ):
        prepared[column] = _numeric(prepared, column)
    prepared["ticker"] = prepared["ticker"].astype(str)
    usable = (
        prepared["predicted_return"].notna()
        & prepared["probability_up"].notna()
        & (prepared["actual_open"] > 0.0)
        & (prepared["actual_close"] > 0.0)
    )
    prepared = prepared.loc[usable].copy()
    prepared["actual_return"] = prepared["actual_close"] / prepared["actual_open"] - 1.0
    return prepared.sort_values(["prediction_date", "ticker"], kind="stable")


def _within_window(frame: pd.DataFrame, config: ScenarioConfig) -> pd.DataFrame:
    """Restrict rows to the configured inclusive prediction-date window."""

    if frame.empty or (config.date_from is None and config.date_to is None):
        return frame
    dates = frame["prediction_date"].astype(str)
    keep = pd.Series(True, index=frame.index)
    if config.date_from is not None:
        keep &= dates >= str(config.date_from)
    if config.date_to is not None:
        keep &= dates <= str(config.date_to)
    return frame.loc[keep].copy()


def _rank_and_select(frame: pd.DataFrame, config: ScenarioConfig) -> pd.DataFrame:
    """Rank each day's BUY-eligible rows and keep at most ``top_n`` of them."""

    ranked = frame.reset_index(drop=True).copy()
    eligible = (ranked["predicted_return"] > config.return_threshold) & (
        ranked["probability_up"] >= config.probability_threshold
    )
    ranked["selected"] = eligible
    ranked["rank"] = pd.NA
    candidates = ranked.loc[eligible]
    for _, day in candidates.groupby("prediction_date", sort=False):
        ordered = day.sort_values("predicted_return", ascending=False, kind="stable")
        for order, position in enumerate(ordered.index, start=1):
            ranked.at[position, "rank"] = order
            if config.top_n is not None and order > config.top_n:
                ranked.at[position, "selected"] = False
    return ranked


def evaluate_scenario(
    rows: object,
    config: ScenarioConfig | None = None,
    *,
    scenarios_evaluated: int = 1,
    periods_per_year: int = 252,
) -> ScenarioResult:
    """Recompute OOS trading metrics for one set of user-chosen rules.

    ``rows`` must be walk-forward (one-step-ahead) predictions joined to the
    realized Open/Close of the same session. Positions are entered at the Open
    and liquidated at that day's Close, matching the production strategy.
    """

    settings = config or ScenarioConfig()
    supplied = rows if isinstance(rows, pd.DataFrame) else pd.DataFrame(rows or [])
    prepared = _within_window(prepare_scenario_frame(supplied), settings)
    warnings: list[str] = []
    skipped = max(0, len(supplied) - len(prepared))
    if skipped:
        warnings.append(
            f"{skipped}件を除外しました: 予測値またはOpen/Closeが未確定です。"
        )
    if prepared.empty:
        return ScenarioResult(
            config=settings,
            trades=pd.DataFrame(columns=SCENARIO_TRADE_COLUMNS),
            portfolio=calculate_performance_metrics([]),
            per_ticker=pd.DataFrame(),
            daily_returns=pd.Series(dtype=float),
            rows_considered=0,
            rows_skipped=skipped,
            scenarios_evaluated=scenarios_evaluated,
            warnings=tuple(warnings),
        )

    ranked = _rank_and_select(prepared, settings)
    records: list[dict[str, Any]] = []
    for _, row in ranked.iterrows():
        selected = bool(row["selected"])
        trade = simulate_intraday_trade(
            float(row["actual_open"]),
            float(row["actual_close"]),
            execute=selected,
            config=settings.execution_config,
        )
        records.append(
            {
                "ticker": str(row["ticker"]),
                "prediction_date": row["prediction_date"],
                "predicted_return": float(row["predicted_return"]),
                "probability_up": float(row["probability_up"]),
                "actual_open": float(row["actual_open"]),
                "actual_close": float(row["actual_close"]),
                "actual_return": float(row["actual_return"]),
                "rank": row["rank"],
                "selected": selected and trade.is_buy,
                "shares": trade.shares,
                "gross_profit": trade.gross_profit,
                "commission_cost": trade.commission_cost,
                "slippage_cost": trade.slippage_cost,
                "net_profit": trade.net_profit,
                "trade_return": trade.return_on_capital,
            }
        )

    trades = pd.DataFrame.from_records(records, columns=SCENARIO_TRADE_COLUMNS)
    executed = trades.loc[trades["selected"]].copy()
    if executed.empty:
        warnings.append("この条件ではBUYが1件も成立しません。")

    daily_returns = (
        executed.groupby("prediction_date", sort=True)["trade_return"].mean()
        if not executed.empty
        else pd.Series(dtype=float)
    )
    portfolio = calculate_performance_metrics(
        executed["net_profit"].to_numpy() if not executed.empty else [],
        trade_returns=daily_returns.to_numpy() if not daily_returns.empty else None,
        predicted_returns=(
            executed["predicted_return"].to_numpy() if not executed.empty else None
        ),
        actual_returns=(
            executed["actual_return"].to_numpy() if not executed.empty else None
        ),
        capital_per_trade=settings.capital_per_stock,
        periods_per_year=periods_per_year,
    )
    if portfolio.number_of_trades < 20:
        warnings.append(
            "LOW_SAMPLE: OOS trade数が20未満です。"
            "指標を有効性の根拠にしないでください。"
        )
    if scenarios_evaluated > 1:
        warnings.append(
            f"この画面で{scenarios_evaluated}通りの条件を試しています。"
            "最良値を選ぶとselection biasが入ります。"
        )

    return ScenarioResult(
        config=settings,
        trades=trades,
        portfolio=portfolio,
        per_ticker=_per_ticker_metrics(executed, settings, periods_per_year),
        daily_returns=daily_returns,
        rows_considered=len(prepared),
        rows_skipped=skipped,
        scenarios_evaluated=scenarios_evaluated,
        warnings=tuple(warnings),
    )


def _per_ticker_metrics(
    executed: pd.DataFrame, config: ScenarioConfig, periods_per_year: int
) -> pd.DataFrame:
    if executed.empty:
        return pd.DataFrame()
    records: list[dict[str, Any]] = []
    for raw_ticker, group in executed.groupby("ticker", sort=True):
        metrics = calculate_performance_metrics(
            group["net_profit"].to_numpy(),
            trade_returns=group["trade_return"].to_numpy(),
            predicted_returns=group["predicted_return"].to_numpy(),
            actual_returns=group["actual_return"].to_numpy(),
            capital_per_trade=config.capital_per_stock,
            periods_per_year=periods_per_year,
        )
        records.append({"ticker": str(raw_ticker), **metrics.as_dict()})
    return pd.DataFrame.from_records(records)


# Readable alias for dashboard call sites.
recompute_scenario = evaluate_scenario
