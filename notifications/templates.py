"""HTML and plain-text morning prediction templates."""

from __future__ import annotations

import hashlib
import html
from collections.abc import Iterable

from notifications.contracts import EmailCandidate, MorningEmailPayload, RenderedEmail


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


def _candidate_html(rank: int, item: EmailCandidate) -> str:
    warnings = ""
    if item.warnings:
        warning_text = html.escape(", ".join(item.warnings))
        warnings = f"<p><strong>注意:</strong> {warning_text}</p>"
    return (
        "<section style='border:1px solid #ddd;padding:12px;margin:10px 0'>"
        f"<h3>{rank}. {html.escape(item.company)} "
        f"({html.escape(item.ticker)}) — {html.escape(item.signal)}</h3>"
        "<ul>"
        f"<li>予測リターン: {_percent(item.predicted_return)}</li>"
        f"<li>上昇確率: {_probability(item.probability_up)}</li>"
        f"<li>Readability: {_number(item.readability_score)}</li>"
        f"<li>Profit Factor: {_number(item.profit_factor, 2)}</li>"
        f"<li>Expectancy: {_yen(item.expectancy_jpy)}</li>"
        f"<li>プラス要因: {_factor_text(item.positive_factors)}</li>"
        f"<li>マイナス要因: {_factor_text(item.negative_factors)}</li>"
        "</ul>"
        f"{warnings}</section>"
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
    subject = f"【日本株AI予測】{date_text} 寄り付き→大引け"
    if selected:
        text_body = "\n\n".join(
            _candidate_text(rank, item) for rank, item in enumerate(selected, 1)
        )
        html_body = "".join(
            _candidate_html(rank, item) for rank, item in enumerate(selected, 1)
        )
    else:
        text_body = "本日は条件を満たすBUY候補なし"
        html_body = "<p><strong>本日は条件を満たすBUY候補なし</strong></p>"
    warning_text = "\n".join(f"- {item}" for item in payload.warnings)
    text = (
        f"予測日: {date_text}\n"
        f"Cutoff: {payload.cutoff_at.isoformat()}\n"
        f"Provider Status: {payload.provider_status}\n"
        f"Model: {payload.model_version}\n\n"
        f"{text_body}\n\n"
        f"警告:\n{warning_text or '- なし'}\n\n"
        f"Dashboard: {payload.dashboard_url}\n\n"
        "本メールは個人用の分析情報であり、投資助言や収益保証ではありません。"
    )
    safe_url = html.escape(payload.dashboard_url, quote=True)
    safe_status = html.escape(payload.provider_status)
    safe_model = html.escape(payload.model_version)
    warnings_html = (
        "".join(f"<li>{html.escape(item)}</li>" for item in payload.warnings)
        or "<li>なし</li>"
    )
    html_text = (
        "<!doctype html><html><body>"
        f"<h2>{html.escape(subject)}</h2>"
        f"<p>Cutoff: {html.escape(payload.cutoff_at.isoformat())}<br>"
        f"Provider Status: {safe_status}<br>Model: {safe_model}</p>"
        f"{html_body}<h3>警告</h3><ul>{warnings_html}</ul>"
        f"<p><a href='{safe_url}'>Dashboardを見る</a></p>"
        "<p><small>本メールは個人用の分析情報であり、投資助言や収益保証ではありません。</small></p>"
        "</body></html>"
    )
    recipient_hash = hashlib.sha256(recipient.strip().lower().encode()).hexdigest()[:16]
    key = f"morning/{date_text}/{recipient_hash}"
    return RenderedEmail(subject, text, html_text, sender, recipient, key)
