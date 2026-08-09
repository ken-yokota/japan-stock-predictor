"""Per-ticker evidence that a BUY signal beats simply owning the stock.

The question is not "did BUY days make money" — a rising month makes every day
look good. It is "did BUY days do better than this ticker's ordinary days",
which is what buying arbitrarily would have given you.

So each ticker gets a 2x2 table: sessions where a BUY fired against sessions
where it did not, split by whether the stock rose. Fisher's exact test is used
rather than a chi-square or a normal approximation because the counts here are
tiny and will stay tiny for months; an approximation would produce confident
numbers from four observations.

Two things this module refuses to let a reader forget:

* **Twenty-two tickers tested at p < 0.05 produce about one winner by chance.**
  Raw p-values are therefore reported beside Benjamini-Hochberg q-values, and
  the verdict uses the q-value.
* **A ticker with three BUY days has no evidence either way.** Those are
  labelled as such rather than being given a p-value that reads as a finding.

Pure arithmetic over already-fetched rows: no database, no scipy, no fitting.
"""

from __future__ import annotations

import random
from collections import defaultdict
from dataclasses import dataclass
from math import comb
from typing import Any

# Below this many BUY sessions, the exact test cannot reach significance at any
# reasonable effect size, so a p-value would only mislead.
MINIMUM_SIGNALS_FOR_EVIDENCE = 10
DISCOVERY_RATE = 0.10


def fisher_exact_greater(
    signal_up: int, signal_down: int, other_up: int, other_down: int
) -> float:
    """One-sided Fisher's exact p that BUY sessions rise more often.

    One-sided on purpose: the claim being tested is that the signal helps, not
    that it differs. A two-sided test would award significance to a signal that
    reliably picks losers, which is not the question.
    """

    rows = (signal_up + signal_down, other_up + other_down)
    columns = (signal_up + other_up, signal_down + other_down)
    total = sum(rows)
    if total == 0 or 0 in rows or 0 in columns:
        return 1.0

    def probability(count: int) -> float:
        return (
            comb(columns[0], count)
            * comb(columns[1], rows[0] - count)
            / comb(total, rows[0])
        )

    highest = min(rows[0], columns[0])
    return float(sum(probability(count) for count in range(signal_up, highest + 1)))


def benjamini_hochberg(
    p_values: list[float], rate: float = DISCOVERY_RATE
) -> list[float]:
    """Return q-values controlling the false discovery rate.

    Testing every ticker separately means the best-looking one is partly the
    winner of a lottery. Controlling the discovery rate answers "of the tickers
    I call significant, what share are flukes" — the question an operator
    scanning a table actually has.
    """

    count = len(p_values)
    if count == 0:
        return []
    order = sorted(range(count), key=lambda index: p_values[index])
    q_values = [1.0] * count
    running = 1.0
    for rank, index in reversed(list(enumerate(order, start=1))):
        running = min(running, p_values[index] * count / rank)
        q_values[index] = min(1.0, running)
    return q_values


@dataclass(frozen=True, slots=True)
class TickerEvidence:
    """One ticker's record, and whether it amounts to anything yet."""

    ticker: str
    sessions: int
    signals: int
    signal_up: int
    signal_down: int
    other_up: int
    other_down: int
    signal_win_rate: float | None
    baseline_win_rate: float | None
    signal_mean_return: float | None
    baseline_mean_return: float | None
    p_value: float
    q_value: float

    @property
    def edge(self) -> float | None:
        """How much better BUY days were than ordinary days, in points."""

        if self.signal_win_rate is None or self.baseline_win_rate is None:
            return None
        return self.signal_win_rate - self.baseline_win_rate

    @property
    def has_enough_signals(self) -> bool:
        return self.signals >= MINIMUM_SIGNALS_FOR_EVIDENCE

    @property
    def verdict(self) -> str:
        if not self.has_enough_signals:
            return (
                f"判定不能: BUY {self.signals}回では、どんな差が出ても偶然と"
                f"区別できません (最低{MINIMUM_SIGNALS_FOR_EVIDENCE}回必要)"
            )
        if self.q_value < DISCOVERY_RATE:
            return (
                f"有意: 適当に買うより {(self.edge or 0) * 100:+.1f}pt 高く、"
                f"多重比較を補正しても残ります (q={self.q_value:.3f})"
            )
        return (
            f"有意差なし: 差 {(self.edge or 0) * 100:+.1f}pt は "
            f"偶然の範囲です (q={self.q_value:.3f})"
        )


def _rate(up: int, total: int) -> float | None:
    return (up / total) if total else None


def _mean(values: list[float]) -> float | None:
    return (sum(values) / len(values)) if values else None


def evaluate_tickers(rows: list[dict[str, Any]]) -> list[TickerEvidence]:
    """Score every ticker that has at least one settled session.

    A session counts only once its close is known. "Rose" means the open-to-close
    return was positive, which is the move the strategy actually trades.
    """

    by_ticker: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        if row.get("actual_return") is None:
            continue
        by_ticker[str(row["ticker"])].append(row)

    counts: list[tuple[str, int, int, int, int, list[float], list[float]]] = []
    for ticker in sorted(by_ticker):
        signal_up = signal_down = other_up = other_down = 0
        signal_returns: list[float] = []
        other_returns: list[float] = []
        for row in by_ticker[ticker]:
            rose = float(row["actual_return"]) > 0.0
            if row.get("signal") == "BUY":
                signal_returns.append(float(row["actual_return"]))
                if rose:
                    signal_up += 1
                else:
                    signal_down += 1
            else:
                other_returns.append(float(row["actual_return"]))
                if rose:
                    other_up += 1
                else:
                    other_down += 1
        counts.append(
            (
                ticker,
                signal_up,
                signal_down,
                other_up,
                other_down,
                signal_returns,
                other_returns,
            )
        )

    p_values = [
        fisher_exact_greater(signal_up, signal_down, other_up, other_down)
        for _, signal_up, signal_down, other_up, other_down, _, _ in counts
    ]
    q_values = benjamini_hochberg(p_values)

    evidence: list[TickerEvidence] = []
    for (
        (
            ticker,
            signal_up,
            signal_down,
            other_up,
            other_down,
            signal_returns,
            other_returns,
        ),
        p_value,
        q_value,
    ) in zip(counts, p_values, q_values, strict=True):
        signals = signal_up + signal_down
        others = other_up + other_down
        evidence.append(
            TickerEvidence(
                ticker=ticker,
                sessions=signals + others,
                signals=signals,
                signal_up=signal_up,
                signal_down=signal_down,
                other_up=other_up,
                other_down=other_down,
                signal_win_rate=_rate(signal_up, signals),
                baseline_win_rate=_rate(other_up, others),
                signal_mean_return=_mean(signal_returns),
                baseline_mean_return=_mean(other_returns),
                p_value=p_value,
                q_value=q_value,
            )
        )
    return evidence


@dataclass(frozen=True, slots=True)
class OverallEvidence:
    """The same question asked across every ticker at once.

    Pooling raises a problem the per-ticker tables do not have: the 22 stocks
    trade on the same days and move together, so their sessions are not
    independent observations. Fisher's exact test assumes they are, and would
    report a p-value far smaller than the evidence supports.

    The verdict therefore comes from a block bootstrap that resamples whole
    dates, keeping each day's stocks together, so a single strong market day
    counts as one observation rather than twenty-two. The naive p-value is kept
    beside it only to show how much the independence assumption flatters it.
    """

    sessions: int
    signals: int
    signal_up: int
    signal_down: int
    other_up: int
    other_down: int
    signal_win_rate: float | None
    baseline_win_rate: float | None
    signal_mean_return: float | None
    baseline_mean_return: float | None
    trading_days: int
    naive_p_value: float
    block_bootstrap_p_value: float | None
    iterations: int

    @property
    def edge(self) -> float | None:
        if self.signal_win_rate is None or self.baseline_win_rate is None:
            return None
        return self.signal_win_rate - self.baseline_win_rate

    @property
    def verdict(self) -> str:
        if self.block_bootstrap_p_value is None:
            return (
                f"判定不能: BUYが出た営業日が {self.trading_days} 日では、"
                "日単位のブートストラップができません。"
            )
        if self.signals < MINIMUM_SIGNALS_FOR_EVIDENCE:
            return f"判定不能: BUY {self.signals}回では結論が出せません。"
        if self.block_bootstrap_p_value < 0.05:
            return (
                f"有意: 適当に買うより {(self.edge or 0) * 100:+.1f}pt 高く、"
                f"日単位ブートストラップで p={self.block_bootstrap_p_value:.3f}"
            )
        return (
            f"有意差なし: 差 {(self.edge or 0) * 100:+.1f}pt は、"
            f"日単位で見ると偶然の範囲です (p={self.block_bootstrap_p_value:.3f})"
        )


def evaluate_overall(
    rows: list[dict[str, Any]], *, iterations: int = 2000, seed: int = 42
) -> OverallEvidence:
    """Pool every ticker, and test the pooled edge by resampling whole days."""

    by_date: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        if row.get("actual_return") is None:
            continue
        by_date[str(row["date"])].append(row)

    def edge_of(days: list[list[dict[str, Any]]]) -> float | None:
        signal_up = signal_total = other_up = other_total = 0
        for day in days:
            for row in day:
                rose = float(row["actual_return"]) > 0.0
                if row.get("signal") == "BUY":
                    signal_total += 1
                    signal_up += rose
                else:
                    other_total += 1
                    other_up += rose
        if not signal_total or not other_total:
            return None
        return signal_up / signal_total - other_up / other_total

    days = [by_date[key] for key in sorted(by_date)]
    signal_up = signal_down = other_up = other_down = 0
    signal_returns: list[float] = []
    other_returns: list[float] = []
    for day in days:
        for row in day:
            value = float(row["actual_return"])
            rose = value > 0.0
            if row.get("signal") == "BUY":
                signal_returns.append(value)
                signal_up += rose
                signal_down += not rose
            else:
                other_returns.append(value)
                other_up += rose
                other_down += not rose

    observed = edge_of(days)
    bootstrap_p: float | None = None
    # One day is one observation, so a handful of days cannot support a test
    # however many stocks they contain.
    if observed is not None and len(days) >= 5:
        generator = random.Random(seed)
        worse_or_equal = 0
        valid = 0
        for _ in range(iterations):
            sample = [generator.choice(days) for _ in days]
            resampled = edge_of(sample)
            if resampled is None:
                continue
            valid += 1
            # Centering on the observed edge tests the null that the true edge
            # is zero, rather than re-testing the sample against itself.
            worse_or_equal += (resampled - observed) <= -observed
        bootstrap_p = (worse_or_equal + 1) / (valid + 1) if valid else None

    return OverallEvidence(
        sessions=signal_up + signal_down + other_up + other_down,
        signals=signal_up + signal_down,
        signal_up=signal_up,
        signal_down=signal_down,
        other_up=other_up,
        other_down=other_down,
        signal_win_rate=_rate(signal_up, signal_up + signal_down),
        baseline_win_rate=_rate(other_up, other_up + other_down),
        signal_mean_return=_mean(signal_returns),
        baseline_mean_return=_mean(other_returns),
        trading_days=len(days),
        naive_p_value=fisher_exact_greater(
            signal_up, signal_down, other_up, other_down
        ),
        block_bootstrap_p_value=bootstrap_p,
        iterations=iterations,
    )
