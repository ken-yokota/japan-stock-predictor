"""Compare arms on the sessions all of them actually predicted.

Two arms scored over different date sets are not comparable, and the difference
is not small: an arm that failed on the twelve worst sessions and was scored on
the remaining 238 will look better than one that predicted all 250, for a reason
that has nothing to do with the model. The estimator arms fail independently --
a quantile fit can raise where a ridge returns a number -- so the overlap has to
be taken explicitly rather than assumed.

So every comparison here is restricted to the (date, ticker) pairs present in
every arm, the count that survived is reported beside the results, and an arm
that would shrink the common set materially is named rather than silently
dropping the sessions for everyone.

The ordering is the one that decides things, in the operator's stated priority:

    Net Expectancy > Profit Factor > Max Drawdown > BUY Win Rate
                   > Direction Accuracy > Correlation > MAE

MAE last is deliberate and not an oversight. Predicting a flat zero has the
lowest MAE of anything measured here -- 1.2073% against the production arm's
higher figure -- and a model that never takes a position cannot lose money or
make any.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from research.evaluation import (
    Evaluation,
    Prediction,
    evaluate,
    from_research_rows,
    without_costs,
)
from research.universe import round_trip_cost

# Below this the overlap is not a comparison, it is a different experiment.
MINIMUM_COMMON_PAIRS = 1000


@dataclass(frozen=True, slots=True)
class Arm:
    """One walk-forward run, named by what varied."""

    label: str
    path: Path
    predictions: list[Prediction]
    estimator: str | None = None
    feature_set: str | None = None

    @property
    def keys(self) -> set[tuple[str, str]]:
        return {(row.date, row.ticker) for row in self.predictions}


def load_arm(path: Path, *, label: str | None = None) -> Arm:
    """Read one artifact produced by the walk-forward runner.

    An artifact fixes its yen figures at the cost in force when it ran. Costs
    went to zero on 2026-08-29, so an arm generated before that would report a
    costed trading layer beside a universe study that charges nothing, and the
    two tables in one report would not be on the same basis.

    Zeroing is exact -- see ``research.evaluation.without_costs`` -- so it is
    applied when the configuration charges nothing. Any other change of rate is
    not exact, and rather than approximate one, the artifact's own figures are
    kept and the caller is left to re-run the arm.
    """

    payload: dict[str, Any] = json.loads(Path(path).read_text(encoding="utf-8"))
    rows = from_research_rows(payload.get("predictions", []))
    if round_trip_cost() == 0.0:
        rows = without_costs(rows)
    feature_set = payload.get("feature_set")
    estimator = payload.get("estimator") or None
    default = feature_set or Path(path).stem
    if estimator:
        default = f"{default} / {estimator}"
    return Arm(
        label=label or str(default),
        path=Path(path),
        predictions=rows,
        estimator=estimator,
        feature_set=feature_set,
    )


def common_keys(arms: Sequence[Arm]) -> set[tuple[str, str]]:
    """The (date, ticker) pairs every arm produced a scored prediction for."""

    if not arms:
        return set()
    shared = arms[0].keys
    for arm in arms[1:]:
        shared &= arm.keys
    return shared


def restrict(arm: Arm, keys: set[tuple[str, str]]) -> list[Prediction]:
    return [row for row in arm.predictions if (row.date, row.ticker) in keys]


@dataclass(frozen=True, slots=True)
class Comparison:
    """Every arm scored on one shared set of predictions."""

    evaluations: list[Evaluation]
    common_pairs: int
    common_sessions: int
    dropped: dict[str, int]

    @property
    def underpowered(self) -> bool:
        return self.common_pairs < MINIMUM_COMMON_PAIRS


def compare(arms: Sequence[Arm]) -> Comparison:
    keys = common_keys(arms)
    dropped = {arm.label: len(arm.keys) - len(keys) for arm in arms}
    return Comparison(
        evaluations=[
            evaluate(restrict(arm, keys), label=arm.label) for arm in arms
        ],
        common_pairs=len(keys),
        common_sessions=len({date for date, _ in keys}),
        dropped=dropped,
    )


def _key(evaluation: Evaluation) -> tuple[float, ...]:
    """The operator's ordering, as a sort key. Larger is better throughout.

    Missing values sort last rather than as zero: an arm that took no trades has
    no expectancy, and treating that as break-even would rank it above every arm
    that lost money by actually trading.
    """

    trading = evaluation.trading
    model = evaluation.model
    worst = float("-inf")
    return (
        trading.expectancy_jpy if trading.expectancy_jpy is not None else worst,
        trading.profit_factor if trading.profit_factor is not None else worst,
        trading.max_drawdown_jpy,  # negative; closer to zero is better
        trading.win_rate if trading.win_rate is not None else worst,
        model.direction_accuracy,
        model.spearman if model.spearman is not None else worst,
        -model.mae,
    )


def ranked(comparison: Comparison) -> list[Evaluation]:
    return sorted(comparison.evaluations, key=_key, reverse=True)


def _number(value: float | None, fmt: str, *, scale: float = 1.0) -> str:
    if value is None:
        return "—"
    return format(value * scale, fmt)


def model_table(comparison: Comparison) -> list[str]:
    rows = [
        f"{'アーム':<28}{'MAE%':>8}{'RMSE%':>8}{'Pearson':>9}"
        f"{'Spearman':>10}{'方向的中':>9}{'較正傾き':>9}",
        "-" * 81,
    ]
    for item in comparison.evaluations:
        model = item.model
        rows.append(
            f"{item.label[:27]:<28}"
            f"{_number(model.mae, '.4f', scale=100):>8}"
            f"{_number(model.rmse, '.4f', scale=100):>8}"
            f"{_number(model.pearson, '+.4f'):>9}"
            f"{_number(model.spearman, '+.4f'):>10}"
            f"{_number(model.direction_accuracy, '.2%'):>9}"
            f"{_number(model.calibration_slope, '.3f'):>9}"
        )
    return rows


def selection_table(comparison: Comparison) -> list[str]:
    rows = [
        f"{'アーム':<28}{'順位IC':>9}{'t値':>8}{'Top5超過%':>11}"
        f"{'t値':>8}{'上下差%':>10}",
        "-" * 74,
    ]
    for item in comparison.evaluations:
        pick = item.selection
        rows.append(
            f"{item.label[:27]:<28}"
            f"{_number(pick.rank_ic_mean, '+.4f'):>9}"
            f"{_number(pick.rank_ic_t, '+.2f'):>8}"
            f"{_number(pick.top5_alpha, '+.4f', scale=100):>11}"
            f"{_number(pick.top5_alpha_t, '+.2f'):>8}"
            f"{_number(pick.top_bottom_spread, '+.4f', scale=100):>10}"
        )
    return rows


def probability_table(comparison: Comparison) -> list[str]:
    rows = [
        f"{'アーム':<28}{'Brier':>9}{'LogLoss':>10}{'実際の上昇率':>13}",
        "-" * 60,
    ]
    for item in comparison.evaluations:
        chance = item.probability
        rows.append(
            f"{item.label[:27]:<28}"
            f"{_number(chance.brier, '.4f'):>9}"
            f"{_number(chance.log_loss, '.4f'):>10}"
            f"{_number(chance.base_rate, '.2%'):>13}"
        )
    return rows


def trading_table(comparison: Comparison) -> list[str]:
    rows = [
        f"{'アーム':<28}{'建玉':>6}{'純損益円':>11}{'期待値円':>10}"
        f"{'PF':>7}{'勝率':>8}{'最大DD円':>11}",
        "-" * 81,
    ]
    for item in comparison.evaluations:
        trade = item.trading
        rows.append(
            f"{item.label[:27]:<28}"
            f"{trade.trades:>6}"
            f"{trade.net_jpy:>+11,.0f}"
            f"{_number(trade.expectancy_jpy, '+,.0f'):>10}"
            f"{_number(trade.profit_factor, '.2f'):>7}"
            f"{_number(trade.win_rate, '.1%'):>8}"
            f"{trade.max_drawdown_jpy:>+11,.0f}"
        )
    return rows


def report(comparison: Comparison) -> list[str]:
    """The four layers, then the ranking that actually decides."""

    lines = [
        f"共通(日付, 銘柄) {comparison.common_pairs}件"
        f" / {comparison.common_sessions}営業日",
    ]
    extra = {name: n for name, n in comparison.dropped.items() if n}
    if extra:
        lines.append(
            "  共通集合外として除外: "
            + "、".join(f"{name} {count}件" for name, count in extra.items())
        )
    if comparison.underpowered:
        lines.append(
            f"  ※ 共通集合が{MINIMUM_COMMON_PAIRS}件未満です。"
            "比較ではなく別の実験になっています。"
        )
    for title, builder in (
        ("【Model層】", model_table),
        ("【Selection層】", selection_table),
        ("【Probability層】", probability_table),
        ("【Trading層】", trading_table),
    ):
        lines += ["", title, *builder(comparison)]
    lines += [
        "",
        "【優先順位による順位】"
        " Net期待値 > PF > 最大DD > 勝率 > 方向的中 > 相関 > MAE",
        "-" * 60,
    ]
    for position, item in enumerate(ranked(comparison), start=1):
        trade = item.trading
        lines.append(
            f"  {position}. {item.label[:34]:<36}"
            f"期待値 {_number(trade.expectancy_jpy, '+,.0f'):>8}円"
            f" / 建玉 {trade.trades}"
        )
    lines += [
        "",
        "  取引層の標本は常に最小です。順位はこの標本で決まっており、"
        "優位性の証明ではありません。",
    ]
    return lines


__all__ = [
    "Arm",
    "Comparison",
    "common_keys",
    "compare",
    "load_arm",
    "ranked",
    "report",
    "restrict",
]
