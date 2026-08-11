"""HTML and plain-text morning prediction templates."""

from __future__ import annotations

import hashlib
import html
import unicodedata
from collections.abc import Iterable, Sequence

from notifications.contracts import EmailCandidate, MorningEmailPayload, RenderedEmail

# The palette the operator approved on 2026-08-11. See docs/EMAIL_FORMAT.md;
# a signed number without a colour is unreadable on a phone.
UP = "#15803d"
DOWN = "#b91c1c"
INK = "#111827"
MUTED = "#6b7280"
LINE = "#e5e7eb"
HEAD = "#1f2937"
BUY_BG = "#ecfdf5"
BAND = "#f9fafb"


def _percent(value: float | None) -> str:
    return "—" if value is None else f"{value:+.2%}"


def _probability(value: float | None) -> str:
    return "—" if value is None else f"{value:.0%}"


def _number(value: float | None, digits: int = 1) -> str:
    return "—" if value is None else f"{value:.{digits}f}"


def _yen(value: float | None) -> str:
    return "—" if value is None else f"¥{value:+,.0f}"


def _factor_text(values: Iterable[str]) -> str:
    escaped = [html.escape(value) for value in values]
    return ", ".join(escaped) if escaped else "—"


def _display_width(value: str) -> int:
    return sum(2 if unicodedata.east_asian_width(ch) in "WF" else 1 for ch in value)


def _pad(value: str, width: int) -> str:
    """Pad to a *display* width, so a column of Japanese names still lines up."""

    return value + " " * max(width - _display_width(value), 0)


def _name(item: EmailCandidate) -> str:
    return _pad(f"{item.ticker} {item.company}", 18)


def _price(value: float | None) -> str:
    return "—".rjust(9) if value is None else f"{value:>9,.1f}"


def _buy_table(items: Sequence[EmailCandidate]) -> str:
    """The decision table: what to buy, at what price, with what confidence."""

    header = (
        "  順位  銘柄              予測      確率    前日終値   予測終値\n"
        "  ----  ----------------  --------  ------  ---------  ---------"
    )
    rows = [
        f"  {(item.rank or index):>4}  {_name(item)}"
        f"{_percent(item.predicted_return):>8}  {_probability(item.probability_up):>6}"
        f"  {_price(item.reference_price)}  {_price(item.predicted_close)}"
        for index, item in enumerate(items, 1)
    ]
    return "\n".join([header, *rows])


def _all_table(items: Sequence[EmailCandidate]) -> str:
    """Every ticker, so a missing name is visible rather than merely absent."""

    header = (
        "  銘柄              判定    予測      確率    前日終値\n"
        "  ----------------  ------  --------  ------  ---------"
    )
    rows = [
        f"  {_name(item)}{item.signal:<8}{_percent(item.predicted_return):>8}"
        f"  {_probability(item.probability_up):>6}  {_price(item.reference_price)}"
        for item in items
    ]
    return "\n".join([header, *rows])


def _reasons_text(items: Sequence[EmailCandidate]) -> str:
    """Why each buy was chosen, in the model's own terms."""

    blocks: list[str] = []
    for item in items:
        lines = [
            f"  {item.ticker} {item.company}（予測 {_percent(item.predicted_return)}）"
        ]
        lines.append(f"    押し上げた要因: {', '.join(item.positive_factors) or '—'}")
        lines.append(f"    押し下げた要因: {', '.join(item.negative_factors) or '—'}")
        if item.warnings:
            lines.append(f"    注意: {', '.join(item.warnings)}")
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks)


def _candidate_text(rank: int, item: EmailCandidate) -> str:
    warnings = f"\n  注意: {', '.join(item.warnings)}" if item.warnings else ""
    return (
        f"{rank}. {item.company} ({item.ticker}) [{item.signal}]\n"
        f"  予測リターン: {_percent(item.predicted_return)}\n"
        f"  上昇確率: {_probability(item.probability_up)}\n"
        f"  Readability: {_number(item.readability_score)}\n"
        f"  Profit Factor: {_number(item.profit_factor, 2)}\n"
        f"  Expectancy: {_yen(item.expectancy_jpy)}\n"
        f"  プラス要因: {', '.join(item.positive_factors) or '—'}\n"
        f"  マイナス要因: {', '.join(item.negative_factors) or '—'}"
        f"{warnings}"
    )


def _signed(value: float | None, digits: int = 2) -> str:
    """A percentage that carries its own colour, green up and red down."""

    if value is None:
        return f"<span style='color:{MUTED}'>—</span>"
    tone = UP if value > 0 else DOWN if value < 0 else MUTED
    return f"<span style='color:{tone};font-weight:600'>{value:+.{digits}%}</span>"


def _money(value: float | None) -> str:
    if value is None:
        return f"<span style='color:{MUTED}'>—</span>"
    tone = UP if value > 0 else DOWN if value < 0 else MUTED
    return f"<span style='color:{tone};font-weight:600'>{value:+,.0f}円</span>"


def _price_html(value: float | None, *, bold: bool = False) -> str:
    if value is None:
        return f"<span style='color:{MUTED}'>—</span>"
    return f"<strong>{value:,.1f}</strong>" if bold else f"{value:,.1f}"


def _chip(signal: str) -> str:
    if signal == "BUY":
        return (
            "<span style='background:#15803d;color:#fff;border-radius:4px;"
            "padding:2px 8px;font-size:12px;font-weight:700'>買い</span>"
        )
    return f"<span style='color:{MUTED};font-size:12px'>見送り</span>"


def _td(
    content: str, *, align: str = "left", muted: bool = False, nowrap: bool = True
) -> str:
    style = (
        f"padding:9px 10px;border-bottom:1px solid {LINE};"
        f"text-align:{align};{'white-space:nowrap;' if nowrap else ''}"
        f"color:{MUTED if muted else INK};font-size:14px"
    )
    return f"<td style='{style}'>{content}</td>"


def _th(name: str, align: str = "left") -> str:
    return (
        f"<th style='padding:9px 10px;text-align:{align};font-size:12px;"
        f"letter-spacing:.04em;color:#fff;background:{HEAD};"
        f"white-space:nowrap;font-weight:600'>{html.escape(name)}</th>"
    )


def _table(headers: Sequence[tuple[str, str]], rows: Sequence[str]) -> str:
    """Wrapped in its own scroller so the page body never scrolls sideways."""

    head = "".join(_th(name, align) for name, align in headers)
    return (
        "<div style='overflow-x:auto;-webkit-overflow-scrolling:touch'>"
        "<table role='presentation' style='border-collapse:collapse;width:100%;"
        f"min-width:520px;border:1px solid {LINE};border-radius:8px'>"
        f"<thead><tr>{head}</tr></thead><tbody>{''.join(rows)}</tbody>"
        "</table></div>"
    )


def _section(title: str, body: str = "", note: str = "") -> str:
    tail = (
        (
            f"<p style='margin:8px 0 0;color:{MUTED};font-size:12.5px;"
            f"line-height:1.75'>{note}</p>"
        )
        if note
        else ""
    )
    return (
        f"<h2 style='margin:28px 0 10px;font-size:16px;color:{INK};"
        f"border-left:4px solid {HEAD};padding-left:9px'>"
        f"{html.escape(title)}</h2>{body}{tail}"
    )


def _name_html(item: EmailCandidate) -> str:
    return (
        f"<strong>{html.escape(item.ticker)}</strong> "
        f"<span style='color:{MUTED}'>{html.escape(item.company)}</span>"
    )


def _buy_rows_html(items: Sequence[EmailCandidate]) -> str:
    rows = [
        f"<tr style='background:{BUY_BG}'>"
        + _td(f"<strong>{item.rank or index}</strong>", align="center")
        + _td(_name_html(item))
        + _td(_signed(item.predicted_return), align="right")
        + _td(_probability(item.probability_up), align="right")
        + _td(_price_html(item.reference_price), align="right", muted=True)
        + _td(_price_html(item.predicted_close, bold=True), align="right")
        + "</tr>"
        for index, item in enumerate(items, 1)
    ]
    return _table(
        [
            ("順位", "center"),
            ("銘柄", "left"),
            ("予測リターン", "right"),
            ("上昇確率", "right"),
            ("前日終値", "right"),
            ("予測終値", "right"),
        ],
        rows,
    )


def _all_rows_html(items: Sequence[EmailCandidate]) -> str:
    rows = [
        f"<tr style='background:{'#fff' if index % 2 == 0 else BAND}'>"
        + _td(_name_html(item))
        + _td(_chip(item.signal), align="center")
        + _td(_signed(item.predicted_return), align="right")
        + _td(_probability(item.probability_up), align="right", muted=True)
        + _td(_price_html(item.reference_price), align="right", muted=True)
        + "</tr>"
        for index, item in enumerate(items)
    ]
    return _table(
        [
            ("銘柄", "left"),
            ("判定", "center"),
            ("予測リターン", "right"),
            ("上昇確率", "right"),
            ("前日終値", "right"),
        ],
        rows,
    )


def _reasons_html(items: Sequence[EmailCandidate]) -> str:
    blocks: list[str] = []
    for item in items:
        rows = [
            f"<tr>{_td('押し上げた要因', muted=True)}"
            f"{_td(_factor_text(item.positive_factors), nowrap=False)}</tr>",
            f"<tr>{_td('押し下げた要因', muted=True)}"
            f"{_td(_factor_text(item.negative_factors), nowrap=False)}</tr>",
        ]
        if item.warnings:
            rows.append(
                f"<tr>{_td('注意', muted=True)}"
                f"{_td(_factor_text(item.warnings), nowrap=False)}</tr>"
            )
        blocks.append(
            f"<p style='margin:16px 0 6px;font-size:14px;color:{INK}'>"
            f"{_name_html(item)} <span style='color:{MUTED}'>予測 "
            f"{_percent(item.predicted_return)}</span></p>"
            + _table([("区分", "left"), ("要因", "left")], rows)
        )
    return "".join(blocks)


def _page(title: str, lede: str, blocks: Sequence[str], footer: str) -> str:
    return (
        "<!doctype html><html><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width,initial-scale=1'>"
        f"</head><body style='margin:0;background:{BAND};padding:16px 0'>"
        "<div style='max-width:680px;margin:0 auto;background:#fff;padding:22px;"
        "border-radius:10px;font-family:-apple-system,BlinkMacSystemFont,"
        '"Hiragino Sans","Yu Gothic",sans-serif;line-height:1.6\'>'
        f"<h1 style='margin:0 0 4px;font-size:19px;color:{INK}'>{title}</h1>"
        f"<p style='margin:0 0 4px;color:{MUTED};font-size:13px'>{lede}</p>"
        + "".join(blocks)
        + f"<p style='margin:26px 0 0;color:{MUTED};font-size:11.5px;"
        f"border-top:1px solid {LINE};padding-top:12px'>{footer}</p>"
        "</div></body></html>"
    )


def render_morning_email(
    payload: MorningEmailPayload,
    *,
    sender: str,
    recipient: str,
    top_n: int = 5,
) -> RenderedEmail:
    """Render a deterministic message from a persisted prediction set."""

    if top_n <= 0:
        raise ValueError("top_n must be positive")
    selected = tuple(
        item
        for item in payload.candidates
        if item.signal == "BUY" and item.status == "READY"
    )[:top_n]
    date_text = payload.prediction_date.isoformat()
    subject = f"【日本株AI予測】{date_text} 寄り付き→大引け／買い{len(selected)}銘柄"
    everything = tuple(payload.candidates)
    if selected:
        text_body = "\n".join(
            [
                "■ 買い候補",
                "",
                _buy_table(selected),
                "",
                "",
                "■ なぜその予測になったか",
                "",
                _reasons_text(selected),
            ]
        )
        html_body = _section("買い候補", _buy_rows_html(selected)) + _section(
            "なぜその予測になったか",
            _reasons_html(selected),
            "モデルが押し上げた要因と押し下げた要因です。",
        )
    else:
        text_body = "本日は条件を満たすBUY候補なし"
        html_body = _section(
            "買い候補",
            "<p style='margin:0;padding:14px;background:#f9fafb;border-radius:8px'>"
            "<strong>本日は条件を満たすBUY候補はありません。</strong></p>",
        )
    if everything:
        text_body += "\n\n\n■ 全銘柄\n\n" + _all_table(everything)
        html_body += _section(
            "全銘柄",
            _all_rows_html(everything),
            "買わなかった銘柄も載せています。欠けている銘柄は予測自体がありません。",
        )
    warning_text = "\n".join(f"- {item}" for item in payload.warnings)
    text = (
        f"予測日          {date_text}（寄り付きに買い、大引けに売る前提）\n"
        f"買い候補        {len(selected)}銘柄 / 全{len(everything)}銘柄\n"
        f"Cutoff          {payload.cutoff_at.isoformat()}\n"
        f"取得状態        {payload.provider_status}\n"
        f"モデル          {payload.model_version}\n\n\n"
        f"{text_body}\n\n\n"
        f"■ 警告\n\n{warning_text or '- なし'}\n\n"
        f"Dashboard: {payload.dashboard_url}\n\n"
        "本メールは個人用の分析情報であり、投資助言や収益保証ではありません。"
    )
    safe_url = html.escape(payload.dashboard_url, quote=True)
    warnings_html = (
        "".join(
            f"<li style='margin-bottom:4px'>{html.escape(item)}</li>"
            for item in payload.warnings
        )
        or "<li>なし</li>"
    )
    html_text = _page(
        f"日本株AI予測　{date_text}",
        f"寄り付きに買い、大引けに売る前提　|　買い候補 {len(selected)}銘柄 / "
        f"全{len(everything)}銘柄　|　取得状態 "
        f"{html.escape(payload.provider_status)}　|　モデル "
        f"{html.escape(payload.model_version)}",
        [
            html_body,
            _section(
                "警告",
                f"<ul style='margin:0;padding-left:20px;color:{INK};"
                f"font-size:13.5px'>{warnings_html}</ul>",
            ),
            f"<p style='margin:22px 0 0'><a href='{safe_url}' "
            f"style='color:#1d4ed8;font-size:14px'>ダッシュボードを開く</a></p>",
        ],
        "本メールは個人用の分析情報であり、投資助言や収益保証ではありません。"
        "売買判断には使用しないでください。優位性はまだ確認されていません。",
    )
    recipient_hash = hashlib.sha256(recipient.strip().lower().encode()).hexdigest()[:16]
    key = f"morning/{date_text}/{recipient_hash}"
    return RenderedEmail(subject, text, html_text, sender, recipient, key)
