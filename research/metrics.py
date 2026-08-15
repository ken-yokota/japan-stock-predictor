"""Score a window by its daily cross-section instead of by counting hits.

Direction accuracy asks, of every prediction, whether the sign was right. That
sounds like 1,078 observations and behaves like about 101: the 22 tickers of one
morning share a market, so their errors move together, and the paired sign test
that follows collapses each day to a single bit. Measured here, the smallest
difference such a test can resolve is about 3.1pp of accuracy, while four
indicator proposals in a row measured between +0.19 and +0.93pp. Those results
were reported as "not adopted" when the truthful reading is that the test could
not tell.

Rank IC asks a different question of the same predictions: within one morning,
did the stocks predicted to do better actually do better? The ranking is taken
across tickers on each day, so all 22 contribute, and the shared market move
cancels out of a ranking rather than dominating it. What is left is one number
per day, and days really are close to independent - which is what makes the
usual standard error apply.

Two things are reported that are easy to omit and change the reading:

``lag1_autocorrelation``
    If daily ICs are serially correlated, days are not independent, the
    denominator is too small and every p-value here is optimistic.

``detectable_ic``
    The smallest mean IC this many days could distinguish from zero at 80%
    power. Comparing a result to this before interpreting it is the whole point
    of the module; a measurement below it is not evidence of absence.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

import numpy as np
import pandas as pd

# Spearman on two points is +/-1 by construction and carries no information.
MINIMUM_NAMES = 3

# Two-sided 5% with 80% power: z(0.975) + z(0.80).
_POWER_CONSTANT = 1.959963985 + 0.841621234


def _spearman(frame: pd.DataFrame) -> float:
    """One morning's rank correlation between prediction and outcome."""

    if len(frame) < MINIMUM_NAMES:
        return float("nan")
    predicted = frame["predicted_return"].rank()
    actual = frame["actual_return"].rank()
    if predicted.nunique() < 2 or actual.nunique() < 2:
        # A day with no spread to rank cannot agree or disagree with anything.
        return float("nan")
    return float(predicted.corr(actual, method="pearson"))


def _pearson(frame: pd.DataFrame) -> float:
    """One morning's linear correlation between prediction and outcome."""

    if len(frame) < MINIMUM_NAMES:
        return float("nan")
    predicted = frame["predicted_return"].astype(float)
    actual = frame["actual_return"].astype(float)
    if predicted.nunique() < 2 or actual.nunique() < 2:
        return float("nan")
    return float(predicted.corr(actual, method="pearson"))


def pearson_ic_series(predictions: pd.DataFrame) -> pd.Series:
    """Daily Pearson IC, for comparison against the rank version.

    It is reported beside rank IC rather than instead of it. Pearson answers a
    question about magnitudes and is moved by a single violent name, which is
    exactly the day a ranking is unbothered by; when the two disagree, the
    disagreement is the finding.
    """

    return _daily(predictions, _pearson)


def _daily(
    predictions: pd.DataFrame, statistic: Callable[[pd.DataFrame], float]
) -> pd.Series:
    if predictions.empty:
        return pd.Series(dtype=float)
    frame = predictions
    if "date" not in frame.columns:
        frame = frame.reset_index()
    required = {"date", "predicted_return", "actual_return"}
    missing = required - set(frame.columns)
    if missing:
        raise KeyError(f"predictions are missing {sorted(missing)}")
    daily = frame.groupby("date", sort=True).apply(statistic, include_groups=False)
    return daily.dropna().astype(float)


def rank_ic_series(predictions: pd.DataFrame) -> pd.Series:
    """Daily rank IC, indexed by date, with unrankable days dropped."""

    return _daily(predictions, _spearman)


@dataclass(frozen=True, slots=True)
class RankICSummary:
    """A window's cross-sectional skill, with the power to read it against."""

    days: int
    mean: float
    standard_deviation: float
    information_ratio: float
    t_statistic: float
    p_value: float | None
    lag1_autocorrelation: float | None
    detectable_ic: float
    confidence_low: float = 0.0
    confidence_high: float = 0.0

    @property
    def effect_size(self) -> float:
        """Cohen's d for a one-sample mean: the IR, under its statistical name."""

        return self.information_ratio

    @property
    def is_detectable(self) -> bool:
        """Does the measured mean clear the 80%-power floor for this window?

        This is a question about design power, not about this result: the
        floor uses 2.80 standard errors while significance needs about 2.04,
        so a genuinely significant result can sit below it. It is therefore
        only consulted to interpret a null - which is the case that was
        being misreported.
        """

        return abs(self.mean) >= self.detectable_ic

    def verdict(self) -> str:
        if self.days < 2:
            return "判定不能: 有効な日数が足りません。"
        if self.p_value is not None and self.p_value < 0.05:
            direction = "正" if self.mean > 0 else "負"
            return f"有意（{direction}）: p={self.p_value:.4f}、{self.days}日。"
        if not self.is_detectable:
            return (
                f"判定不能: 実測IC {self.mean:+.4f} は検出下限 "
                f"{self.detectable_ic:.4f} 未満です。効果がないのではなく、"
                "測れていません。"
            )
        # "Not significant" is not "the same". Only an interval that excludes
        # everything worth acting on supports the second claim.
        width = self.confidence_high - self.confidence_low
        if width and max(abs(self.confidence_low), abs(self.confidence_high)) < 0.02:
            return (
                f"実質的に同等: p={self.p_value:.4f}、95%CI "
                f"[{self.confidence_low:+.4f}, {self.confidence_high:+.4f}]。"
            )
        return (
            f"有意差なし（同等ではない）: p={self.p_value:.4f}、95%CI "
            f"[{self.confidence_low:+.4f}, {self.confidence_high:+.4f}]、"
            f"{self.days}日。"
        )


def summarise_rank_ic(daily: pd.Series) -> RankICSummary:
    """Turn a daily IC series into its mean, its error, and its detection floor."""

    values = np.asarray(daily.dropna(), dtype=float)
    days = int(values.size)
    if days == 0:
        return RankICSummary(0, 0.0, 0.0, 0.0, 0.0, None, None, float("inf"))

    mean = float(values.mean())
    # One degree of freedom is spent on the mean; ddof=1 or the error is small.
    deviation = float(values.std(ddof=1)) if days > 1 else 0.0
    ratio = mean / deviation if deviation > 0 else 0.0
    error = deviation / np.sqrt(days) if deviation > 0 else 0.0
    t_statistic = mean / error if error > 0 else 0.0

    p_value: float | None = None
    if days > 1 and error > 0:
        from scipy.stats import t as student  # type: ignore[import-untyped]

        p_value = float(2 * student.sf(abs(t_statistic), df=days - 1))

    lag1: float | None = None
    if days > 2:
        current, following = values[:-1], values[1:]
        if current.std() > 0 and following.std() > 0:
            lag1 = float(np.corrcoef(current, following)[0, 1])

    detectable = (
        _POWER_CONSTANT * deviation / np.sqrt(days) if deviation > 0 else float("inf")
    )

    # A 95% interval, so "no significant difference" can be told apart from
    # "equivalent": the first is an interval that includes zero, the second is
    # an interval narrow enough to exclude anything that would matter.
    low = high = mean
    if days > 1 and error > 0:
        from scipy.stats import t as student

        half = float(student.ppf(0.975, df=days - 1)) * error
        low, high = mean - half, mean + half

    return RankICSummary(
        days=days,
        mean=mean,
        standard_deviation=deviation,
        information_ratio=float(ratio),
        t_statistic=float(t_statistic),
        p_value=p_value,
        lag1_autocorrelation=lag1,
        detectable_ic=float(detectable),
        confidence_low=float(low),
        confidence_high=float(high),
    )


def paired_rank_ic(candidate: pd.DataFrame, baseline: pd.DataFrame) -> RankICSummary:
    """Summarise the day-by-day IC difference between two arms.

    The two are reduced to the (date, ticker) pairs both produced before either
    is ranked. Scoring one arm on names the other never predicted would compare
    the universes rather than the models.
    """

    left = candidate.set_index(["date", "ticker"]) if "date" in candidate else candidate
    right = baseline.set_index(["date", "ticker"]) if "date" in baseline else baseline
    shared = left.index.intersection(right.index)
    candidate_daily = rank_ic_series(left.loc[shared].reset_index())
    baseline_daily = rank_ic_series(right.loc[shared].reset_index())
    common_days = candidate_daily.index.intersection(baseline_daily.index)
    difference = candidate_daily.loc[common_days] - baseline_daily.loc[common_days]
    return summarise_rank_ic(difference)
