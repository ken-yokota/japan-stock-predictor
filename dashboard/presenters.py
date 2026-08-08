"""Pure formatting and warning derivation for Streamlit pages."""

from __future__ import annotations

import json
import math
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from typing import Any
from zoneinfo import ZoneInfo

from dashboard.catalog import sector_label, stock_label
from trading.post_open import project_predicted_close

JST = ZoneInfo("Asia/Tokyo")
_SENSITIVE = re.compile(
    r"(?i)(?:password|passwd|secret|api[_-]?key|access[_-]?token|"
    r"postgres(?:ql)?://[^\s]+|://[^\s:@]+:[^\s@]+@)"
)


class AlertLevel(StrEnum):
    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"


@dataclass(frozen=True, slots=True)
class Alert:
    level: AlertLevel
    title: str
    detail: str


@dataclass(frozen=True, slots=True)
class OperationalCounts:
    fallback: int = 0
    stale_or_missing: int = 0
    unverified_or_delayed: int = 0


def safe_text(value: object, *, maximum: int = 240) -> str:
    """Return bounded display text without credentials or connection strings."""

    rendered = str(value).strip()
    if not rendered:
        return "—"
    if _SENSITIVE.search(rendered):
        return "詳細は非公開です。監査ログを確認してください。"
    if len(rendered) > maximum:
        return f"{rendered[: maximum - 1]}…"
    return rendered


def string_list(value: object) -> tuple[str, ...]:
    """Normalize JSON/list warning fields while applying secret redaction."""

    parsed: object = value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except (json.JSONDecodeError, TypeError):
            parsed = (value,)
    if not isinstance(parsed, (list, tuple, set)):
        return ()
    values = (safe_text(item) for item in parsed if str(item).strip())
    return tuple(dict.fromkeys(values))


def as_number(value: object) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(Decimal(str(value)))
    except (InvalidOperation, TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def format_percent(value: object, *, digits: int = 2) -> str:
    number = as_number(value)
    return "—" if number is None else f"{number * 100:.{digits}f}%"


def format_probability(value: object) -> str:
    return format_percent(value, digits=1)


def format_percent_range(low: object, high: object) -> str:
    lower = as_number(low)
    upper = as_number(high)
    if lower is None or upper is None:
        return "—"
    return f"[{format_percent(lower)}, {format_percent(upper)}]"


def format_number(value: object, *, digits: int = 2) -> str:
    number = as_number(value)
    return "—" if number is None else f"{number:,.{digits}f}"


def format_yen(value: object) -> str:
    number = as_number(value)
    return "—" if number is None else f"¥{number:,.0f}"


def _as_datetime(value: object) -> datetime | None:
    parsed: datetime
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    else:
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed


def format_jst(value: object, *, include_date: bool = True) -> str:
    if isinstance(value, date) and not isinstance(value, datetime):
        return value.isoformat()
    parsed = _as_datetime(value)
    if parsed is None:
        return "—"
    local = parsed.astimezone(JST)
    pattern = "%Y-%m-%d %H:%M JST" if include_date else "%H:%M JST"
    return local.strftime(pattern)


def latest_by(
    rows: Iterable[Mapping[str, Any]],
    *,
    identity: str,
) -> dict[str, Mapping[str, Any]]:
    """Keep the first row per identity; query methods order newest first."""

    latest: dict[str, Mapping[str, Any]] = {}
    for row in rows:
        key = str(row.get(identity, ""))
        if key and key not in latest:
            latest[key] = row
    return latest


def operational_counts(
    selections: Iterable[Mapping[str, Any]],
) -> OperationalCounts:
    fallback = 0
    stale_or_missing = 0
    unverified_or_delayed = 0
    for row in selections:
        role = str(row.get("selection_role", "")).upper()
        freshness = str(row.get("freshness_status", "")).upper()
        quality = str(row.get("data_quality", "")).upper()
        fallback += role == "FALLBACK"
        stale_or_missing += freshness not in {"", "FRESH"} or role == "NONE"
        unverified_or_delayed += quality in {"FREE_UNVERIFIED", "DELAYED"}
    return OperationalCounts(fallback, stale_or_missing, unverified_or_delayed)


def derive_operational_alerts(
    *,
    run: Mapping[str, Any] | None,
    prediction_set: Mapping[str, Any] | None,
    predictions: Sequence[Mapping[str, Any]],
    selections: Sequence[Mapping[str, Any]],
) -> tuple[Alert, ...]:
    """Expose cutoff, quality, stale, fallback and PENDING states prominently."""

    alerts: list[Alert] = []
    if run is None:
        alerts.append(
            Alert(
                AlertLevel.WARNING,
                "PENDING: pipeline runなし",
                "保存済みの日次runがありません。画面は予測を生成しません。",
            )
        )
    else:
        run_status = str(run.get("status", "UNKNOWN")).upper()
        if run_status != "SUCCESS":
            level = AlertLevel.ERROR if run_status == "FAILED" else AlertLevel.WARNING
            alerts.append(
                Alert(
                    level,
                    f"Pipeline {run_status}",
                    "最新runは完了状態ではありません。BUY表示を根拠にしないでください。",
                )
            )
        if run.get("cutoff_at") is None:
            alerts.append(
                Alert(
                    AlertLevel.WARNING,
                    "Data Cutoff未記録",
                    "08:30 JST時点性を画面から確認できません。",
                )
            )
        failed = string_list(run.get("failed_symbols"))
        if failed:
            alerts.append(
                Alert(
                    AlertLevel.ERROR,
                    "取得失敗銘柄あり",
                    "、".join(failed),
                )
            )

    if prediction_set is None:
        alerts.append(
            Alert(
                AlertLevel.WARNING,
                "PENDING: prediction setなし",
                "公開済み予測はまだありません。",
            )
        )
    else:
        status = str(prediction_set.get("status", "UNKNOWN")).upper()
        if status != "READY":
            level = AlertLevel.ERROR if status == "FAILED" else AlertLevel.WARNING
            alerts.append(
                Alert(
                    level,
                    f"Prediction {status}",
                    "READY以外の予測はBUY候補として扱いません。",
                )
            )
        for warning in string_list(prediction_set.get("warnings")):
            alerts.append(Alert(AlertLevel.WARNING, "Prediction warning", warning))

    bad_predictions = sum(
        str(row.get("status", "")).upper() != "SUCCESS" for row in predictions
    )
    if bad_predictions:
        alerts.append(
            Alert(
                AlertLevel.WARNING,
                "利用不可の個別予測あり",
                f"{bad_predictions}銘柄がSUCCESSではありません。",
            )
        )
    for row in predictions:
        ticker = str(row.get("ticker", ""))
        for warning in string_list(row.get("warnings")):
            alerts.append(Alert(AlertLevel.WARNING, f"{ticker} warning", warning))

    counts = operational_counts(selections)
    if counts.fallback:
        alerts.append(
            Alert(
                AlertLevel.WARNING,
                "Provider fallback使用",
                f"{counts.fallback}系列でPrimary以外を採用しています。",
            )
        )
    if counts.stale_or_missing:
        alerts.append(
            Alert(
                AlertLevel.ERROR,
                "STALE / MISSING",
                f"{counts.stale_or_missing}系列が鮮度条件を満たしていません。",
            )
        )
    if counts.unverified_or_delayed:
        alerts.append(
            Alert(
                AlertLevel.WARNING,
                "品質注意",
                f"{counts.unverified_or_delayed}系列がFREE_UNVERIFIEDまたはDELAYEDです。",
            )
        )

    deduplicated: dict[tuple[AlertLevel, str, str], Alert] = {}
    for alert in alerts:
        deduplicated[(alert.level, alert.title, alert.detail)] = alert
    return tuple(deduplicated.values())


def _post_open_close(prediction: Mapping[str, Any]) -> str:
    """Show the Open-based predicted close only once a real Open exists.

    Before 09:00 there is no Open, so this stays ``PENDING`` rather than reusing
    the morning previous-close projection, which would read as if the day's Open
    were already known.
    """

    projection = project_predicted_close(
        prediction.get("actual_open"),
        prediction.get("predicted_intraday_return"),
    )
    if projection is None:
        return "PENDING"
    return f"{projection.predicted_close:,.1f}"


def today_table_rows(
    predictions: Iterable[Mapping[str, Any]],
    metrics: Iterable[Mapping[str, Any]],
) -> list[dict[str, object]]:
    latest_metrics = latest_by(metrics, identity="ticker")
    output: list[dict[str, object]] = []
    for prediction in predictions:
        ticker = str(prediction.get("ticker", ""))
        metric = latest_metrics.get(ticker, {})
        output.append(
            {
                "順位": prediction.get("rank") or "—",
                "銘柄": stock_label(ticker),
                "業種": sector_label(ticker),
                "状態": safe_text(prediction.get("status", "—")),
                "予測リターン": format_percent(
                    prediction.get("predicted_intraday_return")
                ),
                "予測区間": format_percent_range(
                    prediction.get("prediction_interval_low"),
                    prediction.get("prediction_interval_high"),
                ),
                "上昇確率": format_probability(prediction.get("probability_up")),
                "判定": safe_text(prediction.get("signal", "—")),
                "実績Open": format_number(prediction.get("actual_open")),
                "予測終値(Open基準)": _post_open_close(prediction),
                "Confidence": format_number(
                    prediction.get("confidence_score"), digits=1
                ),
                "Feature Coverage": format_percent(
                    prediction.get("feature_coverage"), digits=1
                ),
                "Positive Factors": "、".join(
                    string_list(prediction.get("positive_factors"))
                )
                or "—",
                "Negative Factors": "、".join(
                    string_list(prediction.get("negative_factors"))
                )
                or "—",
                "取引数": metric.get("trade_count", "—"),
                "勝率": format_probability(metric.get("win_rate")),
                "Profit Factor": format_number(metric.get("profit_factor")),
                "期待損益": format_yen(metric.get("expectancy_jpy")),
                "Readability": format_number(metric.get("readability_score"), digits=1),
                "Sample": safe_text(metric.get("sample_status", "PENDING")),
            }
        )
    return output


def sector_rows(
    predictions: Iterable[Mapping[str, Any]],
    metrics: Iterable[Mapping[str, Any]],
) -> list[dict[str, object]]:
    latest_metrics = latest_by(metrics, identity="ticker")
    grouped: dict[str, list[Mapping[str, Any]]] = {}
    for row in predictions:
        grouped.setdefault(sector_label(str(row.get("ticker", ""))), []).append(row)

    output: list[dict[str, object]] = []
    for sector, rows in sorted(grouped.items()):
        returns = [
            value
            for value in (
                as_number(row.get("predicted_intraday_return")) for row in rows
            )
            if value is not None
        ]
        probabilities = [
            value
            for value in (as_number(row.get("probability_up")) for row in rows)
            if value is not None
        ]
        metric_rows = [
            latest_metrics[ticker]
            for ticker in (str(row.get("ticker", "")) for row in rows)
            if ticker in latest_metrics
        ]
        readabilities = [
            value
            for value in (
                as_number(metric.get("readability_score")) for metric in metric_rows
            )
            if value is not None
        ]
        output.append(
            {
                "業種": sector,
                "銘柄数": len(rows),
                "SUCCESS": sum(
                    str(row.get("status", "")).upper() == "SUCCESS" for row in rows
                ),
                "BUY": sum(str(row.get("signal", "")).upper() == "BUY" for row in rows),
                "平均予測リターン": (sum(returns) / len(returns) if returns else None),
                "平均上昇確率": (
                    sum(probabilities) / len(probabilities) if probabilities else None
                ),
                "平均Readability": (
                    sum(readabilities) / len(readabilities) if readabilities else None
                ),
            }
        )
    return output
