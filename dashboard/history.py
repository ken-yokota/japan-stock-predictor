"""Project persisted predictions into the report shape the pages already render.

The research artifacts and the production record answer the same question over
different data, so this returns the dictionary ``dashboard.report_view``
already knows how to draw rather than inventing a second layout. A reader
comparing a backtest window against the live record should be comparing the
numbers, not squinting at two different tables.

Read-only aggregation of rows the pipeline has already written. Nothing here
fetches, trains, or persists.
"""

from __future__ import annotations

from collections import defaultdict
from decimal import Decimal
from typing import Any


def _number(value: Decimal | float | None) -> float | None:
    return None if value is None else float(value)


def _direction_correct(predicted: float | None, actual: float | None) -> bool | None:
    """Whether the sign was right, or ``None`` while the day is unresolved.

    A prediction whose session has not closed yet has no answer. Counting it as
    wrong would drag every in-flight day's accuracy down; counting it as right
    would flatter it. It is excluded until the close is known.
    """

    if predicted is None or actual is None:
        return None
    return (predicted > 0.0) == (actual > 0.0)


def _totals(rows: list[dict[str, Any]]) -> dict[str, Any]:
    traded = [row for row in rows if row["signal"] == "BUY" and row["net_profit_jpy"]]
    wins = [row for row in traded if row["net_profit_jpy"] > 0.0]
    losses = [row for row in traded if row["net_profit_jpy"] < 0.0]
    gross_win = sum(row["net_profit_jpy"] for row in wins)
    gross_loss = -sum(row["net_profit_jpy"] for row in losses)
    resolved = [row for row in rows if row["direction_correct"] is not None]
    return {
        "predictions": len(rows),
        "buy_signals": len([row for row in rows if row["signal"] == "BUY"]),
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": (len(wins) / len(traded)) if traded else None,
        "gross_win_jpy": gross_win,
        "gross_loss_jpy": gross_loss,
        "money_win_ratio": (gross_win / gross_loss) if gross_loss > 0.0 else None,
        "net_profit_jpy": sum(row["net_profit_jpy"] for row in traded),
        "direction_accuracy": (
            sum(1 for row in resolved if row["direction_correct"]) / len(resolved)
            if resolved
            else None
        ),
    }


def build_history_report(source: list[dict[str, Any]]) -> dict[str, Any]:
    """Return the report shape from already-fetched prediction/outcome rows.

    Takes rows rather than a session so the dashboard keeps a single database
    entry point and this stays pure enough to test without a database.
    """

    rows: list[dict[str, Any]] = []
    thresholds: dict[str, float] = {}
    for record in source:
        predicted_return = _number(record.get("predicted_intraday_return"))
        actual_return = _number(record.get("actual_intraday_return"))
        actual_open = _number(record.get("actual_open"))
        # The rule in force for this prediction is stored on the row itself.
        # Reading today's config instead would describe a rule that may never
        # have been applied to it.
        for column, key in (
            ("return_threshold", "return_threshold"),
            ("probability_threshold", "probability_threshold"),
        ):
            value = record.get(column)
            if value is not None:
                thresholds.setdefault(key, float(value))
        rows.append(
            {
                "date": str(record["prediction_date"]),
                "ticker": str(record["ticker"]),
                "signal": record.get("signal"),
                "status": record.get("status"),
                "predicted_return": predicted_return,
                "probability_up": _number(record.get("probability_up")),
                "reference_close": _number(record.get("reference_price")),
                "morning_predicted_close": _number(record.get("predicted_close")),
                "post_open_predicted_close": (
                    actual_open * (1.0 + predicted_return)
                    if actual_open is not None and predicted_return is not None
                    else None
                ),
                "predicted_price_difference": _number(
                    record.get("predicted_price_difference")
                ),
                "actual_open": actual_open,
                "actual_close": _number(record.get("actual_close")),
                "actual_return": actual_return,
                "actual_price_difference": _number(
                    record.get("actual_price_difference")
                ),
                "direction_correct": _direction_correct(
                    predicted_return, actual_return
                ),
                "shares": int(record.get("shares") or 0),
                "net_profit_jpy": _number(record.get("net_profit_jpy")) or 0.0,
                "positive_factors": list(record.get("positive_factors") or []),
                "negative_factors": list(record.get("negative_factors") or []),
            }
        )

    by_day: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        by_day[row["date"]].append(row)
    daily = []
    for day in sorted(by_day):
        summary = _totals(by_day[day])
        daily.append({"date": day, **summary})

    dates = sorted(by_day)
    return {
        "generated_for": {
            "from": dates[0] if dates else "—",
            "to": dates[-1] if dates else "—",
            "training_window_sessions": "—",
        },
        "rule": thresholds,
        "totals": _totals(rows),
        "daily": daily,
        "predictions": rows,
        "coefficient_changes": [],
        "company_coefficients": [],
        "failures": {},
        "caveats": [
            "本番pipelineが実際に公開した予測と、その後に観測された実績です。",
            "研究用の検証結果とは別物で、こちらが唯一の実績記録です。",
            "件数が少ないうちは、勝率も損益も有効性の証拠になりません。",
        ],
    }
