"""HTML and plain-text morning prediction templates."""

from __future__ import annotations

import hashlib
import html
import unicodedata
from collections.abc import Iterable, Sequence

from notifications.contracts import EmailCandidate, MorningEmailPayload, RenderedEmail
from notifications.report_layout import (
    BAND,
    GOOD_BG,
    INK,
    MUTED,
    cell,
    page,
    section,
    signed_percent,
    table,
)


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


def _buytable(items: Sequence[EmailCandidate]) -> str:
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


def _alltable(items: Sequence[EmailCandidate]) -> str:
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


def _price_html(value: float | None, *, bold: bool = False) -> str:
    if value is None:
        return f"<span style='color:{MUTED}'>—</span>"
    return f"<strong>{value:,.1f}</strong>" if bold else f"{value:,.1f}"


def _chip(signal: str) -> str:
    """BUY is the only signal worth a colour; everything else is quiet."""

    if signal == "BUY":
        return (
            "<span style='background:#15803d;color:#fff;border-radius:4px;"
            "padding:2px 8px;font-size:12px;font-weight:700'>買い</span>"
        )
    return f"<span style='color:{MUTED};font-size:12px'>見送り</span>"


def _name_html(item: EmailCandidate) -> str:
    return (
        f"<strong>{html.escape(item.ticker)}</strong> "
        f"<span style='color:{MUTED}'>{html.escape(item.company)}</span>"
    )


def _buy_rows_html(items: Sequence[EmailCandidate]) -> str:
    rows = [
        f"<tr style='background:{GOOD_BG}'>"
        + cell(f"<strong>{item.rank or index}</strong>", align="center")
        + cell(_name_html(item))
        + cell(signed_percent(item.predicted_return), align="right")
        + cell(_probability(item.probability_up), align="right")
        + cell(_price_html(item.reference_price), align="right", muted=True)
        + cell(_price_html(item.predicted_close, bold=True), align="right")
        + "</tr>"
        for index, item in enumerate(items, 1)
    ]
    return table(
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
        + cell(_name_html(item))
        + cell(_chip(item.signal), align="center")
        + cell(signed_percent(item.predicted_return), align="right")
        + cell(_probability(item.probability_up), align="right", muted=True)
        + cell(_price_html(item.reference_price), align="right", muted=True)
        + "</tr>"
        for index, item in enumerate(items)
    ]
    return table(
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
            f"<tr>{cell('押し上げた要因', muted=True)}"
            f"{cell(_factor_text(item.positive_factors), nowrap=False)}</tr>",
            f"<tr>{cell('押し下げた要因', muted=True)}"
            f"{cell(_factor_text(item.negative_factors), nowrap=False)}</tr>",
        ]
        if item.warnings:
            rows.append(
                f"<tr>{cell('注意', muted=True)}"
                f"{cell(_factor_text(item.warnings), nowrap=False)}</tr>"
            )
        blocks.append(
            f"<p style='margin:16px 0 6px;font-size:14px;color:{INK}'>"
            f"{_name_html(item)} <span style='color:{MUTED}'>予測 "
            f"{_percent(item.predicted_return)}</span></p>"
            + table([("区分", "left"), ("要因", "left")], rows)
        )
    return "".join(blocks)


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
                _buytable(selected),
                "",
                "",
                "■ なぜその予測になったか",
                "",
                _reasons_text(selected),
            ]
        )
        html_body = section("買い候補", _buy_rows_html(selected)) + section(
            "なぜその予測になったか",
            _reasons_html(selected),
            "モデルが押し上げた要因と押し下げた要因です。",
        )
    else:
        text_body = "本日は条件を満たすBUY候補なし"
        html_body = section(
            "買い候補",
            "<p style='margin:0;padding:14px;background:#f9fafb;border-radius:8px'>"
            "<strong>本日は条件を満たすBUY候補はありません。</strong></p>",
        )
    if everything:
        text_body += "\n\n\n■ 全銘柄\n\n" + _alltable(everything)
        html_body += section(
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
    html_text = page(
        f"日本株AI予測　{date_text}",
        f"寄り付きに買い、大引けに売る前提　|　買い候補 {len(selected)}銘柄 / "
        f"全{len(everything)}銘柄　|　取得状態 "
        f"{html.escape(payload.provider_status)}　|　モデル "
        f"{html.escape(payload.model_version)}",
        [
            html_body,
            section(
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
