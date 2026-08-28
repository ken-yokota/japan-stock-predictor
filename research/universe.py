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
from functools import lru_cache

import numpy as np

from research.evaluation import Prediction, _t_statistic

# A ticker needs this many scored predictions before the rule may form a view.
# Below it, the accuracy estimate moves by whole percentage points on a single
# session and any threshold is reading noise.
MINIMUM_HISTORY = 40


def round_trip_cost() -> float:
    """The cost a position actually pays, read from the trading config.

    Hard-coded here first as 0.165%, which was wrong and wrong in the direction
    that flatters every rule below: the config charges 5 bps commission and 5
    bps slippage per side, so a round trip is 0.20%. The simulated trades in the
    walk-forward artifacts come out at 0.2002% of notional, which is the same
    number arrived at from the other end.

    Read rather than restated, so a change to the config cannot leave a research
    result quoting a cost the system does not charge.
    """

    return _cached_round_trip_cost()


@lru_cache(maxsize=1)
def _cached_round_trip_cost() -> float:
    from data.config import load_app_config

    costs = load_app_config().trading.costs
    per_side = float(costs.commission_bps_per_side) + float(
        costs.slippage_bps_per_side
    )
    return 2.0 * per_side / 10_000.0


def breakeven_accuracy(
    predictions: Sequence[Prediction], *, cost: float | None = None
) -> float:
    """The direction accuracy at which a coin-flip-sized edge covers the cost.

    Derived from the window being scored rather than carried between windows.
    The first version of this used 1.327%, the average absolute move over the
    thirteen live sessions, against a 250-session record whose average is
    1.1893% -- a smaller move makes the same cost a higher hurdle, so the bar
    was understated at 56.2% when it is 58.4%.
    """

    moves = [abs(row.actual_return) for row in predictions]
    average = float(np.mean(moves)) if moves else 0.0
    if average <= 0.0:
        return 1.0
    return 0.5 + (round_trip_cost() if cost is None else cost) / (2.0 * average)



@dataclass(frozen=True, slots=True)
class TickerRecord:
    """What the history before a session says about one ticker."""

    ticker: str
    predictions: int
    accuracy: float
    z: float
    net: float
    expectancy: float
    # This ticker's own average absolute open-to-close move, over its own
    # history so far. The breakeven bar depends on it, and a quiet name needs a
    # higher accuracy than a volatile one to cover the same fixed cost.
    mean_abs_move: float = 0.0

    @property
    def breakeven(self) -> float:
        if self.mean_abs_move <= 0.0:
            return 1.0
        return 0.5 + round_trip_cost() / (2.0 * self.mean_abs_move)


@dataclass(slots=True)
class _Tally:
    """One ticker's history, accumulated rather than rescanned.

    Rebuilding every record from the full history on every session made the
    backtest quadratic: 250 sessions x 22 tickers x a growing list, four passes
    each. Measured at 16 minutes of CPU for one study. Nothing about the result
    changes -- the same rows are counted in the same order -- but a rule can now
    be scored in seconds, which is what makes running the study per arm
    affordable.
    """

    ticker: str
    predictions: int = 0
    hits: int = 0
    traded: int = 0
    net: float = 0.0
    abs_move_total: float = 0.0

    def add(self, row: Prediction) -> None:
        self.predictions += 1
        self.hits += int(row.direction_correct)
        self.abs_move_total += abs(row.actual_return)
        if row.signal == "BUY":
            self.traded += 1
            self.net += float(row.net_profit_jpy or 0.0)

    def record(self) -> TickerRecord:
        total = self.predictions
        deviation = math.sqrt(total * 0.25)
        return TickerRecord(
            ticker=self.ticker,
            predictions=total,
            accuracy=self.hits / total if total else 0.0,
            z=(self.hits - total * 0.5) / deviation if deviation else 0.0,
            net=self.net,
            expectancy=self.net / self.traded if self.traded else 0.0,
            mean_abs_move=self.abs_move_total / total if total else 0.0,
        )


def _record(ticker: str, rows: Sequence[Prediction]) -> TickerRecord:
    hits = sum(1 for row in rows if row.direction_correct)
    total = len(rows)
    deviation = math.sqrt(total * 0.25)
    traded = [row for row in rows if row.signal == "BUY"]
    net = sum(float(row.net_profit_jpy or 0.0) for row in traded)
    moves = [abs(row.actual_return) for row in rows]
    return TickerRecord(
        ticker=ticker,
        predictions=total,
        accuracy=hits / total if total else 0.0,
        z=(hits - total * 0.5) / deviation if deviation else 0.0,
        net=net,
        expectancy=net / len(traded) if traded else 0.0,
        mean_abs_move=float(np.mean(moves)) if moves else 0.0,
    )


UniverseRule = Callable[[Sequence[TickerRecord]], set[str]]


def all_tickers(records: Sequence[TickerRecord]) -> set[str]:
    """The control. Any rule that cannot beat it has removed value."""

    return {record.ticker for record in records}


def _eligible(records: Sequence[TickerRecord]) -> list[TickerRecord]:
    return [r for r in records if r.predictions >= MINIMUM_HISTORY]


def above_breakeven(records: Sequence[TickerRecord]) -> set[str]:
    """Each ticker against its own bar, computed from its own history.

    Not one bar for everyone: the hurdle is cost divided by the size of the move
    it has to cover, and a name that moves 0.8% a day needs a much better hit
    rate than one that moves 1.6% to pay the same 0.20%. Using the whole
    period's average would also read the sessions being traded.
    """

    return {r.ticker for r in _eligible(records) if r.accuracy > r.breakeven}


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
    cost_per_position: float | None = None,
) -> BacktestResult:
    """Walk forward: pick the universe from the past, then trade the present."""

    cost = round_trip_cost() if cost_per_position is None else cost_per_position
    by_date: dict[str, list[Prediction]] = {}
    for row in predictions:
        by_date.setdefault(row.date, []).append(row)
    order = sorted(by_date)

    history: dict[str, _Tally] = {}
    daily: list[float] = []
    positions = 0
    traded_sessions = 0
    for day in order:
        records = [tally.record() for tally in history.values()]
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
                    np.mean([row.actual_return - cost for row in chosen])
                )
            )
        else:
            daily.append(0.0)
        for row in by_date[day]:
            history.setdefault(row.ticker, _Tally(row.ticker)).add(row)

    return BacktestResult(
        name=name,
        sessions=len(order),
        traded_sessions=traded_sessions,
        positions=positions,
        daily_returns=daily,
    )


UNIVERSE_RULES: dict[str, UniverseRule] = {
    "A 全22銘柄": all_tickers,
    "B 銘柄ごとの損益分岐超え": above_breakeven,
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
    cost_per_position: float = field(default_factory=round_trip_cost)

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

    history: dict[str, _Tally] = {}
    flat: list[Prediction] = []
    daily: list[float] = []
    chosen_thresholds: list[float] = []
    positions = 0
    traded_sessions = 0
    for day in order:
        records = [tally.record() for tally in history.values()]
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
            history.setdefault(row.ticker, _Tally(row.ticker)).add(row)
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
