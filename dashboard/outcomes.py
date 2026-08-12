"""The record beside the forecast: what was predicted, and what happened.

Kept apart from ``presenters`` on purpose. Streamlit Cloud re-reads a page on
every run but keeps already-imported modules in memory, so a page that reaches
for a newly added name in an old module raises ImportError until the process
is restarted - which is exactly how both Today and Stock Detail broke after
these functions were first added. A module the running process has never
imported is loaded from disk, so this boundary is what lets the record ship
without a reboot.

It is also the honest boundary: formatting a number and judging whether a
prediction came true are different jobs.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

from dashboard.catalog import stock_label
from dashboard.presenters import (
    as_number,
    format_number,
    format_percent,
    format_probability,
    format_yen,
    safe_text,
)


def _direction_hit(predicted: object, actual: object) -> str:
    """Did the prediction get the direction right? Blank until it settles."""

    predicted_value = as_number(predicted)
    actual_value = as_number(actual)
    if predicted_value is None or actual_value is None:
        return "—"
    if actual_value == 0.0:
        return "±0"
    return "的中" if (predicted_value > 0) == (actual_value > 0) else "外れ"


def buy_hit_ratio(rows: Iterable[Mapping[str, Any]]) -> tuple[int, int, int]:
    """(days that actually rose, days a BUY was issued, days not settled yet).

    The denominator is every BUY, not only the settled ones, because a signal
    whose day has not closed is still a signal that was issued. Counting only
    settled days would quietly improve the ratio whenever a session is missing
    its close. The unsettled count is returned alongside so the fraction is
    read with the right caveat rather than as a final score.
    """

    issued = settled_positive = unsettled = 0
    for row in rows:
        if safe_text(row.get("signal", "")).upper() != "BUY":
            continue
        issued += 1
        actual = as_number(row.get("actual_intraday_return"))
        if actual is None:
            unsettled += 1
        elif actual > 0:
            settled_positive += 1
    return settled_positive, issued, unsettled


def outcome_table_rows(
    rows: Iterable[Mapping[str, Any]],
    *,
    buy_only: bool = False,
) -> list[dict[str, object]]:
    """One prediction per row, beside what actually happened.

    The same builder serves Today, Stock Detail and History so a prediction
    cannot read differently depending on which page it is opened from. Rows
    whose session has not settled keep their outcome columns empty rather than
    showing a zero, which would read as a flat day rather than an unknown one.
    """

    output: list[dict[str, object]] = []
    for row in rows:
        signal = safe_text(row.get("signal", "—"))
        if buy_only and signal.upper() != "BUY":
            continue
        predicted = row.get("predicted_intraday_return")
        actual = row.get("actual_intraday_return")
        output.append(
            {
                "予測日": safe_text(row.get("prediction_date", "—")),
                "銘柄": stock_label(str(row.get("ticker", ""))),
                "判定": signal,
                "予測リターン": format_percent(predicted),
                "上昇確率": format_probability(row.get("probability_up")),
                "実績リターン": format_percent(actual) if actual is not None else "—",
                "方向": _direction_hit(predicted, actual),
                "実績Open": format_number(row.get("actual_open")),
                "実績Close": format_number(row.get("actual_close")),
                "損益": format_yen(row.get("net_profit_jpy"))
                if row.get("net_profit_jpy") is not None
                else "—",
                "状態": safe_text(row.get("status", "—")),
            }
        )
    return output
