"""How much of each arm's fit was memorisation of its own training window.

A tree on 120 rows against 38 columns can reach any in-sample accuracy you ask
of it. The number that matters is not either score but the distance between
them: an arm at 85% in-sample and 51% out of sample has learned the window, and
one at 54% and 53% has learned something small and real. Both can be reported as
"53% out of sample" and they are not the same finding.

That distance cannot be recovered afterwards. The fitted model is discarded once
the session is scored, so every arm records its own window's MAE and direction
accuracy at fit time and this reads them back.

The hyperparameters are here for the same reason. A depth grid that always
selects its deepest option is a grid that was too shallow to bind, and one that
always selects its shallowest is telling you the data does not support a tree at
all. Which of those happened is invisible from the out-of-sample score.

Every choice was made by forward-chaining cross-validation inside the training
window. Random K-fold would let a fold contain sessions after the ones it is
scored on, which on a time series is a leak dressed as validation, and it is not
used anywhere in this repository.
"""

from __future__ import annotations

import json
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np


@dataclass(frozen=True, slots=True)
class Gap:
    """One arm's in-sample score against the out-of-sample one."""

    label: str
    count: int
    train_direction: float
    oos_direction: float
    train_mae: float
    oos_mae: float

    @property
    def direction_gap(self) -> float:
        """Percentage points of direction accuracy lost leaving the window."""

        return (self.train_direction - self.oos_direction) * 100

    @property
    def mae_ratio(self) -> float:
        """How much larger the out-of-sample error is. 1.0 means no gap."""

        return self.oos_mae / self.train_mae if self.train_mae > 0 else float("nan")

    @property
    def memorised(self) -> bool:
        """A gap this wide is the window being learned, not the market.

        Ten points is deliberately generous. The arms here predict a target
        whose sign is close to a coin toss, so an honest fit has almost no room
        to be much better in-sample than out.
        """

        return self.direction_gap >= 10.0


def gap(rows: Sequence[dict[str, Any]], *, label: str) -> Gap | None:
    """The two scores side by side, or None when the arm recorded no in-sample."""

    usable = [
        row
        for row in rows
        if row.get("train_direction") is not None
        and row.get("train_mae") is not None
        and row.get("actual_return") is not None
    ]
    if not usable:
        return None
    actual = np.array([float(row["actual_return"]) for row in usable])
    predicted = np.array([float(row["predicted_return"]) for row in usable])
    return Gap(
        label=label,
        count=len(usable),
        train_direction=float(
            np.mean([float(row["train_direction"]) for row in usable])
        ),
        oos_direction=float(np.mean((predicted > 0) == (actual > 0))),
        train_mae=float(np.mean([float(row["train_mae"]) for row in usable])),
        oos_mae=float(np.mean(np.abs(predicted - actual))),
    )


@dataclass(frozen=True, slots=True)
class Setting:
    """How often one hyperparameter took each of its values."""

    name: str
    counts: dict[str, int]

    @property
    def total(self) -> int:
        return sum(self.counts.values())

    @property
    def dominant(self) -> tuple[str, float]:
        if not self.counts:
            return ("—", 0.0)
        value, count = max(self.counts.items(), key=lambda item: item[1])
        return value, count / self.total if self.total else 0.0

    @property
    def pinned_at_an_edge(self) -> bool:
        """Whether the grid never really chose.

        A setting taken in over 95% of fits was not selected by the data. Which
        of the two reasons applies -- the grid could not reach further, or the
        value was never searched at all -- cannot be told apart from the counts,
        so the report says both rather than guessing. Either way it is a
        statement about the search, and it stays invisible if only the
        out-of-sample score is reported.
        """

        return self.dominant[1] > 0.95


def settings(rows: Sequence[dict[str, Any]]) -> list[Setting]:
    """Every hyperparameter the arm recorded, and how it was distributed."""

    tallies: dict[str, Counter[str]] = {}
    for row in rows:
        parameters = row.get("estimator_parameters")
        if not isinstance(parameters, dict):
            continue
        for name, value in parameters.items():
            if isinstance(value, list | dict):
                continue
            tallies.setdefault(name, Counter())[str(value)] += 1
    return [
        Setting(name=name, counts=dict(counter))
        for name, counter in sorted(tallies.items())
    ]


def load(path: str) -> tuple[str, list[dict[str, Any]]]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    label = str(payload.get("feature_set") or path)
    estimator = payload.get("estimator")
    if estimator:
        label = f"{label} / {estimator}"
    return label, list(payload.get("predictions", []))


def report(arms: Sequence[tuple[str, Sequence[dict[str, Any]]]]) -> list[str]:
    lines = [
        "【学習窓の中と外】in-sample は捨てられる前に記録した値",
        "",
        f"  {'アーム':<26}{'標本':>7}{'窓内方向':>10}{'窓外方向':>10}"
        f"{'差(pp)':>9}{'窓内MAE%':>10}{'窓外MAE%':>10}{'比':>7}",
        "  " + "-" * 90,
    ]
    measured = 0
    for label, rows in arms:
        item = gap(rows, label=label)
        if item is None:
            lines.append(f"  {label:<26}（in-sample の記録なし）")
            continue
        measured += 1
        lines.append(
            f"  {item.label[:25]:<26}{item.count:>7}"
            f"{item.train_direction:>10.2%}{item.oos_direction:>10.2%}"
            f"{item.direction_gap:>+9.2f}{item.train_mae * 100:>10.4f}"
            f"{item.oos_mae * 100:>10.4f}{item.mae_ratio:>7.2f}"
        )
    if measured:
        lines += [
            "",
            "  差が10ポイント以上なら、学習窓そのものを覚えたと見なします。"
            "この目的変数は符号がほぼコイン投げなので、",
            "  正直な当てはめには窓内で大きく上回る余地がありません。",
        ]

    lines += ["", "【選ばれたハイパーパラメータ】窓内の時系列CVのみで決定", ""]
    for label, rows in arms:
        chosen = settings(rows)
        if not chosen:
            continue
        lines.append(f"  {label}")
        for setting in chosen:
            spread = "、".join(
                f"{value} が {count}回"
                for value, count in sorted(
                    setting.counts.items(), key=lambda item: -item[1]
                )
            )
            note = (
                "  ← 1値のみ（グリッドの天井か、そもそも固定）"
                if setting.pinned_at_an_edge
                else ""
            )
            lines.append(f"    {setting.name:<16}{spread}{note}")
        lines.append("")
    lines.append(
        "  分割は TimeSeriesSplit のみです。Random K-Fold はこのリポジトリの"
        "どこでも使っていません。"
    )
    return lines


__all__ = ["Gap", "Setting", "gap", "load", "report", "settings"]
