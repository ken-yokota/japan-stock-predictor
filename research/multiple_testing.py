"""Was that ticker really good, or was it the best of twenty-two tries?

Toyota came out of the 250-session comparison at 59.2% direction accuracy with
z = 2.91, which reads as decisive until you remember that twenty-two tickers
were scored and the largest of twenty-two draws is large by construction. The
same arithmetic that makes a lottery winner unremarkable applies here.

Three things are reported and they answer different questions:

    raw p          this ticker on its own, ignoring that others were tested
    BH-adjusted q  the same ticker, controlling the share of false discoveries
                   among everything called significant
    bootstrap CI   how far the estimate itself moves when the sessions are
                   resampled, which the p-value says nothing about

Nothing here decides anything. It exists so a discovery is labelled as a
discovery rather than promoted to a trading rule by the strength of its z.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np

# Resamples for the interval. Enough that the 2.5th and 97.5th percentiles are
# stable to about a tenth of a percentage point, which is finer than the
# differences being argued over.
BOOTSTRAP_SAMPLES = 5000

RANDOM_STATE = 42


@dataclass(frozen=True, slots=True)
class TickerTest:
    """One ticker's directional record, and how much of it survives scrutiny."""

    ticker: str
    sessions: int
    predictions: int
    hits: int
    accuracy: float
    z: float
    raw_p: float
    adjusted_q: float
    ci_low: float
    ci_high: float

    @property
    def survives_fdr(self) -> bool:
        return self.adjusted_q < 0.05

    @property
    def interval_excludes_chance(self) -> bool:
        return self.ci_low > 0.5


def _normal_two_sided_p(z: float) -> float:
    return float(math.erfc(abs(z) / math.sqrt(2)))


def _block_bootstrap(
    by_session: Sequence[Sequence[bool]], samples: int = BOOTSTRAP_SAMPLES
) -> tuple[float, float]:
    """Resample whole sessions, not individual predictions.

    Same-day names move together, so drawing predictions independently would
    treat one correlated day as many observations and return an interval far
    too narrow -- the same mistake as reading 46 trades as 46 samples when they
    came from 8 days.
    """

    if not by_session:
        return (float("nan"), float("nan"))
    rng = np.random.default_rng(RANDOM_STATE)
    sessions = [np.asarray(day, dtype=float) for day in by_session if len(day)]
    if not sessions:
        return (float("nan"), float("nan"))
    count = len(sessions)
    means = np.empty(samples, dtype=float)
    for index in range(samples):
        drawn = rng.integers(0, count, size=count)
        pooled = np.concatenate([sessions[i] for i in drawn])
        means[index] = pooled.mean()
    return float(np.percentile(means, 2.5)), float(np.percentile(means, 97.5))


def benjamini_hochberg(p_values: Sequence[float]) -> list[float]:
    """Adjusted q-values, controlling the false discovery rate.

    Bonferroni would divide the threshold by twenty-two and reject almost
    everything; BH asks instead what share of the things called significant are
    likely to be wrong, which is the question worth asking when the point is to
    decide where to look next.
    """

    count = len(p_values)
    if count == 0:
        return []
    order = sorted(range(count), key=lambda i: p_values[i])
    adjusted = [0.0] * count
    running = 1.0
    for rank, index in enumerate(reversed(order), start=1):
        position = count - rank + 1
        value = min(1.0, p_values[index] * count / position)
        running = min(running, value)
        adjusted[index] = running
    return adjusted


def evaluate_tickers(
    hits_by_ticker: dict[str, list[list[bool]]],
) -> list[TickerTest]:
    """Score every ticker, then correct for having scored every ticker.

    ``hits_by_ticker`` maps a ticker to its sessions, each session being the
    list of correct/incorrect calls made that day. The nesting matters: the
    bootstrap resamples sessions, and flattening it here would silently restore
    the independence assumption the nesting exists to avoid.
    """

    measured: list[TickerTest] = []
    for ticker, sessions in sorted(hits_by_ticker.items()):
        flat = [hit for day in sessions for hit in day]
        if not flat:
            continue
        total = len(flat)
        hits = int(sum(flat))
        deviation = math.sqrt(total * 0.25)
        z = (hits - total * 0.5) / deviation if deviation else 0.0
        low, high = _block_bootstrap(sessions)
        measured.append(
            TickerTest(
                ticker=ticker,
                sessions=len(sessions),
                predictions=total,
                hits=hits,
                accuracy=hits / total,
                z=z,
                raw_p=_normal_two_sided_p(z),
                adjusted_q=1.0,  # filled in below, once every ticker is known
                ci_low=low,
                ci_high=high,
            )
        )

    adjusted = benjamini_hochberg([item.raw_p for item in measured])
    return [
        TickerTest(
            ticker=item.ticker,
            sessions=item.sessions,
            predictions=item.predictions,
            hits=item.hits,
            accuracy=item.accuracy,
            z=item.z,
            raw_p=item.raw_p,
            adjusted_q=q_value,
            ci_low=item.ci_low,
            ci_high=item.ci_high,
        )
        for item, q_value in zip(measured, adjusted, strict=True)
    ]
