"""Every model family's forecast distribution, one tab each.

Self-contained on purpose, and that is worth stating because it duplicates a
little arithmetic that also lives in ``today_view``. Streamlit Cloud keeps
already-imported modules in memory while re-reading changed pages, so a module
that only reaches for long-settled names elsewhere (``distribution_levels``,
``_cumulative``) can be added to a page and deploy without a container reboot,
while one that reaches into another recently-changed module inherits that
module's staleness. Twenty lines of repeated interpolation buys that.

The substance of this module is the distinction the tabs have to carry. Eight
families publish a curve and they do **not** mean the same thing:

    conditional  lightgbm / xgboost, and the production quantile arm.
                 Fitted against pinball loss at each level, so the width
                 answers to today's inputs.
    ensemble     random_forest. The spread between the trees -- real
                 disagreement, but disagreement is not a calibrated interval.
    residual     ridge / lasso / elastic_net / mlp. The arm's own out-of-fold
                 error distribution, shifted onto today's point. Measured on
                 7203 over six sessions, the width moved by a coefficient of
                 variation of 0.008-0.044 -- which is to say it did not move.
                 It says "this model is usually this wrong", never "today is
                 uncertain".

Logistic regression has no curve and none is invented for it: it estimates a
direction probability, not a return. Presenting eight of these side by side
without the grouping would read as eight independent opinions when four of them
share a shape by construction.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

import altair as alt
import pandas as pd
import streamlit as st

from dashboard.presenters import _cumulative, as_number, distribution_levels

CONDITIONAL = "conditional"
ENSEMBLE = "ensemble"
RESIDUAL = "residual"

# What each spread construction can and cannot claim, in the operator's words.
SPREAD_NOTES: dict[str, str] = {
    CONDITIONAL: "当日の入力で幅が変わります（分位点を直接学習）",
    ENSEMBLE: (
        "木どうしの意見の割れです"
        "（幅は入力で変わりますが、区間として較正されたものではありません）"
    ),
    RESIDUAL: "過去の誤差幅をそのまま当てています。**幅は当日の入力で変わりません**",
}

SPREAD_SHORT: dict[str, str] = {
    CONDITIONAL: "条件付き",
    ENSEMBLE: "木の割れ",
    RESIDUAL: "残差（一定）",
}

# The production curve, which is not one of the arms but belongs beside them.
PRODUCTION_LABEL = "本番（分位点回帰）"

_DOWNSIDE: tuple[tuple[str, float], ...] = (("P50", 0.50), ("P75", 0.25), ("P90", 0.10))


@dataclass(frozen=True, slots=True)
class ArmCurve:
    """One family's published curve for one ticker-session."""

    name: str
    label: str
    spread_kind: str
    points: list[tuple[float, float]]
    predicted_return: float | None

    @property
    def width(self) -> float:
        """The 5%-95% span, the number the three groups differ most in."""

        return self.points[-1][1] - self.points[0][1]


def _points(payload: object) -> list[tuple[float, float]] | None:
    levels = distribution_levels(payload)
    if len(levels) < 3:
        return None
    return sorted(levels.items())


def arm_curves(row: Mapping[str, Any]) -> list[ArmCurve]:
    """Every family on this row that published a curve, production first.

    An arm whose status is not OK, or which never had a distribution, is left
    out rather than drawn flat. Logistic regression is therefore always absent,
    which is correct: it does not estimate a return.
    """

    curves: list[ArmCurve] = []
    production = _points(row.get("return_distribution"))
    if production is not None:
        curves.append(
            ArmCurve(
                name="production",
                label=PRODUCTION_LABEL,
                spread_kind=CONDITIONAL,
                points=production,
                predicted_return=as_number(row.get("predicted_intraday_return")),
            )
        )

    arms = row.get("arm_predictions") or []
    if not isinstance(arms, Sequence):
        return curves
    for arm in arms:
        if not isinstance(arm, Mapping):
            continue
        if str(arm.get("status", "")).upper() != "OK":
            continue
        points = _points(arm.get("distribution"))
        if points is None:
            continue
        curves.append(
            ArmCurve(
                name=str(arm.get("name", "")),
                label=str(arm.get("label") or arm.get("name") or "—"),
                spread_kind=str(arm.get("spread_kind") or RESIDUAL),
                points=points,
                predicted_return=as_number(arm.get("predicted_return")),
            )
        )
    return curves


def axis_for(
    curves: Sequence[ArmCurve], *, actual: float | None
) -> tuple[float, float]:
    """One return range covering every curve on the row, and the outcome.

    Shared across the tabs deliberately. Letting each family scale to its own
    range would draw a wide, uncertain forecast and a narrow, confident one at
    identical widths, which is the single comparison the tabs exist to enable.
    """

    values: list[float] = []
    for curve in curves:
        values.extend((curve.points[0][1], curve.points[-1][1]))
    if actual is not None:
        values.append(actual)
    if not values:
        return (-0.05, 0.05)
    low, high = min(values), max(values)
    if high <= low:
        low, high = low - 0.01, high + 0.01
    margin = (high - low) * 0.12
    return (low - margin, high + margin)


def quantile_at(points: Sequence[tuple[float, float]], level: float) -> float | None:
    """The return at one level, interpolated, pinned outside the fitted range."""

    if not points:
        return None
    for candidate, value in points:
        if abs(candidate - level) < 1e-9:
            return value
    if level <= points[0][0]:
        return points[0][1]
    if level >= points[-1][0]:
        return points[-1][1]
    for index in range(len(points) - 1):
        (low_level, low_value), (high_level, high_value) = (
            points[index],
            points[index + 1],
        )
        if low_level <= level <= high_level:
            if high_level == low_level:
                return low_value
            weight = (level - low_level) / (high_level - low_level)
            return low_value + weight * (high_value - low_value)
    return None


def density_frame(
    curve: ArmCurve, *, low: float, high: float, columns: int = 160
) -> pd.DataFrame | None:
    """Probability mass per equal-width column across the shared axis."""

    if columns < 1 or high <= low:
        return None
    step = (high - low) / columns
    edges = [low + step * index for index in range(columns + 1)]
    mass = [
        max(
            _cumulative(curve.points, edges[index + 1])
            - _cumulative(curve.points, edges[index]),
            0.0,
        )
        for index in range(columns)
    ]
    centres = [low + step * (index + 0.5) for index in range(columns)]
    return pd.DataFrame({"リターン (%)": [c * 100 for c in centres], "確率": mass})


def density_chart(
    curve: ArmCurve,
    *,
    low: float,
    high: float,
    actual: float | None = None,
    columns: int = 160,
) -> alt.LayerChart | alt.FacetChart | None:
    """One family's density, with its point, its downside levels, and the day."""

    frame = density_frame(curve, low=low, high=high, columns=columns)
    if frame is None:
        return None

    layers: list[Any] = [
        alt.Chart(frame)
        .mark_area(opacity=0.75)
        .encode(
            x=alt.X("リターン (%):Q", title="リターン (%)"),
            y=alt.Y("確率:Q", title="確率密度", axis=alt.Axis(labels=False)),
            tooltip=[
                alt.Tooltip("リターン (%):Q", format="+.2f"),
                alt.Tooltip("確率:Q", format=".4f"),
            ],
        )
    ]
    if curve.predicted_return is not None:
        layers.append(
            alt.Chart(pd.DataFrame({"x": [curve.predicted_return * 100]}))
            .mark_rule(strokeDash=[4, 3], size=2)
            .encode(x=alt.X("x:Q"))
        )
    risk = [
        {"x": value * 100, "label": label}
        for label, level in _DOWNSIDE
        if (value := quantile_at(curve.points, level)) is not None
    ]
    if risk:
        risk_frame = pd.DataFrame(risk)
        layers.append(
            alt.Chart(risk_frame)
            .mark_rule(color="#6b7280", strokeDash=[2, 2], size=1)
            .encode(x=alt.X("x:Q"), tooltip=["label:N"])
        )
        layers.append(
            alt.Chart(risk_frame)
            .mark_text(
                align="left", baseline="top", dx=3, dy=2, fontSize=10, color="#6b7280"
            )
            .encode(x=alt.X("x:Q"), text="label:N")
        )
    if actual is not None:
        layers.append(
            alt.Chart(pd.DataFrame({"x": [actual * 100]}))
            .mark_rule(color="#d62728", size=3)
            .encode(x=alt.X("x:Q"))
        )
    return alt.layer(*layers).properties(height=170)


def render_arm_tabs(row: Mapping[str, Any], *, actual: float | None = None) -> None:
    """One tab per family, on one shared axis.

    Tabs rather than a fold: these are mutually exclusive views of the same
    ticker-session, which is the shape a tab strip is for, and the operator
    asked for the selection to work that way.
    """

    curves = arm_curves(row)
    if not curves:
        st.info(
            "この銘柄の手法別分布は保存されていません。"
            "ロジスティック回帰は上昇確率だけを返すため、分布を持ちません。"
        )
        return

    low, high = axis_for(curves, actual=actual)
    st.caption(
        "横軸は全手法で共通です。破線が各手法の点予測、灰色の点線が下振れ側の "
        "P50 / P75 / P90"
        + ("、赤い実線が実績リターンです。" if actual is not None else " です。")
    )
    for tab, curve in zip(
        st.tabs([f"{curve.label}" for curve in curves]), curves, strict=True
    ):
        with tab:
            note = SPREAD_NOTES.get(curve.spread_kind, "")
            st.caption(
                f"**{curve.label}** — 幅の作り方: "
                f"{SPREAD_SHORT.get(curve.spread_kind, curve.spread_kind)}。{note}"
            )
            chart = density_chart(curve, low=low, high=high, actual=actual)
            if chart is None:
                st.info("この手法の分布は描画できませんでした。")
                continue
            st.altair_chart(chart, width="stretch")
            st.caption(
                f"5%〜95%幅 {curve.width * 100:.2f}pt"
                + (
                    f" ／ 点予測 {curve.predicted_return * 100:+.2f}%"
                    if curve.predicted_return is not None
                    else ""
                )
            )
