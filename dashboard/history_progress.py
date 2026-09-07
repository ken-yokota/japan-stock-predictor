"""Daily accuracy and return series for the History page.

A new module rather than an addition to ``progress`` for the deployment reason
the other split-out modules record: Streamlit Cloud keeps already-imported
modules in memory while re-reading changed pages, so a page reaching for a new
name in an old module raises ImportError until someone reboots the container.

Everything here is *deviation from a coin flip*, not raw accuracy. A direction
call has two outcomes, so 50% is the score of knowing nothing; plotting raw
accuracy puts the meaningful line in the top half of the chart and wastes the
bottom half on scores worse than guessing. Centring on zero makes "better than
nothing" the thing you read off the axis, which is the only question a
direction hit rate answers.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from dashboard.catalog import sector_label, stock_label

# A coin flip. Direction has two outcomes, so this is what knowing nothing
# scores, and every series here is measured against it.
COIN_FLIP = 0.5

GROUP_BY_SECTOR = "業界別"
GROUP_BY_TICKER = "銘柄別"
GROUPINGS: tuple[str, ...] = (GROUP_BY_SECTOR, GROUP_BY_TICKER)


@dataclass(frozen=True, slots=True)
class AccuracyPoint:
    """One session's direction accuracy, and its distance from a coin flip."""

    date: str
    accuracy: float
    deviation: float
    count: int


def _settled(rows: Iterable[Mapping[str, Any]]) -> list[Mapping[str, Any]]:
    """Rows whose session closed and whose direction could be judged.

    A prediction with no observed close is not a miss; it is a day that has
    not finished. Counting it either way would move the line for a reason that
    has nothing to do with the model.
    """

    return [
        row
        for row in rows
        if row.get("actual_return") is not None
        and row.get("direction_correct") is not None
    ]


def _is_buy(row: Mapping[str, Any]) -> bool:
    return str(row.get("signal", "")).upper() == "BUY"


def accuracy_series(
    rows: Iterable[Mapping[str, Any]], *, buy_only: bool
) -> list[AccuracyPoint]:
    """Direction accuracy per session, oldest first.

    ``buy_only`` restricts to the names the rule actually recommended that
    morning. The unrestricted series is the wider question -- can the model
    call direction at all -- and the two disagree often enough that showing
    only one of them would be misleading about which is being claimed.
    """

    by_date: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for row in _settled(rows):
        if buy_only and not _is_buy(row):
            continue
        by_date[str(row.get("date", ""))].append(row)

    points: list[AccuracyPoint] = []
    for day in sorted(by_date):
        settled = by_date[day]
        if not settled:
            continue
        correct = sum(1 for row in settled if row["direction_correct"])
        accuracy = correct / len(settled)
        points.append(
            AccuracyPoint(
                date=day,
                accuracy=accuracy,
                deviation=accuracy - COIN_FLIP,
                count=len(settled),
            )
        )
    return points


def cumulative_profit(rows: Iterable[Mapping[str, Any]]) -> list[tuple[str, float]]:
    """Running total of the simulated yen result, oldest first.

    Only settled sessions contribute. The figure is what the recorded trade
    sizes would have produced; it is not a broker statement and carries no
    costs the simulation did not model.
    """

    by_date: dict[str, float] = defaultdict(float)
    for row in _settled(rows):
        value = row.get("net_profit_jpy")
        if value is None:
            continue
        by_date[str(row.get("date", ""))] += float(value)

    running = 0.0
    output: list[tuple[str, float]] = []
    for day in sorted(by_date):
        running += by_date[day]
        output.append((day, running))
    return output


def _group_key(row: Mapping[str, Any], grouping: str) -> str:
    ticker = str(row.get("ticker", ""))
    if grouping == GROUP_BY_SECTOR:
        return sector_label(ticker)
    return stock_label(ticker)


def grouped_accuracy(
    rows: Iterable[Mapping[str, Any]], *, grouping: str, buy_only: bool = False
) -> list[dict[str, Any]]:
    """Direction accuracy per session per sector or per ticker.

    Deviation is carried alongside the raw rate for the same reason the
    top-level series is centred: a ticker at 0.5 has demonstrated nothing, and
    a chart that does not make that the origin invites reading it as skill.
    """

    if grouping not in GROUPINGS:
        raise ValueError(f"unknown grouping: {grouping}")

    buckets: dict[tuple[str, str], list[Mapping[str, Any]]] = defaultdict(list)
    for row in _settled(rows):
        if buy_only and not _is_buy(row):
            continue
        buckets[(str(row.get("date", "")), _group_key(row, grouping))].append(row)

    output: list[dict[str, Any]] = []
    for (day, group), settled in sorted(buckets.items()):
        correct = sum(1 for row in settled if row["direction_correct"])
        accuracy = correct / len(settled)
        output.append(
            {
                "date": day,
                "group": group,
                "accuracy": accuracy,
                "deviation": accuracy - COIN_FLIP,
                "count": len(settled),
            }
        )
    return output


def grouped_returns(
    rows: Iterable[Mapping[str, Any]], *, grouping: str, buy_only: bool = False
) -> list[dict[str, Any]]:
    """Mean predicted and mean realised return per session per group.

    Both sides are averaged over the same rows, so a gap between the lines is
    the model's bias on that group and not an artefact of one side including
    sessions the other one dropped.
    """

    if grouping not in GROUPINGS:
        raise ValueError(f"unknown grouping: {grouping}")

    buckets: dict[tuple[str, str], list[tuple[float, float]]] = defaultdict(list)
    for row in _settled(rows):
        if buy_only and not _is_buy(row):
            continue
        predicted, actual = row.get("predicted_return"), row.get("actual_return")
        if predicted is None or actual is None:
            continue
        key = (str(row.get("date", "")), _group_key(row, grouping))
        buckets[key].append((float(predicted), float(actual)))

    output: list[dict[str, Any]] = []
    for (day, group), pairs in sorted(buckets.items()):
        output.append(
            {
                "date": day,
                "group": group,
                "predicted_mean": sum(pair[0] for pair in pairs) / len(pairs),
                "actual_mean": sum(pair[1] for pair in pairs) / len(pairs),
                "count": len(pairs),
            }
        )
    return output


def pivot(
    records: Sequence[Mapping[str, Any]], *, value: str
) -> dict[str, dict[str, float]]:
    """``{date: {group: value}}``, for handing straight to a wide-form chart."""

    table: dict[str, dict[str, float]] = defaultdict(dict)
    for record in records:
        table[str(record["date"])][str(record["group"])] = float(record[value])
    return dict(table)
