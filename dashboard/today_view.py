"""Today's record: the eleven-column table, the BUY cards, and the densities.

A new module rather than an addition to ``presenters`` or ``outcomes`` for the
same deployment reason those two are separate. Streamlit Cloud re-reads a
changed page on every rerun but keeps already-imported modules in memory, so a
page reaching for a newly added name in an old module raises ImportError until
someone reboots the container by hand. A module the running process has never
imported is loaded from disk, so everything new the page needs lives here.

The split is also honest: deciding what a row's pipeline state *is* -- did the
data arrive, did the model run, did the mail go, is it published -- is a
judgement, not a formatting choice.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

import altair as alt
import pandas as pd

from dashboard.catalog import stock_label
from dashboard.presenters import (
    _cumulative,
    as_number,
    distribution_levels,
    format_number,
    format_percent,
    format_probability,
    safe_text,
)

# The eleven columns, in the order the operator asked for them. Kept as one
# tuple so the builder, the column config and the tests cannot drift apart.
RESULT_COLUMNS: tuple[str, ...] = (
    "予測日",
    "銘柄名",
    "判定",
    "方向",
    "予測R",
    "上昇確率",
    "実績R",
    "寄付値",
    "予測終値",
    "実績終値",
    "状態",
)

# Frozen while the table scrolls sideways: which day and which stock. Every
# other cell is unreadable without those two, and nothing else is -- 判定 was
# pinned too at first and taken back out, because a pinned column costs its
# width on every screen whether or not the reader needs it there.
PINNED_COLUMNS: frozenset[str] = frozenset({"予測日", "銘柄名"})

# One width for all nine data columns. Equal spacing is the point -- a table
# whose columns size themselves to their contents puts the eye somewhere
# different on each row -- and every one of them holds something short: a
# percentage, a price, a verdict.
COLUMN_WIDTH_PX = 68

# The two identifying columns are the exception, and the exception is the
# reason they are pinned. At 68px "8306 三菱UFJフィナンシャル・グループ" shows
# about four characters, which makes a frozen column that cannot identify its
# own row -- the opposite of what pinning it is for. Widened to what the
# longest name and a full date actually need, and no further. The table is
# still narrower overall than when all eleven were 84px.
IDENTITY_WIDTH_PX: dict[str, int] = {"予測日": 92, "銘柄名": 156}

# The operator's Pxx convention, repeated rather than imported: dashboard/ is
# barred from importing the notification layer, and that ban is worth more than
# this duplication. **Pxx is a downside level -- the return exceeded xx% of the
# time** -- so P90 is the bad case, not the good one, and P90 sits *below* P50
# on the axis. ``test_the_risk_levels_match_the_notification_layer`` pins this
# against notifications.risk_levels so the two cannot drift.
RISK_QUANTILES: tuple[tuple[str, float], ...] = (
    ("P50", 0.50),
    ("P75", 0.25),
    ("P90", 0.10),
)

_SETTLED_STATUSES = frozenset({"FINAL", "CORRECTED"})


def direction_hit(predicted: object, actual: object) -> str:
    """的中 / 外れ, or ``—`` while the session has not settled.

    A flat close is neither: calling a zero-return day a hit or a miss would
    invent a verdict the data does not contain.
    """

    predicted_value = as_number(predicted)
    actual_value = as_number(actual)
    if predicted_value is None or actual_value is None:
        return "—"
    if actual_value == 0.0:
        return "±0"
    return "的中" if (predicted_value > 0) == (actual_value > 0) else "外れ"


def is_settled(row: Mapping[str, Any]) -> bool:
    """Has this row's session produced a real close yet?"""

    return as_number(row.get("actual_intraday_return")) is not None


def day_is_settled(rows: Iterable[Mapping[str, Any]]) -> bool:
    """True once any row has settled, which is what flips the page's mode.

    Settlement is a whole-session event -- the close runs for every ticker at
    once -- so one settled row means the after-close view is the right one.
    """

    return any(is_settled(row) for row in rows)


def _verdict(row: Mapping[str, Any]) -> str:
    """BUY / NO BUY, never blank.

    The operator asked for the判定 column to answer one question, so a row that
    the model could not score reads NO BUY rather than an empty cell: nothing
    was recommended, which is the answer.
    """

    return "BUY" if safe_text(row.get("signal", "")).upper() == "BUY" else "NO BUY"


def pipeline_state(
    row: Mapping[str, Any],
    *,
    email_sent: bool | None,
    degraded_tickers: frozenset[str] = frozenset(),
) -> str:
    """Where this row got to along data → 予測 → メール → 公開.

    Reported as the *first* thing that did not happen rather than a chain of
    ticks, because a column narrow enough to sit beside ten others cannot carry
    four states and still be read at a glance. The full chain is in the column
    help text.

    ``email_sent`` is ``None`` when the delivery record could not be read; that
    is reported as 不明 rather than as a failure, since an unread log is not
    evidence the mail did not go.
    """

    ticker = safe_text(row.get("ticker", ""))
    status = safe_text(row.get("status", "")).upper()
    if status != "SUCCESS":
        return f"予測不可（{status or '不明'}）"
    # Completeness here means the *required* indicators, which is what
    # ``missing_required`` records. Feature Coverage is deliberately not used:
    # it is the share of generated features that had a value, so an indicator
    # that never arrived is absent from its denominator entirely. Judging data
    # completeness by it marked all 22 tickers "欠損" on a morning the
    # completeness panel on the same page called NORMAL.
    if ticker in degraded_tickers:
        return "データ欠損"
    if safe_text(row.get("prediction_set_status", "")).upper() != "READY":
        return "未公開"
    if row.get("published_at") is None:
        return "未公開"
    if email_sent is None:
        return "公開済／メール不明"
    if not email_sent:
        return "メール未送信"
    return "完了"


def result_rows(
    rows: Iterable[Mapping[str, Any]],
    *,
    email_sent: bool | None = None,
    degraded_tickers: frozenset[str] = frozenset(),
) -> list[dict[str, object]]:
    """One row per ticker, in the eleven columns, ordered by rank then ticker.

    Unsettled rows keep their outcome cells at ``—``. Writing a zero there
    would read as a flat day rather than a day that has not happened yet, and
    this table is read before the open as well as after the close.
    """

    output: list[dict[str, object]] = []
    for row in rows:
        predicted = row.get("predicted_intraday_return")
        actual = row.get("actual_intraday_return")
        settled = as_number(actual) is not None
        output.append(
            {
                "予測日": safe_text(row.get("prediction_date", "—")),
                "銘柄名": stock_label(safe_text(row.get("ticker", ""))),
                "判定": _verdict(row),
                "方向": direction_hit(predicted, actual),
                "予測R": format_percent(predicted),
                "上昇確率": format_probability(row.get("probability_up")),
                "実績R": format_percent(actual) if settled else "—",
                "寄付値": format_number(row.get("actual_open")),
                "予測終値": format_number(row.get("predicted_close")),
                "実績終値": format_number(row.get("actual_close")),
                "状態": pipeline_state(
                    row,
                    email_sent=email_sent,
                    degraded_tickers=degraded_tickers,
                ),
            }
        )
    return output


def result_column_config() -> dict[str, Any]:
    """Equal widths for every column, with the first three pinned.

    Built here rather than at the call site so the widths and the pinning stay
    with the column list they describe.
    """

    from streamlit import column_config

    help_text = {
        "状態": (
            "データ取得 → 予測 → メール送信 → 公開 の順に見て、"
            "最初に到達できていない段階を表示します。すべて到達していれば「完了」です。"
        ),
        "方向": "予測の符号と実績の符号が一致したか。実績が出るまでは「—」です。",
        "予測R": "寄り付きから大引けまでの予測リターンです。",
        "実績R": "寄り付きから大引けまでの実績リターンです。",
    }
    return {
        name: column_config.TextColumn(
            name,
            width=IDENTITY_WIDTH_PX.get(name, COLUMN_WIDTH_PX),
            pinned=name in PINNED_COLUMNS,
            help=help_text.get(name),
        )
        for name in RESULT_COLUMNS
    }


@dataclass(frozen=True, slots=True)
class BuyCard:
    """One BUY candidate, with its outcome once the session settles."""

    ticker: str
    label: str
    predicted_return: str
    probability_up: str
    rank: str
    settled: bool
    actual_return: str
    direction: str


def buy_cards(rows: Iterable[Mapping[str, Any]]) -> list[BuyCard]:
    """The published BUY candidates, ranked.

    Only rows from a READY set are included: a candidate from a set that never
    reached READY was never actually recommended, and showing it beside the
    real ones would misreport what the system said that morning.
    """

    candidates = [
        row
        for row in rows
        if safe_text(row.get("status", "")).upper() == "SUCCESS"
        and safe_text(row.get("signal", "")).upper() == "BUY"
        and safe_text(row.get("prediction_set_status", "")).upper() == "READY"
    ]
    output: list[BuyCard] = []
    for row in candidates:
        actual = row.get("actual_intraday_return")
        settled = as_number(actual) is not None
        output.append(
            BuyCard(
                ticker=safe_text(row.get("ticker", "")),
                label=stock_label(safe_text(row.get("ticker", ""))),
                predicted_return=format_percent(row.get("predicted_intraday_return")),
                probability_up=format_probability(row.get("probability_up")),
                rank=str(row.get("rank") or "—"),
                settled=settled,
                actual_return=format_percent(actual) if settled else "—",
                direction=direction_hit(row.get("predicted_intraday_return"), actual),
            )
        )
    return output


def distribution_of(row: Mapping[str, Any]) -> list[tuple[float, float]] | None:
    """The persisted curve as ascending ``(level, return)`` points, or ``None``.

    Parsed with the dashboard's own reader rather than the training package's.
    ``dashboard/`` is forbidden from importing ``models`` -- a read-only screen
    must not be able to reach code that fits anything -- and a guard test
    enforces it. Fewer than three points cannot describe a shape, so those are
    treated as absent, matching what the sparkline column does.

    Never raises. A malformed document means this ticker gets no density plot,
    not that the page fails.
    """

    levels = distribution_levels(row.get("return_distribution"))
    if len(levels) < 3:
        return None
    return sorted(levels.items())


def _quantile_at(curve: list[tuple[float, float]], level: float) -> float | None:
    """The return at one quantile level, interpolated between fitted points.

    Pinned at the outermost fitted level rather than extrapolated. A curve
    fitted from P5 to P95 cannot support a claim past its own ends, and drawing
    one would put a confident-looking rule where the model has said nothing.
    """

    if not curve:
        return None
    for candidate, value in curve:
        if abs(candidate - level) < 1e-9:
            return value
    if level <= curve[0][0]:
        return curve[0][1]
    if level >= curve[-1][0]:
        return curve[-1][1]
    for index in range(len(curve) - 1):
        (low_level, low_value), (high_level, high_value) = (
            curve[index],
            curve[index + 1],
        )
        if low_level <= level <= high_level:
            if high_level == low_level:
                return low_value
            weight = (level - low_level) / (high_level - low_level)
            return low_value + weight * (high_value - low_value)
    return None


def shared_axis(
    rows: Sequence[Mapping[str, Any]], *, pad: float = 0.15
) -> tuple[float, float]:
    """One return range wide enough for every curve, and for every outcome.

    Each ticker drawn on its own axis cannot be compared with the next one by
    eye, which is the main thing anyone wants from a wall of these. The settled
    return is included in the span so the outcome line never falls off the edge
    of the chart that is supposed to be showing it.
    """

    points: list[float] = []
    for row in rows:
        curve = distribution_of(row)
        if curve is not None:
            points.extend((curve[0][1], curve[-1][1]))
        actual = as_number(row.get("actual_intraday_return"))
        if actual is not None:
            points.append(actual)
    if not points:
        return (-0.05, 0.05)
    low, high = min(points), max(points)
    if high <= low:
        low, high = low - 0.01, high + 0.01
    margin = (high - low) * pad
    return (low - margin, high + margin)


def density_frame(
    row: Mapping[str, Any],
    *,
    low: float,
    high: float,
    columns: int = 160,
) -> pd.DataFrame | None:
    """Probability mass per equal-width column, as a plottable frame.

    ``columns`` is deliberately high: the operator asked for a fine horizontal
    axis, and the underlying curve is resampled rather than smoothed, so a
    denser axis shows the shape the model actually produced instead of a
    prettier one.
    """

    curve = distribution_of(row)
    if curve is None or columns < 1 or high <= low:
        return None
    step = (high - low) / columns
    edges = [low + step * index for index in range(columns + 1)]
    # Mass per equal-width column, read off the stored curve. Equal-width
    # rather than the curve's own equal-mass bins: unequal widths make two
    # tickers impossible to compare by eye, which is the whole point of drawing
    # them on one axis.
    profile = [
        max(
            _cumulative(curve, edges[index + 1]) - _cumulative(curve, edges[index]), 0.0
        )
        for index in range(columns)
    ]
    centres = [low + step * (index + 0.5) for index in range(columns)]
    return pd.DataFrame(
        {
            "リターン (%)": [centre * 100 for centre in centres],
            "確率": profile,
        }
    )


def density_chart(
    row: Mapping[str, Any],
    *,
    low: float,
    high: float,
    columns: int = 160,
) -> alt.LayerChart | alt.FacetChart | None:
    """The forecast density, with the realised return drawn on top once known.

    Before the close this is the prediction alone. After it, the outcome is a
    rule across the same axis, so "the model said this, the day did that" is
    one glance rather than two numbers to hold in your head.
    """

    frame = density_frame(row, low=low, high=high, columns=columns)
    curve = distribution_of(row)
    if frame is None or curve is None:
        return None

    area = (
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
    )
    layers: list[Any] = [area]

    predicted = as_number(row.get("predicted_intraday_return"))
    if predicted is not None:
        layers.append(
            alt.Chart(pd.DataFrame({"x": [predicted * 100]}))
            .mark_rule(strokeDash=[4, 3], size=2)
            .encode(x=alt.X("x:Q"))
        )

    # The downside levels, drawn where the risk actually is. Under the
    # operator's convention P90 is the return exceeded 90% of the time, so
    # these march leftwards: P50, then P75, then P90 furthest out. Labelled on
    # the chart because an unlabelled rule at the bad end reads as a target.
    risk_points = [
        {"x": value * 100, "label": label}
        for label, level in RISK_QUANTILES
        if (value := _quantile_at(curve, level)) is not None
    ]
    if risk_points:
        risk_frame = pd.DataFrame(risk_points)
        layers.append(
            alt.Chart(risk_frame)
            .mark_rule(color="#6b7280", strokeDash=[2, 2], size=1)
            .encode(
                x=alt.X("x:Q"), tooltip=["label:N", alt.Tooltip("x:Q", format="+.2f")]
            )
        )
        layers.append(
            alt.Chart(risk_frame)
            .mark_text(
                align="left", baseline="top", dx=3, dy=2, fontSize=10, color="#6b7280"
            )
            .encode(x=alt.X("x:Q"), text="label:N")
        )

    actual = as_number(row.get("actual_intraday_return"))
    if actual is not None:
        layers.append(
            alt.Chart(pd.DataFrame({"x": [actual * 100]}))
            .mark_rule(color="#d62728", size=3)
            .encode(x=alt.X("x:Q"))
        )

    return alt.layer(*layers).properties(height=140)


def settled_status(row: Mapping[str, Any]) -> str:
    """Whether this row's outcome is final, for callers that need the word."""

    status = safe_text(row.get("outcome_status", "")).upper()
    return status if status in _SETTLED_STATUSES else ""


def morning_email_sent(service: object, prediction_date: str) -> bool | None:
    """Did the morning mail for this date go out? ``None`` when unknowable.

    Reached through ``getattr`` on purpose. ``email_deliveries`` is a new method
    on a class the running Streamlit Cloud process may have imported before it
    existed, and an AttributeError there would take down a page over a status
    column. An unread log is reported as 不明, never as a failure to send.
    """

    reader = getattr(service, "email_deliveries", None)
    if reader is None:
        return None
    try:
        result = reader()
    except Exception:
        return None
    if not getattr(result, "ready", False):
        return None
    for row in result.rows:
        if safe_text(row.get("prediction_date", "")) != prediction_date:
            continue
        if safe_text(row.get("status", "")).upper() == "SENT":
            return True
    return False
