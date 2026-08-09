"""Whether the model is getting better, tracked day by day.

A single accuracy number cannot answer that. It moves every day for reasons
that have nothing to do with the model, and by the time a change ships the
previous number has already been forgotten. So this builds the series instead:
what the model scored on each session, what simply owning the stocks would have
scored on the same session, and which model version produced it.

Two choices make the picture honest rather than flattering.

The comparison is a *paired* one. Accuracy against a fixed 50% would mostly
measure whether the month rose; against the same day's actual up-rate it
measures the model. A quiet month and a violent one become comparable.

The rolling window is reported alongside a cumulative line. Rolling shows the
current state and is noisy; cumulative is stable and lags. Reading only one of
them is how a lucky fortnight becomes "the new model works".
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from typing import Any

# Below this, a day's accuracy is mostly noise from a handful of predictions.
MINIMUM_PREDICTIONS_PER_DAY = 5
DEFAULT_ROLLING_SESSIONS = 20


@dataclass(frozen=True, slots=True)
class DailyPoint:
    """One settled session's score, and what produced it."""

    date: str
    predictions: int
    direction_accuracy: float
    baseline_up_rate: float
    edge: float
    signals: int
    signal_win_rate: float | None
    net_profit_jpy: float
    model_version: str | None

    @property
    def is_reliable(self) -> bool:
        return self.predictions >= MINIMUM_PREDICTIONS_PER_DAY


def daily_points(rows: list[dict[str, Any]]) -> list[DailyPoint]:
    """Score each session that has closed, oldest first."""

    by_date: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        if row.get("actual_return") is None or row.get("direction_correct") is None:
            continue
        by_date[str(row["date"])].append(row)

    points: list[DailyPoint] = []
    for day in sorted(by_date):
        settled = by_date[day]
        correct = sum(1 for row in settled if row["direction_correct"])
        rose = sum(1 for row in settled if float(row["actual_return"]) > 0.0)
        accuracy = correct / len(settled)
        # "Always up" on this day: the honest zero-effort comparison, and the one
        # the model has to beat before anything else matters.
        baseline = rose / len(settled)
        signals = [row for row in settled if row.get("signal") == "BUY"]
        signal_wins = sum(1 for row in signals if float(row["actual_return"]) > 0.0)
        versions = {
            str(row["model_version"]) for row in settled if row.get("model_version")
        }
        points.append(
            DailyPoint(
                date=day,
                predictions=len(settled),
                direction_accuracy=accuracy,
                baseline_up_rate=baseline,
                edge=accuracy - baseline,
                signals=len(signals),
                signal_win_rate=(signal_wins / len(signals)) if signals else None,
                net_profit_jpy=sum(
                    float(row.get("net_profit_jpy") or 0.0) for row in signals
                ),
                # More than one version in a day means a change landed mid-run;
                # naming both is more useful than picking one.
                model_version=" / ".join(sorted(versions)) if versions else None,
            )
        )
    return points


def rolling_series(
    points: list[DailyPoint], window: int = DEFAULT_ROLLING_SESSIONS
) -> list[dict[str, Any]]:
    """Add rolling and cumulative views to each session.

    The rolling figures stay ``None`` until the window is full rather than
    averaging whatever is available: a "20-session accuracy" computed from three
    sessions is a different statistic wearing the same label.
    """

    series: list[dict[str, Any]] = []
    accuracies: list[float] = []
    baselines: list[float] = []
    profit = 0.0
    for index, point in enumerate(points):
        accuracies.append(point.direction_accuracy)
        baselines.append(point.baseline_up_rate)
        profit += point.net_profit_jpy
        full = index + 1 >= window
        series.append(
            {
                "date": point.date,
                "予測数": point.predictions,
                "方向的中率": point.direction_accuracy,
                "常に上昇と予測した場合": point.baseline_up_rate,
                f"方向的中率({window}日移動平均)": (
                    sum(accuracies[-window:]) / window if full else None
                ),
                f"常に上昇({window}日移動平均)": (
                    sum(baselines[-window:]) / window if full else None
                ),
                "累積の方向的中率": sum(accuracies) / (index + 1),
                "累積の常に上昇": sum(baselines) / (index + 1),
                "累積損益": profit,
                "model_version": point.model_version,
            }
        )
    return series


def version_changes(points: list[DailyPoint]) -> list[dict[str, str]]:
    """Return the sessions where the model version changed.

    A performance series without these is unreadable for the purpose it exists
    for: you cannot credit a change you cannot locate.
    """

    changes: list[dict[str, str]] = []
    previous: str | None = None
    for point in points:
        if point.model_version and point.model_version != previous:
            changes.append(
                {
                    "date": point.date,
                    "model_version": point.model_version,
                    "previous": previous or "—",
                }
            )
            previous = point.model_version
    return changes


def version_summary(points: list[DailyPoint]) -> list[dict[str, Any]]:
    """Score each model version over the sessions it actually produced."""

    by_version: dict[str, list[DailyPoint]] = defaultdict(list)
    for point in points:
        by_version[point.model_version or "unknown"].append(point)

    summary: list[dict[str, Any]] = []
    for version, group in by_version.items():
        predictions = sum(point.predictions for point in group)
        weighted = sum(point.direction_accuracy * point.predictions for point in group)
        baseline = sum(point.baseline_up_rate * point.predictions for point in group)
        summary.append(
            {
                "model_version": version,
                "from": group[0].date,
                "to": group[-1].date,
                "sessions": len(group),
                "predictions": predictions,
                "direction_accuracy": weighted / predictions if predictions else None,
                "baseline_up_rate": baseline / predictions if predictions else None,
                "edge": ((weighted - baseline) / predictions if predictions else None),
                "net_profit_jpy": sum(point.net_profit_jpy for point in group),
            }
        )
    return sorted(summary, key=lambda item: item["from"])
