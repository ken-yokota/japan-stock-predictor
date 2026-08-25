"""One evaluation of a prediction set, in three layers that must not be mixed.

The system has three separable questions and they had been collapsed into one
number, which is how a change could improve the predictions and worsen the
trading and leave nobody able to say whether it was an improvement:

| 層 | 問い | 標本 |
|---|---|---|
| Model | 当日の Open→Close をどれだけ当てられるか | 予測1件ずつ |
| Selection | その日の銘柄から相対的に良いものを選べるか | 営業日ごと |
| Trading | 閾値・建玉・コストを通して最終的にいくらか | 取引と営業日 |

The model layer has the largest sample and is the one a model change is judged
on. The trading layer has the smallest and is reported but never used to
choose -- a handful of trades cannot separate a better model from a luckier
month.

Everything here is a pure function of already-made predictions. Nothing refits,
nothing fetches, and nothing here can see a value dated on or after the session
it is scoring; that boundary belongs to whoever produced the predictions.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

import numpy as np

# Below this many trades, trade statistics are reported and then disclaimed.
MINIMUM_TRADES_FOR_EVIDENCE = 20


@dataclass(frozen=True, slots=True)
class Prediction:
    """One ticker on one session, and what happened to it.

    Deliberately provider-agnostic: the live database and the research
    walk-forward both map onto this, so the same metric code scores both. A
    metric that exists in two implementations will eventually disagree with
    itself, and then a comparison measures the implementations.
    """

    date: str
    ticker: str
    predicted_return: float
    actual_return: float
    probability_up: float | None = None
    signal: str = "NO_BUY"
    net_profit_jpy: float | None = None
    gross_profit_jpy: float | None = None
    cost_jpy: float | None = None
    sector: str | None = None

    @property
    def direction_correct(self) -> bool:
        return (self.predicted_return > 0) == (self.actual_return > 0)


def _ranks(values: Sequence[float] | np.ndarray) -> np.ndarray:
    """Average ranks, so ties do not invent an ordering that is not there."""

    array = np.asarray(values, dtype=float)
    order = array.argsort()
    ranks = np.empty(len(array), dtype=float)
    ranks[order] = np.arange(len(array), dtype=float)
    # Average the ranks inside each tie group.
    _, inverse, counts = np.unique(array, return_inverse=True, return_counts=True)
    for index in np.nonzero(counts > 1)[0]:
        mask = inverse == index
        ranks[mask] = ranks[mask].mean()
    return ranks


def pearson(
    x: Sequence[float] | np.ndarray, y: Sequence[float] | np.ndarray
) -> float | None:
    if len(x) < 3:
        return None
    a, b = np.asarray(x, dtype=float), np.asarray(y, dtype=float)
    if a.std() == 0 or b.std() == 0:
        return None
    return float(np.corrcoef(a, b)[0, 1])


def spearman(
    x: Sequence[float] | np.ndarray, y: Sequence[float] | np.ndarray
) -> float | None:
    if len(x) < 3:
        return None
    return pearson(_ranks(x), _ranks(y))


def _t_statistic(values: Sequence[float] | np.ndarray) -> float | None:
    """One-sample t against zero. Reported so a mean is never read alone."""

    if len(values) < 2:
        return None
    array = np.asarray(values, dtype=float)
    deviation = array.std(ddof=1)
    if deviation == 0:
        return None
    return float(array.mean() / (deviation / math.sqrt(len(array))))


@dataclass(frozen=True, slots=True)
class ModelQuality:
    """How close the predicted number came to the realised one."""

    count: int
    mae: float
    rmse: float
    pearson: float | None
    spearman: float | None
    bias: float
    calibration_slope: float | None
    calibration_intercept: float | None
    direction_accuracy: float
    predicted_mean: float
    actual_mean: float

    @property
    def direction_edge_pp(self) -> float:
        """Percentage points above a coin toss."""

        return (self.direction_accuracy - 0.5) * 100


def model_quality(predictions: Sequence[Prediction]) -> ModelQuality:
    predicted = np.array([p.predicted_return for p in predictions], dtype=float)
    actual = np.array([p.actual_return for p in predictions], dtype=float)
    error = predicted - actual
    slope = intercept = None
    if len(predicted) >= 3 and predicted.std() > 0:
        # actual = intercept + slope * predicted. Perfect calibration is
        # slope 1, intercept 0; slope below 1 means the forecast overshoots.
        slope_value, intercept_value = np.polyfit(predicted, actual, 1)
        slope, intercept = float(slope_value), float(intercept_value)
    return ModelQuality(
        count=len(predictions),
        mae=float(np.abs(error).mean()),
        rmse=float(np.sqrt((error**2).mean())),
        pearson=pearson(predicted, actual),
        spearman=spearman(predicted, actual),
        bias=float(predicted.mean() - actual.mean()),
        calibration_slope=slope,
        calibration_intercept=intercept,
        direction_accuracy=float(
            np.mean([p.direction_correct for p in predictions])
        ),
        predicted_mean=float(predicted.mean()),
        actual_mean=float(actual.mean()),
    )


@dataclass(frozen=True, slots=True)
class QuantileRow:
    quantile: int
    count: int
    predicted_mean: float
    actual_mean: float
    win_rate: float


def quantile_table(
    predictions: Sequence[Prediction], *, buckets: int = 5
) -> list[QuantileRow]:
    """Sorted by predicted return, so monotonicity is visible or absent.

    If the top bucket does not out-realise the bottom one, no threshold on the
    predicted value can help: the ordering carries no information to threshold.
    """

    if len(predictions) < buckets:
        return []
    predicted = np.array([p.predicted_return for p in predictions], dtype=float)
    actual = np.array([p.actual_return for p in predictions], dtype=float)
    order = predicted.argsort()
    rows = []
    for index, chunk in enumerate(np.array_split(order, buckets), start=1):
        rows.append(
            QuantileRow(
                quantile=index,
                count=len(chunk),
                predicted_mean=float(predicted[chunk].mean()),
                actual_mean=float(actual[chunk].mean()),
                win_rate=float((actual[chunk] > 0).mean()),
            )
        )
    return rows


def quantile_is_monotonic(rows: Sequence[QuantileRow]) -> bool:
    """Whether realised return rises with the forecast across every bucket."""

    return len(rows) >= 2 and all(
        rows[i].actual_mean <= rows[i + 1].actual_mean for i in range(len(rows) - 1)
    )


@dataclass(frozen=True, slots=True)
class SessionSelection:
    """One session's cross-section: could the model rank that day's names?"""

    date: str
    universe: int
    rank_ic: float | None
    universe_mean: float
    top1: float | None
    top3: float | None
    top5: float | None
    bottom3: float | None
    bottom5: float | None

    def alpha(self, top: float | None) -> float | None:
        return None if top is None else top - self.universe_mean


def _mean_of_top(sorted_actual: np.ndarray, n: int) -> float | None:
    return float(sorted_actual[:n].mean()) if len(sorted_actual) >= n else None


def session_selection(predictions: Sequence[Prediction]) -> list[SessionSelection]:
    """Per session, ranked by predicted return, scored on realised return."""

    by_date: dict[str, list[Prediction]] = {}
    for item in predictions:
        by_date.setdefault(item.date, []).append(item)
    sessions = []
    for day in sorted(by_date):
        rows = by_date[day]
        if len(rows) < 3:
            continue
        predicted = np.array([r.predicted_return for r in rows], dtype=float)
        actual = np.array([r.actual_return for r in rows], dtype=float)
        order = (-predicted).argsort()
        ranked = actual[order]
        sessions.append(
            SessionSelection(
                date=day,
                universe=len(rows),
                rank_ic=spearman(predicted, actual),
                universe_mean=float(actual.mean()),
                top1=_mean_of_top(ranked, 1),
                top3=_mean_of_top(ranked, 3),
                top5=_mean_of_top(ranked, 5),
                bottom3=_mean_of_top(ranked[::-1], 3),
                bottom5=_mean_of_top(ranked[::-1], 5),
            )
        )
    return sessions


@dataclass(frozen=True, slots=True)
class SelectionQuality:
    """Whether ranking the day's names adds anything over holding them all."""

    sessions: int
    rank_ic_mean: float | None
    rank_ic_sd: float | None
    rank_ic_t: float | None
    top1_alpha: float | None
    top3_alpha: float | None
    top5_alpha: float | None
    top5_alpha_t: float | None
    top_bottom_spread: float | None
    universe_mean: float | None


def selection_quality(sessions: Sequence[SessionSelection]) -> SelectionQuality:
    if not sessions:
        return SelectionQuality(0, None, None, None, None, None, None, None, None, None)
    ics = [s.rank_ic for s in sessions if s.rank_ic is not None]
    alphas: dict[str, list[float]] = {}
    for name in ("top1", "top3", "top5"):
        values: list[float] = []
        for item in sessions:
            top = getattr(item, name)
            if top is not None:
                values.append(float(top) - item.universe_mean)
        alphas[name] = values
    spread = [
        s.top5 - s.bottom5
        for s in sessions
        if s.top5 is not None and s.bottom5 is not None
    ]
    return SelectionQuality(
        sessions=len(sessions),
        rank_ic_mean=float(np.mean(ics)) if ics else None,
        rank_ic_sd=float(np.std(ics, ddof=1)) if len(ics) > 1 else None,
        rank_ic_t=_t_statistic(ics),
        top1_alpha=float(np.mean(alphas["top1"])) if alphas["top1"] else None,
        top3_alpha=float(np.mean(alphas["top3"])) if alphas["top3"] else None,
        top5_alpha=float(np.mean(alphas["top5"])) if alphas["top5"] else None,
        top5_alpha_t=_t_statistic(alphas["top5"]) if alphas["top5"] else None,
        top_bottom_spread=float(np.mean(spread)) if spread else None,
        universe_mean=float(np.mean([s.universe_mean for s in sessions])),
    )


@dataclass(frozen=True, slots=True)
class ProbabilityBin:
    low: float
    high: float
    count: int
    mean_predicted: float
    actual_up_rate: float


@dataclass(frozen=True, slots=True)
class ProbabilityQuality:
    count: int
    brier: float | None
    log_loss: float | None
    base_rate: float
    bins: list[ProbabilityBin] = field(default_factory=list)


DEFAULT_PROBABILITY_BINS: tuple[tuple[float, float], ...] = (
    (0.00, 0.50),
    (0.50, 0.55),
    (0.55, 0.60),
    (0.60, 0.65),
    (0.65, 0.70),
    (0.70, 0.80),
    (0.80, 1.01),
)


def probability_quality(
    predictions: Sequence[Prediction],
    *,
    bins: Sequence[tuple[float, float]] = DEFAULT_PROBABILITY_BINS,
) -> ProbabilityQuality:
    """Is a stated 60% actually 60%?

    A threshold on an uncalibrated score is an arbitrary cut, not a decision.
    """

    scored = [p for p in predictions if p.probability_up is not None]
    if not scored:
        return ProbabilityQuality(0, None, None, 0.0, [])
    probability = np.array([p.probability_up for p in scored], dtype=float)
    outcome = np.array([1.0 if p.actual_return > 0 else 0.0 for p in scored])
    clipped = np.clip(probability, 1e-9, 1 - 1e-9)
    rows = []
    for low, high in bins:
        mask = (probability >= low) & (probability < high)
        if not mask.any():
            continue
        rows.append(
            ProbabilityBin(
                low=low,
                high=high,
                count=int(mask.sum()),
                mean_predicted=float(probability[mask].mean()),
                actual_up_rate=float(outcome[mask].mean()),
            )
        )
    return ProbabilityQuality(
        count=len(scored),
        brier=float(np.mean((probability - outcome) ** 2)),
        log_loss=float(
            -np.mean(outcome * np.log(clipped) + (1 - outcome) * np.log(1 - clipped))
        ),
        base_rate=float(outcome.mean()),
        bins=rows,
    )


@dataclass(frozen=True, slots=True)
class TradingQuality:
    """The smallest sample in the report, and the one never used to choose."""

    trades: int
    sessions: int
    gross_jpy: float
    cost_jpy: float
    net_jpy: float
    win_rate: float | None
    payoff_ratio: float | None
    profit_factor: float | None
    expectancy_jpy: float | None
    winning_days: int
    losing_days: int
    daily_mean_jpy: float | None
    daily_sd_jpy: float | None
    daily_sharpe: float | None
    daily_sortino: float | None
    max_drawdown_jpy: float
    worst_day_jpy: float | None
    best_day_jpy: float | None

    @property
    def underpowered(self) -> bool:
        return self.trades < MINIMUM_TRADES_FOR_EVIDENCE


def trading_quality(
    predictions: Sequence[Prediction], *, signal: str = "BUY"
) -> TradingQuality:
    """Trade-level and day-level, kept apart on purpose.

    Same-day names move together, so N trades are not N independent
    observations. The day-level block is the one whose count reflects how much
    was actually learned.
    """

    trades = [p for p in predictions if p.signal == signal]
    sessions = sorted({p.date for p in predictions})
    net = np.array([float(p.net_profit_jpy or 0.0) for p in trades])
    gross = np.array([float(p.gross_profit_jpy or 0.0) for p in trades])
    cost = np.array([float(p.cost_jpy or 0.0) for p in trades])
    wins, losses = net[net > 0], net[net <= 0]

    daily: list[float] = []
    for day in sessions:
        daily.append(
            sum(float(p.net_profit_jpy or 0.0) for p in trades if p.date == day)
        )
    daily_array = np.array(daily, dtype=float)
    equity = np.cumsum(daily_array) if len(daily_array) else np.array([0.0])
    peak = np.maximum.accumulate(equity)
    drawdown = float((equity - peak).min()) if len(equity) else 0.0
    downside = daily_array[daily_array < 0]

    return TradingQuality(
        trades=len(trades),
        sessions=len(sessions),
        gross_jpy=float(gross.sum()),
        cost_jpy=float(cost.sum()),
        net_jpy=float(net.sum()),
        win_rate=float(len(wins) / len(net)) if len(net) else None,
        payoff_ratio=(
            float(wins.mean() / abs(losses.mean()))
            if len(wins) and len(losses) and losses.mean() != 0
            else None
        ),
        profit_factor=(
            float(wins.sum() / abs(losses.sum()))
            if len(losses) and losses.sum() != 0
            else None
        ),
        expectancy_jpy=float(net.mean()) if len(net) else None,
        winning_days=int((daily_array > 0).sum()),
        losing_days=int((daily_array < 0).sum()),
        daily_mean_jpy=float(daily_array.mean()) if len(daily_array) else None,
        daily_sd_jpy=(
            float(daily_array.std(ddof=1)) if len(daily_array) > 1 else None
        ),
        daily_sharpe=(
            float(daily_array.mean() / daily_array.std(ddof=1))
            if len(daily_array) > 1 and daily_array.std(ddof=1) > 0
            else None
        ),
        daily_sortino=(
            float(daily_array.mean() / downside.std(ddof=1))
            if len(downside) > 1 and downside.std(ddof=1) > 0
            else None
        ),
        max_drawdown_jpy=drawdown,
        worst_day_jpy=float(daily_array.min()) if len(daily_array) else None,
        best_day_jpy=float(daily_array.max()) if len(daily_array) else None,
    )


@dataclass(frozen=True, slots=True)
class Evaluation:
    """All three layers of one prediction set, scored together but reported apart."""

    label: str
    model: ModelQuality
    quantiles: list[QuantileRow]
    selection: SelectionQuality
    sessions: list[SessionSelection]
    probability: ProbabilityQuality
    trading: TradingQuality


def evaluate(predictions: Sequence[Prediction], *, label: str = "") -> Evaluation:
    sessions = session_selection(predictions)
    return Evaluation(
        label=label,
        model=model_quality(predictions),
        quantiles=quantile_table(predictions),
        selection=selection_quality(sessions),
        sessions=sessions,
        probability=probability_quality(predictions),
        trading=trading_quality(predictions),
    )


def from_research_rows(
    rows: Sequence[dict[str, Any]], *, sectors: dict[str, str] | None = None
) -> list[Prediction]:
    """Map ``research.walk`` output onto the shared shape."""

    sectors = sectors or {}
    out = []
    for row in rows:
        if row.get("actual_return") is None or row.get("predicted_return") is None:
            continue
        out.append(
            Prediction(
                date=str(row["date"]),
                ticker=str(row["ticker"]),
                predicted_return=float(row["predicted_return"]),
                actual_return=float(row["actual_return"]),
                probability_up=(
                    None
                    if row.get("probability_up") is None
                    else float(row["probability_up"])
                ),
                signal=str(row.get("signal") or "NO_BUY"),
                net_profit_jpy=(
                    None
                    if row.get("net_profit_jpy") is None
                    else float(row["net_profit_jpy"])
                ),
                gross_profit_jpy=(
                    None
                    if row.get("gross_profit_jpy") is None
                    else float(row["gross_profit_jpy"])
                ),
                cost_jpy=(
                    None if row.get("cost_jpy") is None else float(row["cost_jpy"])
                ),
                sector=sectors.get(str(row["ticker"])),
            )
        )
    return out


@dataclass(frozen=True, slots=True)
class GroupQuality:
    """One ticker or one sector, scored on the layer that has the sample.

    Direction accuracy comes with the count it rests on and a binomial z, so a
    69.8% that is really 37 of 53 cannot be read as a settled fact. Splitting a
    small sample by group multiplies the number of chances to find something,
    which is exactly how noise gets adopted; the z here is unadjusted for that
    and the caller is expected to say so.
    """

    name: str
    count: int
    sessions: int
    direction_accuracy: float
    direction_z: float | None
    spearman: float | None
    mae: float
    predicted_mean: float
    actual_mean: float
    traded: int
    net_jpy: float


def _binomial_z(hits: int, count: int) -> float | None:
    """How far a hit rate sits from a coin toss, in standard errors."""

    if count < 5:
        return None
    expected = count * 0.5
    deviation = math.sqrt(count * 0.25)
    return float((hits - expected) / deviation) if deviation else None


def group_quality(
    predictions: Sequence[Prediction], *, by: str = "ticker"
) -> list[GroupQuality]:
    """Split by ticker or sector. Rows are returned worst-first by realised P&L."""

    groups: dict[str, list[Prediction]] = {}
    for item in predictions:
        key = item.ticker if by == "ticker" else (item.sector or "—")
        groups.setdefault(key, []).append(item)
    rows = []
    for name, items in groups.items():
        hits = sum(1 for p in items if p.direction_correct)
        predicted = np.array([p.predicted_return for p in items], dtype=float)
        actual = np.array([p.actual_return for p in items], dtype=float)
        traded = [p for p in items if p.signal == "BUY"]
        rows.append(
            GroupQuality(
                name=name,
                count=len(items),
                sessions=len({p.date for p in items}),
                direction_accuracy=hits / len(items),
                direction_z=_binomial_z(hits, len(items)),
                spearman=spearman(predicted, actual),
                mae=float(np.abs(predicted - actual).mean()),
                predicted_mean=float(predicted.mean()),
                actual_mean=float(actual.mean()),
                traded=len(traded),
                net_jpy=float(
                    sum(float(p.net_profit_jpy or 0.0) for p in traded)
                ),
            )
        )
    return sorted(rows, key=lambda r: r.net_jpy)
