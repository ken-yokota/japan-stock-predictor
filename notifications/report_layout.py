"""The one place any report mail is laid out.

The operator asked repeatedly for mails to be readable, and the answer that
finally worked was structural rather than editorial: coloured tables, a signed
number that carries its own colour, and the decision first. This module holds
that layout so no mail has to reinvent it and none can drift from it.

Every mail this project sends -- the morning prediction, the settled result,
and the progress reports -- is built from these pieces. `docs/EMAIL_FORMAT.md`
records what the operator approved; this is the code that produces it.
"""

from __future__ import annotations

import html
from collections.abc import Sequence

# Approved 2026-08-11. Green is a gain or a success, red is a loss or a
# failure, and a signed number is never printed without one of them: on a
# phone the sign alone does not survive the glance.
UP = "#15803d"
DOWN = "#b91c1c"
INK = "#111827"
MUTED = "#6b7280"
LINE = "#e5e7eb"
HEAD = "#1f2937"
GOOD_BG = "#ecfdf5"
BAND = "#f9fafb"

_BADGES = {
    "now": ("#1d4ed8", "#dbeafe"),
    "done": (UP, GOOD_BG),
    "wait": (MUTED, BAND),
    "warn": ("#b45309", "#fef3c7"),
    "fail": (DOWN, "#fee2e2"),
}

Column = tuple[str, str]


def signed_percent(value: float | None, digits: int = 2) -> str:
    """A percentage that carries its own colour."""

    if value is None:
        return f"<span style='color:{MUTED}'>—</span>"
    tone = UP if value > 0 else DOWN if value < 0 else MUTED
    return f"<span style='color:{tone};font-weight:600'>{value:+.{digits}%}</span>"


def signed_yen(value: float | None) -> str:
    if value is None:
        return f"<span style='color:{MUTED}'>—</span>"
    tone = UP if value > 0 else DOWN if value < 0 else MUTED
    return f"<span style='color:{tone};font-weight:600'>{value:+,.0f}円</span>"


def badge(label: str, tone: str = "wait") -> str:
    """A state chip. Tones: now, done, wait, warn, fail."""

    fore, back = _BADGES.get(tone, _BADGES["wait"])
    return (
        f"<span style='background:{back};color:{fore};border-radius:4px;"
        f"padding:2px 8px;font-size:12px;font-weight:700;white-space:nowrap'>"
        f"{html.escape(label)}</span>"
    )


def cell(
    content: str,
    *,
    align: str = "left",
    muted: bool = False,
    nowrap: bool = True,
) -> str:
    style = (
        f"padding:9px 10px;border-bottom:1px solid {LINE};text-align:{align};"
        f"{'white-space:nowrap;' if nowrap else ''}"
        f"color:{MUTED if muted else INK};font-size:14px"
    )
    return f"<td style='{style}'>{content}</td>"


def row(cells: Sequence[str], background: str = "#fff") -> str:
    return f"<tr style='background:{background}'>{''.join(cells)}</tr>"


def _header_cell(name: str, align: str) -> str:
    return (
        f"<th style='padding:9px 10px;text-align:{align};font-size:12px;"
        f"letter-spacing:.04em;color:#fff;background:{HEAD};"
        f"white-space:nowrap;font-weight:600'>{html.escape(name)}</th>"
    )


def table(
    headers: Sequence[Column], rows: Sequence[str], *, min_width: int = 460
) -> str:
    """A table in its own scroller, so the page body never scrolls sideways.

    Keep to four or five columns: a sixth wraps on a phone and the table stops
    being one.
    """

    head = "".join(_header_cell(name, align) for name, align in headers)
    return (
        "<div style='overflow-x:auto;-webkit-overflow-scrolling:touch'>"
        "<table role='presentation' style='border-collapse:collapse;width:100%;"
        f"min-width:{min_width}px;border:1px solid {LINE};border-radius:8px'>"
        f"<thead><tr>{head}</tr></thead><tbody>{''.join(rows)}</tbody>"
        "</table></div>"
    )


def key_values(pairs: Sequence[tuple[str, str]], *, min_width: int = 440) -> str:
    """Two columns for facts that are not a comparison."""

    return table(
        [("項目", "left"), ("内容", "right")],
        [
            row(
                [
                    cell(key, nowrap=False),
                    cell(value, align="right", nowrap=False),
                ],
                "#fff" if index % 2 == 0 else BAND,
            )
            for index, (key, value) in enumerate(pairs)
        ],
        min_width=min_width,
    )


def section(title: str, body: str = "", note: str = "") -> str:
    """A titled block. ``note`` is the caveat that belongs with the numbers."""

    tail = (
        f"<p style='margin:8px 0 0;color:{MUTED};font-size:12.5px;"
        f"line-height:1.75'>{note}</p>"
        if note
        else ""
    )
    return (
        f"<h2 style='margin:28px 0 10px;font-size:16px;color:{INK};"
        f"border-left:4px solid {HEAD};padding-left:9px'>"
        f"{html.escape(title)}</h2>{body}{tail}"
    )


def page(title: str, lede: str, blocks: Sequence[str], footer: str) -> str:
    """Wrap sections in the shell every mail shares.

    ``lede`` is the one line that has to answer the question before anything is
    opened: how many, how much, what state.
    """

    return (
        "<!doctype html><html><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width,initial-scale=1'>"
        f"</head><body style='margin:0;background:{BAND};padding:16px 0'>"
        "<div style='max-width:680px;margin:0 auto;background:#fff;padding:22px;"
        "border-radius:10px;font-family:-apple-system,BlinkMacSystemFont,"
        '"Hiragino Sans","Yu Gothic",sans-serif;line-height:1.6\'>'
        f"<h1 style='margin:0 0 4px;font-size:19px;color:{INK}'>"
        f"{html.escape(title)}</h1>"
        f"<p style='margin:0 0 4px;color:{MUTED};font-size:13px'>{lede}</p>"
        + "".join(blocks)
        + f"<p style='margin:26px 0 0;color:{MUTED};font-size:11.5px;"
        f"border-top:1px solid {LINE};padding-top:12px'>{footer}</p>"
        "</div></body></html>"
    )


# --- Figures -------------------------------------------------------------
#
# Drawn as nested tables with background colours, not SVG and not images.
# Gmail strips <svg> outright and blocks remote images by default, so a chart
# built either of those ways arrives as a blank space -- which is worse than no
# chart, because the row still claims one. Nested tables with bgcolor are the
# one technique every client the operator reads mail on will render.

FORECAST = "#64748b"  # the model's claim: deliberately not green or red
TRACK = "#e5e7eb"  # the unfilled part of a proportion bar
GRID = "#cbd5e1"  # the zero rule a diverging bar is measured from

BAR_HEIGHT = 10


def _bar(percent: float, colour: str, *, align: str = "left") -> str:
    """One coloured bar occupying ``percent`` of its cell."""

    percent = max(min(percent, 100.0), 0.0)
    if percent <= 0:
        return "&nbsp;"
    return (
        f"<table role='presentation' align='{align}' cellpadding='0' cellspacing='0' "
        f"border='0' style='width:{percent:.4g}%;border-collapse:collapse'>"
        f"<tr><td height='{BAR_HEIGHT}' style='height:{BAR_HEIGHT}px;"
        f"background:{colour};border-radius:2px;font-size:0;line-height:0'>"
        "&nbsp;</td></tr></table>"
    )


def diverging_bar(
    value: float | None,
    scale: float,
    *,
    colour: str | None = None,
) -> str:
    """A bar growing right for a gain and left for a loss, about a centre rule.

    ``scale`` is the largest absolute value in the column, so every row in one
    figure is measured against the same ruler. A figure whose rows use
    different scales is a figure that lies.
    """

    if value is None or scale <= 0:
        share = 0.0
        value = value or 0.0
    else:
        share = min(abs(value) / scale, 1.0) * 100
    tone = colour or (UP if value > 0 else DOWN if value < 0 else MUTED)
    left = _bar(share, tone, align="right") if value < 0 else "&nbsp;"
    right = _bar(share, tone, align="left") if value > 0 else "&nbsp;"
    return (
        "<table role='presentation' cellpadding='0' cellspacing='0' border='0' "
        "style='width:100%;border-collapse:collapse'><tr>"
        f"<td width='50%' style='padding:0;border-right:1px solid {GRID}'>{left}</td>"
        f"<td width='50%' style='padding:0'>{right}</td>"
        "</tr></table>"
    )


def stacked_bars(rows: Sequence[tuple[float | None, str | None]], scale: float) -> str:
    """Several diverging bars sharing one centre rule, stacked in one cell.

    Used to put the forecast directly above what happened, so the gap between
    them is the thing the eye lands on.
    """

    parts = [
        f"<div style='padding:1px 0'>{diverging_bar(value, scale, colour=colour)}</div>"
        for value, colour in rows
    ]
    return "".join(parts)


def ratio_bar(value: float | None, *, reference: float = 0.5) -> str:
    """A 0..1 proportion. Coloured by whether it clears ``reference``.

    A reference line drawn inside the bar does not survive every client, so the
    comparison is carried by the colour instead and the threshold is named in
    the column heading.
    """

    if value is None:
        return f"<span style='color:{MUTED}'>—</span>"
    share = max(min(value, 1.0), 0.0) * 100
    tone = UP if value >= reference else DOWN
    return (
        "<table role='presentation' cellpadding='0' cellspacing='0' border='0' "
        "style='width:100%;border-collapse:collapse'><tr>"
        f"<td width='{share:.4g}%' height='{BAR_HEIGHT}' style='height:{BAR_HEIGHT}px;"
        f"background:{tone};border-radius:2px 0 0 2px;font-size:0;line-height:0'>"
        "&nbsp;</td>"
        f"<td height='{BAR_HEIGHT}' style='height:{BAR_HEIGHT}px;background:{TRACK};"
        "border-radius:0 2px 2px 0;font-size:0;line-height:0'>&nbsp;</td>"
        "</tr></table>"
    )


def legend(items: Sequence[tuple[str, str]]) -> str:
    """Name every colour used in a figure. A colour nobody explains is decoration."""

    parts = [
        f"<span style='white-space:nowrap;margin-right:14px'>"
        f"<span style='display:inline-block;width:10px;height:10px;background:{colour};"
        f"border-radius:2px;vertical-align:middle'></span>"
        f"<span style='color:{MUTED};font-size:12px;vertical-align:middle;"
        f"margin-left:5px'>{html.escape(label)}</span></span>"
        for label, colour in items
    ]
    return f"<p style='margin:0 0 8px;line-height:1.9'>{''.join(parts)}</p>"


# The forecast distribution's two bands. Deliberately one hue in two weights
# rather than two hues: they are the same claim at two confidence levels, and
# a second colour would read as a second kind of thing.
BAND_OUTER = "#c7d2fe"
BAND_INNER = "#4f46e5"
MEDIAN_MARK = "#111827"


def _segment(share: float, colour: str | None) -> str:
    if share <= 0:
        return ""
    background = colour or TRACK
    return (
        f"<td width='{share:.4g}%' height='{BAR_HEIGHT}' "
        f"style='width:{share:.4g}%;height:{BAR_HEIGHT}px;background:{background};"
        "font-size:0;line-height:0;padding:0'>&nbsp;</td>"
    )


def _half(segments: Sequence[tuple[float, float, str]], low: float, high: float) -> str:
    """Render the part of a distribution that falls in one half of the ruler."""

    span = high - low
    if span <= 0:
        return "&nbsp;"
    cells: list[str] = []
    cursor = low
    for start, end, colour in segments:
        left, right = max(start, low), min(end, high)
        if right <= left:
            continue
        if left > cursor:
            cells.append(_segment((left - cursor) / span * 100, None))
        cells.append(_segment((right - left) / span * 100, colour))
        cursor = right
    if cursor < high:
        cells.append(_segment((high - cursor) / span * 100, None))
    return (
        "<table role='presentation' cellpadding='0' cellspacing='0' border='0' "
        "style='width:100%;border-collapse:collapse'><tr>"
        + "".join(cells)
        + "</tr></table>"
    )


def distribution_bar(
    low: float,
    lower_quartile: float,
    median: float,
    upper_quartile: float,
    high: float,
    scale: float,
) -> str:
    """One forecast distribution drawn against a shared, zero-centred ruler.

    The inner block is the 50% band and the outer the 80%; the dark rule is the
    median and the thin grey rule down the centre is zero, so whether the mass
    sits above or below break-even is the first thing the eye resolves.

    ``scale`` is the half-width of the ruler and is the same for every row in
    one table. A figure whose rows use different scales is a figure that lies,
    and for a distribution that would be worse than for a point: it would make
    a wide, uncertain forecast look identical to a tight, confident one.
    """

    if scale <= 0:
        return "&nbsp;"
    marker = scale * 0.015
    base = [
        (low, lower_quartile, BAND_OUTER),
        (lower_quartile, upper_quartile, BAND_INNER),
        (upper_quartile, high, BAND_OUTER),
    ]
    segments: list[tuple[float, float, str]] = []
    for start, end, colour in base:
        if end <= median - marker or start >= median + marker:
            segments.append((start, end, colour))
            continue
        if start < median - marker:
            segments.append((start, median - marker, colour))
        if end > median + marker:
            segments.append((median + marker, end, colour))
    segments.append((median - marker, median + marker, MEDIAN_MARK))
    segments.sort()
    return (
        "<table role='presentation' cellpadding='0' cellspacing='0' border='0' "
        "style='width:100%;min-width:150px;border-collapse:collapse'><tr>"
        f"<td width='50%' style='padding:0;border-right:1px solid {GRID}'>"
        f"{_half(segments, -scale, 0.0)}</td>"
        f"<td width='50%' style='padding:0'>{_half(segments, 0.0, scale)}</td>"
        "</tr></table>"
    )


DENSITY_FILL = "#4f46e5"
DENSITY_HEIGHT = 44

# Eight heights of block, plus a space for a column the fit puts no mass in.
# A space rather than a dot: the empty columns are the 5% tails the window
# cannot place, and drawing a mark there would suggest it had placed them.
_BLOCKS = " ▁▂▃▄▅▆▇█"


def density_row(profile: Sequence[float], peak: float) -> str:
    """One forecast density as block characters, on a caller-shared scale.

    ``peak`` is the tallest column across every row of the same figure, so two
    tickers drawn together can be compared: a flatter row really is a less
    certain forecast, not just a differently scaled one.
    """

    if peak <= 0:
        return " " * len(profile)
    return "".join(
        _BLOCKS[min(round(value / peak * (len(_BLOCKS) - 1)), len(_BLOCKS) - 1)]
        if value > 0
        else _BLOCKS[0]
        for value in profile
    )


def density_axis(scale: float, columns: int) -> tuple[str, str]:
    """A ruler for a density row: the tick line, and the labels under it.

    Three labels only. A denser ruler collides with itself at the widths a
    phone renders, and two overlapping percentages are worse than none.
    """

    centre = columns // 2
    ticks = ["-"] * columns
    ticks[centre] = "|"
    ticks[0] = "|"
    ticks[columns - 1] = "|"
    labels = [" "] * columns
    for position, text, align in (
        (0, f"{-scale:+.1%}", "left"),
        (centre, "0", "centre"),
        (columns - 1, f"{scale:+.1%}", "right"),
    ):
        if align == "left":
            start = 0
        elif align == "right":
            start = columns - len(text)
        else:
            start = position - len(text) // 2
        start = min(max(start, 0), columns - len(text))
        labels[start : start + len(text)] = list(text)
    return "".join(ticks), "".join(labels)


def density_chart(profile: Sequence[float], peak: float) -> str:
    """The same density as an email-safe bar chart, zero ruled down the centre.

    Bars are bottom-aligned cells rather than positioned elements: Gmail strips
    ``position``, and a chart that collapses in the one client the operator
    actually reads is not a chart.
    """

    if not profile:
        return "&nbsp;"
    centre = len(profile) // 2
    width = 100.0 / len(profile)
    cells: list[str] = []
    for index, value in enumerate(profile):
        height = 0 if peak <= 0 else round(value / peak * DENSITY_HEIGHT)
        bar = (
            "&nbsp;"
            if height <= 0
            else (
                "<table role='presentation' cellpadding='0' cellspacing='0' "
                "border='0' style='width:100%;border-collapse:collapse'><tr>"
                f"<td height='{height}' style='height:{height}px;"
                f"background:{DENSITY_FILL};font-size:0;line-height:0'>&nbsp;</td>"
                "</tr></table>"
            )
        )
        rule = f"border-left:1px solid {GRID};" if index == centre else ""
        cells.append(
            f"<td valign='bottom' width='{width:.4g}%' "
            f"style='width:{width:.4g}%;height:{DENSITY_HEIGHT}px;padding:0;{rule}'>"
            f"{bar}</td>"
        )
    return (
        "<table role='presentation' cellpadding='0' cellspacing='0' border='0' "
        f"style='width:100%;min-width:180px;height:{DENSITY_HEIGHT}px;"
        "border-collapse:collapse'><tr>" + "".join(cells) + "</tr></table>"
    )
