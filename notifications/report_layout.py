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
