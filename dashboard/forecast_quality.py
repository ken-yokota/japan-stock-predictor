"""Is the point prediction or the distribution's median closer to the truth?

Two different models answer the same question every morning. The traded number
is Ridge's conditional mean; the curve the dashboard draws is a set of quantile
regressions, and its median sits much nearer zero. Which one is closer to what
happens is a measurable question, and answering it by eye on a few sessions is
how a coin flip gets promoted.

So the counting lives here and the *verdict* is withheld until there is enough
of it. The unit of evidence is the **session**, not the row: the twenty-two
tickers on one morning ride the same market and are not twenty-two independent
observations -- the significance module already makes that argument for its own
test, with a block bootstrap, and it applies unchanged here.

A new module rather than an addition to an existing one, for the deployment
reason the other split-out dashboard modules record: Streamlit Cloud keeps
imported modules in memory while re-reading changed pages.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from dashboard.presenters import as_number, distribution_levels

# Sessions, not rows. Below this the comparison cannot separate the two models
# from each other or from doing nothing, so no verdict is offered. Matches the
# floor the all-methods comparison uses for the same reason.
MINIMUM_SESSIONS_FOR_EVIDENCE = 20

# The bands worth checking, as (nominal coverage, lower level, upper level)
# under the fitted quantile grid.
BANDS: tuple[tuple[float, float, float], ...] = (
    (0.90, 0.05, 0.95),
    (0.80, 0.10, 0.90),
    (0.50, 0.25, 0.75),
)


@dataclass(frozen=True, slots=True)
class BandCoverage:
    """How often the outcome actually landed inside a nominal band."""

    nominal: float
    inside: int
    total: int

    @property
    def observed(self) -> float | None:
        return self.inside / self.total if self.total else None


@dataclass(frozen=True, slots=True)
class ForecastQuality:
    """The comparison, and whether it is yet allowed to mean anything."""

    sessions: int
    observations: int
    point_mae: float | None
    median_mae: float | None
    zero_mae: float | None
    point_hits: int
    median_hits: int
    scored: int
    bands: tuple[BandCoverage, ...]

    @property
    def has_enough_evidence(self) -> bool:
        return self.sessions >= MINIMUM_SESSIONS_FOR_EVIDENCE

    @property
    def verdict(self) -> str:
        if not self.observations:
            return "分布と実績がそろった営業日がまだありません。"
        if not self.has_enough_evidence:
            return (
                f"判定不能 — {self.sessions}営業日では点予測と分布P50を区別できません"
                f"（最低{MINIMUM_SESSIONS_FOR_EVIDENCE}営業日必要）。"
                "同じ日の銘柄は一緒に動くので、独立した観測は銘柄数ではなく日数です。"
            )
        if self.point_mae is None or self.median_mae is None:
            return "判定不能 — 誤差を計算できませんでした。"
        gap = (self.median_mae - self.point_mae) * 100
        nearer = "点予測" if gap > 0 else "分布P50"
        return f"{nearer}のほうが平均絶対誤差で {abs(gap):.3f}pt 近いです。"


def _usable(
    rows: Iterable[Mapping[str, Any]],
) -> list[tuple[Mapping[str, Any], float, float, dict[float, float]]]:
    """Rows that settled *and* carry a curve, with the three numbers pulled out.

    Both conditions matter. A settled row without a curve cannot enter the
    comparison, and a curve whose session never closed has nothing to be
    compared against; counting either would change the denominator without
    adding evidence.
    """

    output = []
    for row in rows:
        actual = as_number(row.get("actual_intraday_return"))
        point = as_number(row.get("predicted_intraday_return"))
        if actual is None or point is None:
            continue
        levels = distribution_levels(row.get("return_distribution"))
        if levels.get(0.5) is None:
            continue
        output.append((row, actual, point, levels))
    return output


def evaluate(rows: Iterable[Mapping[str, Any]]) -> ForecastQuality:
    """Compare the traded point against the curve's median, and both against 0.

    Zero is in here because it is the honest floor. "Predict no move" costs
    nothing and has no model behind it; a forecast that cannot beat it on mean
    absolute error has not yet earned the word forecast.
    """

    usable = _usable(rows)
    if not usable:
        return ForecastQuality(0, 0, None, None, None, 0, 0, 0, ())

    sessions = {str(row.get("prediction_date", "")) for row, _, _, _ in usable}
    point_errors, median_errors, zero_errors = [], [], []
    point_hits = median_hits = scored = 0
    inside: dict[float, int] = dict.fromkeys((band[0] for band in BANDS), 0)
    totals: dict[float, int] = dict.fromkeys((band[0] for band in BANDS), 0)

    for _row, actual, point, levels in usable:
        median = levels[0.5]
        point_errors.append(abs(point - actual))
        median_errors.append(abs(median - actual))
        zero_errors.append(abs(actual))
        if actual != 0.0:
            scored += 1
            point_hits += (point > 0) == (actual > 0)
            median_hits += (median > 0) == (actual > 0)
        for nominal, low_level, high_level in BANDS:
            low, high = levels.get(low_level), levels.get(high_level)
            if low is None or high is None:
                continue
            totals[nominal] += 1
            inside[nominal] += low <= actual <= high

    return ForecastQuality(
        sessions=len(sessions),
        observations=len(usable),
        point_mae=sum(point_errors) / len(point_errors),
        median_mae=sum(median_errors) / len(median_errors),
        zero_mae=sum(zero_errors) / len(zero_errors),
        point_hits=point_hits,
        median_hits=median_hits,
        scored=scored,
        bands=tuple(
            BandCoverage(nominal, inside[nominal], totals[nominal])
            for nominal, _, _ in BANDS
            if totals[nominal]
        ),
    )


def comparison_rows(quality: ForecastQuality) -> list[dict[str, object]]:
    """The three contenders side by side, zero included as the floor."""

    if not quality.observations:
        return []

    def hit(hits: int) -> str:
        if not quality.scored:
            return "—"
        return f"{hits}/{quality.scored} ({hits / quality.scored:.1%})"

    return [
        {
            "予測値": "点予測（Ridge・実際に売買判定に使用）",
            "平均絶対誤差": f"{(quality.point_mae or 0) * 100:.3f}pt",
            "方向的中": hit(quality.point_hits),
        },
        {
            "予測値": "分布P50（分位点回帰・画面の密度の中央値）",
            "平均絶対誤差": f"{(quality.median_mae or 0) * 100:.3f}pt",
            "方向的中": hit(quality.median_hits),
        },
        {
            "予測値": "常に0（何もしない基準）",
            "平均絶対誤差": f"{(quality.zero_mae or 0) * 100:.3f}pt",
            "方向的中": "—",
        },
    ]


def coverage_rows(
    quality: ForecastQuality, prior: Sequence[tuple[float, float]] = ()
) -> list[dict[str, object]]:
    """Observed against nominal, beside the larger prior study where there is one.

    The prior column is what makes this table readable on a small sample: a
    figure that reproduces a 5,500-observation study is telling you something
    even when this window is thin, and one that contradicts it is telling you
    something else.
    """

    priors = {round(nominal, 6): observed for nominal, observed in prior}
    output: list[dict[str, object]] = []
    for band in quality.bands:
        observed = band.observed
        reference = priors.get(round(band.nominal, 6))
        output.append(
            {
                "区間": f"{band.nominal:.0%}",
                "実際に入った割合": f"{observed:.1%}" if observed is not None else "—",
                "件数": f"{band.inside}/{band.total}",
                "既存OOS調査": f"{reference:.1%}" if reference is not None else "—",
            }
        )
    return output
