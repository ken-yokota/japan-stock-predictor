"""Choose which tickers to trade, using only what was known before the session.

Toyota, Marubeni and Mitsubishi Corp were the three strongest names across 250
out-of-sample sessions. Trading those three from tomorrow would be fitting the
universe to the very window used to score it: the model's predictions were out
of sample, but the choice of which predictions to act on would not be, and the
250 sessions would stop being an honest test the moment they were used that way.

So the choice becomes part of the walk-forward. At each session the rule sees
only the sessions before it, recomputes each ticker's record from that history,
and picks a universe. A ticker with too short a history is not excluded on a
hunch -- it is held to a minimum sample before the rule is allowed an opinion
about it at all.

The BUY rules are here for the same reason. A threshold chosen by looking at
the whole period and then scored on it measures nothing.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field

import numpy as np

from research.evaluation import Prediction, _t_statistic

# A ticker needs this many scored predictions before the rule may form a view.
# Below it, the accuracy estimate moves by whole percentage points on a single
# session and any threshold is reading noise.
MINIMUM_HISTORY = 40

# Direction accuracy at which a round trip breaks even, given the average
# absolute open-to-close move of 1.327% and a 0.165% cost. Measured, not chosen.
BREAKEVEN_ACCURACY = 0.562


@dataclass(frozen=True, slots=True)
class TickerRecord:
    """What the history before a session says about one ticker."""

    ticker: str
    predictions: int
    accuracy: float
    z: float
    net: float
    expectancy: float


def _record(ticker: str, rows: Sequence[Prediction]) -> TickerRecord:
    hits = sum(1 for row in rows if row.direction_correct)
    total = len(rows)
    deviation = math.sqrt(total * 0.25)
    traded = [row for row in rows if row.signal == "BUY"]
    net = sum(float(row.net_profit_jpy or 0.0) for row in traded)
    return TickerRecord(
        ticker=ticker,
        predictions=total,
        accuracy=hits / total if total else 0.0,
        z=(hits - total * 0.5) / deviation if deviation else 0.0,
        net=net,
        expectancy=net / len(traded) if traded else 0.0,
    )


UniverseRule = Callable[[Sequence[TickerRecord]], set[str]]


def all_tickers(records: Sequence[TickerRecord]) -> set[str]:
    """The control. Any rule that cannot beat it has removed value."""

    return {record.ticker for record in records}


def _eligible(records: Sequence[TickerRecord]) -> list[TickerRecord]:
    return [r for r in records if r.predictions >= MINIMUM_HISTORY]


def above_breakeven(records: Sequence[TickerRecord]) -> set[str]:
    return {r.ticker for r in _eligible(records) if r.accuracy > BREAKEVEN_ACCURACY}


def z_at_least(threshold: float) -> UniverseRule:
    def rule(records: Sequence[TickerRecord]) -> set[str]:
        return {r.ticker for r in _eligible(records) if r.z >= threshold}

    return rule


def top_by_accuracy(count: int) -> UniverseRule:
    def rule(records: Sequence[TickerRecord]) -> set[str]:
        ranked = sorted(_eligible(records), key=lambda r: -r.accuracy)
        return {r.ticker for r in ranked[:count]}

    return rule


BuyRule = Callable[[Prediction], bool]


def buy_production(
    return_threshold: float = 0.003, probability: float = 0.60
) -> BuyRule:
    """What production runs: a forecast above a threshold and a confident classifier."""

    def rule(row: Prediction) -> bool:
        return row.predicted_return > return_threshold and (
            row.probability_up or 0.0
        ) >= probability

    return rule


def buy_regression_only(return_threshold: float = 0.003) -> BuyRule:
    """The same forecast threshold, with the classifier removed.

    Worth its own arm because the classifier is not merely weak: where it
    disagrees with the regression about the sign, the regression is right 52.4%
    of the time and the classifier 47.6%.
    """

    def rule(row: Prediction) -> bool:
        return row.predicted_return > return_threshold

    return rule


def buy_probability_only(probability: float = 0.60) -> BuyRule:
    def rule(row: Prediction) -> bool:
        return (row.probability_up or 0.0) >= probability

    return rule


def buy_agreement() -> BuyRule:
    """Both merely positive, rather than either being confident."""

    def rule(row: Prediction) -> bool:
        return row.predicted_return > 0 and (row.probability_up or 0.0) > 0.5

    return rule


@dataclass(frozen=True, slots=True)
class BacktestResult:
    """One (universe rule, buy rule) pair, scored at session level."""

    name: str
    sessions: int
    traded_sessions: int
    positions: int
    daily_returns: list[float] = field(default_factory=list)

    @property
    def total_return(self) -> float:
        return float(np.sum(self.daily_returns)) if self.daily_returns else 0.0

    @property
    def mean_daily(self) -> float | None:
        return float(np.mean(self.daily_returns)) if self.daily_returns else None

    @property
    def daily_t(self) -> float | None:
        return _t_statistic(self.daily_returns)

    @property
    def sharpe(self) -> float | None:
        if len(self.daily_returns) < 2:
            return None
        array = np.asarray(self.daily_returns, dtype=float)
        deviation = array.std(ddof=1)
        return float(array.mean() / deviation) if deviation else None

    @property
    def max_drawdown(self) -> float:
        if not self.daily_returns:
            return 0.0
        equity = np.cumsum(self.daily_returns)
        peak = np.maximum.accumulate(equity)
        return float((equity - peak).min())

    @property
    def winning_sessions(self) -> int:
        return sum(1 for value in self.daily_returns if value > 0)


def backtest(
    predictions: Sequence[Prediction],
    *,
    name: str,
    universe: UniverseRule,
    buy: BuyRule,
    cost_per_position: float = 0.00165,
) -> BacktestResult:
    """Walk forward: pick the universe from the past, then trade the present."""

    by_date: dict[str, list[Prediction]] = {}
    for row in predictions:
        by_date.setdefault(row.date, []).append(row)
    order = sorted(by_date)

    history: dict[str, list[Prediction]] = {}
    daily: list[float] = []
    positions = 0
    traded_sessions = 0
    for day in order:
        records = [_record(ticker, rows) for ticker, rows in history.items()]
        allowed = universe(records) if records else set()
        chosen = [
            row
            for row in by_date[day]
            if row.ticker in allowed and buy(row)
        ]
        if chosen:
            traded_sessions += 1
            positions += len(chosen)
            daily.append(
                float(
                    np.mean([row.actual_return - cost_per_position for row in chosen])
                )
            )
        else:
            daily.append(0.0)
        for row in by_date[day]:
            history.setdefault(row.ticker, []).append(row)

    return BacktestResult(
        name=name,
        sessions=len(order),
        traded_sessions=traded_sessions,
        positions=positions,
        daily_returns=daily,
    )


UNIVERSE_RULES: dict[str, UniverseRule] = {
    "A 全22銘柄": all_tickers,
    "B 過去的中率>56.2%": above_breakeven,
    "C 過去z>=1.5": z_at_least(1.5),
    "D 過去z>=2.0": z_at_least(2.0),
    "E 過去上位5銘柄": top_by_accuracy(5),
}

BUY_RULES: dict[str, BuyRule] = {
    "A 現行(予測>0.3% かつ 確率>=0.60)": buy_production(),
    "B 回帰のみ(予測>0.3%)": buy_regression_only(),
    "C 確率のみ(確率>=0.60)": buy_probability_only(),
    "D 一致(予測>0 かつ 確率>0.5)": buy_agreement(),
    "E 現行だが確率>=0.50": buy_production(probability=0.50),
    "F 現行だが確率>=0.55": buy_production(probability=0.55),
    "G 現行だが確率>=0.65": buy_production(probability=0.65),
}


@dataclass(frozen=True, slots=True)
class AdaptiveBuy:
    """Pick the probability threshold from the sessions already scored.

    Sweeping thresholds over the whole period and reporting the best one is the
    most direct way to manufacture an edge here: seven candidates on 250
    sessions will always produce a winner, and it will not survive contact with
    the next 250. This chooses at each session from the history before it, so
    the threshold is part of the walk-forward rather than a result of it.
    """

    candidates: tuple[float, ...] = (0.50, 0.55, 0.60, 0.65)
    return_threshold: float = 0.003
    minimum_history: int = MINIMUM_HISTORY
    cost_per_position: float = 0.00165

    def choose(self, history: Sequence[Prediction]) -> float:
        """The threshold with the best realised net return so far."""

        eligible = [
            row for row in history if row.predicted_return > self.return_threshold
        ]
        if len(eligible) < self.minimum_history:
            return self.candidates[len(self.candidates) // 2]
        scored: list[tuple[float, float]] = []
        for candidate in self.candidates:
            taken = [
                row
                for row in eligible
                if (row.probability_up or 0.0) >= candidate
            ]
            if not taken:
                continue
            net = float(
                np.mean([r.actual_return - self.cost_per_position for r in taken])
            )
            scored.append((net, candidate))
        return max(scored)[1] if scored else self.candidates[0]


def backtest_adaptive(
    predictions: Sequence[Prediction],
    *,
    name: str,
    universe: UniverseRule,
    adaptive: AdaptiveBuy,
) -> tuple[BacktestResult, list[float]]:
    """As ``backtest``, but the buy threshold is re-chosen each session."""

    by_date: dict[str, list[Prediction]] = {}
    for row in predictions:
        by_date.setdefault(row.date, []).append(row)
    order = sorted(by_date)

    history: dict[str, list[Prediction]] = {}
    flat: list[Prediction] = []
    daily: list[float] = []
    chosen_thresholds: list[float] = []
    positions = 0
    traded_sessions = 0
    for day in order:
        records = [_record(ticker, rows) for ticker, rows in history.items()]
        allowed = universe(records) if records else set()
        threshold = adaptive.choose(flat)
        chosen_thresholds.append(threshold)
        rule = buy_production(adaptive.return_threshold, threshold)
        chosen = [row for row in by_date[day] if row.ticker in allowed and rule(row)]
        if chosen:
            traded_sessions += 1
            positions += len(chosen)
            daily.append(
                float(
                    np.mean(
                        [
                            row.actual_return - adaptive.cost_per_position
                            for row in chosen
                        ]
                    )
                )
            )
        else:
            daily.append(0.0)
        for row in by_date[day]:
            history.setdefault(row.ticker, []).append(row)
            flat.append(row)

    return (
        BacktestResult(
            name=name,
            sessions=len(order),
            traded_sessions=traded_sessions,
            positions=positions,
            daily_returns=daily,
        ),
        chosen_thresholds,
    )
