"""Trading-layer rules applied to predictions that already exist.

The model produces a number per ticker per session. Everything after that --
how many to hold, how to weight them, whether to cap a sector, whether to short
the bottom -- is a separate decision layer, and it was tangled up with the
model. Separating it means a rule can be changed and measured without refitting
anything, and a model change can be judged before any rule is applied to it.

Every rule here is scored the same way: realised return, minus the configured
round-trip cost, aggregated to the session first and only then across sessions.
Same-day names move together, so the session is the unit that carries
information; the trade is not.

Nothing here chooses a rule. Picking the best row of a table computed on the
same sessions that produced it is the overfitting this repository has already
been bitten by, and the table says so.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np

from research.evaluation import Prediction, _t_statistic


@dataclass(frozen=True, slots=True)
class RuleResult:
    """One rule's realised outcome, at session level."""

    name: str
    sessions: int
    positions: int
    mean_position_return: float | None
    daily_mean_return: float | None
    daily_sd_return: float | None
    daily_t: float | None
    total_return: float
    winning_sessions: int
    losing_sessions: int
    max_drawdown: float

    @property
    def total_jpy_per_million(self) -> float:
        """Yen on one million per position, so rules with different counts compare."""

        return self.total_return * 1_000_000


def _drawdown(daily: Sequence[float]) -> float:
    if not daily:
        return 0.0
    equity = np.cumsum(np.asarray(daily, dtype=float))
    peak = np.maximum.accumulate(equity)
    return float((equity - peak).min())


def _session_map(
    predictions: Sequence[Prediction],
) -> dict[str, list[Prediction]]:
    sessions: dict[str, list[Prediction]] = {}
    for item in predictions:
        sessions.setdefault(item.date, []).append(item)
    return sessions


def _weights(rows: Sequence[Prediction], scheme: str) -> np.ndarray:
    """Position weights within one session, always summing to one."""

    if scheme == "equal" or not rows:
        return np.full(len(rows), 1.0 / max(len(rows), 1))
    if scheme == "predicted":
        raw = np.array([max(r.predicted_return, 0.0) for r in rows], dtype=float)
    elif scheme == "confidence":
        raw = np.array(
            [max((r.probability_up or 0.5) - 0.5, 0.0) for r in rows], dtype=float
        )
    else:
        raise ValueError(f"unknown weighting scheme: {scheme}")
    if raw.sum() <= 0:
        return np.full(len(rows), 1.0 / len(rows))
    return np.asarray(raw / raw.sum(), dtype=float)


def _apply_sector_cap(
    rows: Sequence[Prediction], cap: int | None
) -> list[Prediction]:
    """Keep at most ``cap`` names per sector, strongest forecast first.

    Buying four banks is one bet with four tickets. The cap is how that stops
    being invisible.
    """

    if cap is None:
        return list(rows)
    kept: list[Prediction] = []
    seen: dict[str, int] = {}
    for row in sorted(rows, key=lambda r: -r.predicted_return):
        sector = row.sector or row.ticker
        if seen.get(sector, 0) >= cap:
            continue
        seen[sector] = seen.get(sector, 0) + 1
        kept.append(row)
    return kept


def evaluate_rule(
    predictions: Sequence[Prediction],
    *,
    name: str,
    top_n: int | None = None,
    short_n: int | None = None,
    sector_cap: int | None = None,
    weighting: str = "equal",
    cost_per_position: float = 0.00165,
    signal_only: bool = False,
) -> RuleResult:
    """Score one selection rule over every session, net of cost.

    ``signal_only`` reproduces the rule production actually runs: whatever the
    stored signal says, with no ranking of its own.
    """

    sessions = _session_map(predictions)
    daily: list[float] = []
    positions = 0
    position_returns: list[float] = []
    for day in sorted(sessions):
        rows = sessions[day]
        if signal_only:
            chosen = [r for r in rows if r.signal == "BUY"]
        else:
            chosen = _apply_sector_cap(rows, sector_cap)
            chosen = sorted(chosen, key=lambda r: -r.predicted_return)
            if top_n is not None:
                chosen = chosen[:top_n]
        shorts: list[Prediction] = []
        if short_n:
            ranked = sorted(rows, key=lambda r: r.predicted_return)
            shorts = ranked[:short_n]
        if not chosen and not shorts:
            daily.append(0.0)
            continue
        long_weights = _weights(chosen, weighting)
        long_return = float(
            sum(
                w * (r.actual_return - cost_per_position)
                for w, r in zip(long_weights, chosen, strict=True)
            )
        )
        short_return = 0.0
        if shorts:
            short_weights = _weights(shorts, "equal")
            short_return = float(
                sum(
                    w * (-r.actual_return - cost_per_position)
                    for w, r in zip(short_weights, shorts, strict=True)
                )
            )
            # A long book and a short book of equal size split the capital.
            long_return, short_return = long_return / 2, short_return / 2
        daily.append(long_return + short_return)
        positions += len(chosen) + len(shorts)
        position_returns.extend(r.actual_return - cost_per_position for r in chosen)
        position_returns.extend(-r.actual_return - cost_per_position for r in shorts)

    array = np.asarray(daily, dtype=float)
    return RuleResult(
        name=name,
        sessions=len(daily),
        positions=positions,
        mean_position_return=(
            float(np.mean(position_returns)) if position_returns else None
        ),
        daily_mean_return=float(array.mean()) if len(array) else None,
        daily_sd_return=float(array.std(ddof=1)) if len(array) > 1 else None,
        daily_t=_t_statistic(array),
        total_return=float(array.sum()),
        winning_sessions=int((array > 0).sum()),
        losing_sessions=int((array < 0).sum()),
        max_drawdown=_drawdown(daily),
    )


def standard_rules(
    predictions: Sequence[Prediction], *, cost_per_position: float = 0.00165
) -> list[RuleResult]:
    """The comparison the operator asked for, in one pass over one dataset.

    The control comes first deliberately: any rule that cannot beat holding the
    whole universe has destroyed value rather than added it, and that is the
    comparison most easily left out.
    """

    def run(
        name: str,
        *,
        top_n: int | None = None,
        short_n: int | None = None,
        sector_cap: int | None = None,
        weighting: str = "equal",
        signal_only: bool = False,
    ) -> RuleResult:
        return evaluate_rule(
            predictions,
            name=name,
            top_n=top_n,
            short_n=short_n,
            sector_cap=sector_cap,
            weighting=weighting,
            signal_only=signal_only,
            cost_per_position=cost_per_position,
        )

    return [
        run("対照: 全銘柄を毎日持つ", top_n=None),
        run("本番の現行ルール（保存済みsignal）", signal_only=True),
        run("Top1", top_n=1),
        run("Top3", top_n=3),
        run("Top5", top_n=5),
        run("Top10", top_n=10),
        run("Top5・予測加重", top_n=5, weighting="predicted"),
        run("Top5・確信度加重", top_n=5, weighting="confidence"),
        run("Top5・1セクター1銘柄まで", top_n=5, sector_cap=1),
        run("Top5・1セクター2銘柄まで", top_n=5, sector_cap=2),
        run("Top3ロング + Bottom3ショート", top_n=3, short_n=3),
        run("Top5ロング + Bottom5ショート", top_n=5, short_n=5),
    ]
