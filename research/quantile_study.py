"""What the fitted quantiles are worth beyond their median.

The quantile arm returns a distribution per session, not a point. Three things
follow from that, and only the third is a change to how anything trades:

    interval coverage   an 80% interval should contain the outcome 80% of the
                        time. Production quotes an interval built from
                        in-sample residuals which has never been checked
                        against an outcome at all.
    q25 > 0 as a rule   "the 25th percentile is above zero" is a stricter and
                        more honest confidence statement than a classifier's
                        0.60, because it is a claim about the distribution the
                        model actually fitted.
    P(up) replacement   the share of the distribution above zero, against the
                        separate logistic. The logistic is not merely weak --
                        where it disagrees with the regression about the sign,
                        the regression is right 52.4% of the time and it is
                        right 47.6%.

Coverage is the diagnostic that cannot be argued with. A model whose 80%
interval contains 55% of outcomes is not conservative or aggressive, it is
wrong about its own uncertainty, and every probability derived from it inherits
that.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

import numpy as np

from research.universe import round_trip_cost

# The pairs the runner fits. Named here so a missing level is an error rather
# than a silently narrower report.
INTERVALS: tuple[tuple[str, str, float], ...] = (
    ("q0.1", "q0.9", 0.80),
    ("q0.25", "q0.75", 0.50),
)


@dataclass(frozen=True, slots=True)
class Coverage:
    """How often the outcome landed inside an interval that claimed it would."""

    low: str
    high: str
    nominal: float
    observed: float
    count: int
    mean_width: float

    @property
    def shortfall(self) -> float:
        return self.observed - self.nominal


@dataclass(frozen=True, slots=True)
class RuleResult:
    """One buy condition, scored on the sessions it fired."""

    name: str
    positions: int
    sessions: int
    mean_net: float
    total_net: float
    win_rate: float
    direction_accuracy: float

    # ``total_net`` is the sum of equally weighted daily returns, the same
    # convention research/universe.py uses. Summing per position instead makes a
    # rule that opens 2,724 positions read as -455%, which is not a portfolio
    # anyone could have held, and it cannot be compared with the universe table
    # in the same report.


def coverage(rows: Sequence[dict[str, Any]]) -> list[Coverage]:
    """Observed against nominal, for each fitted interval."""

    out: list[Coverage] = []
    for low, high, nominal in INTERVALS:
        inside: list[bool] = []
        widths: list[float] = []
        for row in rows:
            quantiles = row.get("quantiles")
            actual = row.get("actual_return")
            if not quantiles or actual is None:
                continue
            if low not in quantiles or high not in quantiles:
                continue
            lower, upper = float(quantiles[low]), float(quantiles[high])
            inside.append(lower <= float(actual) <= upper)
            widths.append(upper - lower)
        out.append(
            Coverage(
                low=low,
                high=high,
                nominal=nominal,
                observed=float(np.mean(inside)) if inside else 0.0,
                count=len(inside),
                mean_width=float(np.mean(widths)) if widths else 0.0,
            )
        )
    return out


def _score(name: str, taken: Sequence[dict[str, Any]], cost: float) -> RuleResult:
    """One rule, scored per position and accumulated per session.

    Days the rule sat out contribute nothing rather than being dropped, so a
    rule that trades twice in 250 sessions cannot post a large cumulative from
    two good days.
    """

    if not taken:
        return RuleResult(name, 0, 0, 0.0, 0.0, 0.0, 0.0)
    net = np.array(
        [float(row["actual_return"]) - cost for row in taken], dtype=float
    )
    correct = [
        (float(row["predicted_return"]) > 0) == (float(row["actual_return"]) > 0)
        for row in taken
    ]
    by_day: dict[str, list[float]] = {}
    for row, value in zip(taken, net, strict=True):
        by_day.setdefault(str(row["date"]), []).append(float(value))
    daily = [float(np.mean(values)) for values in by_day.values()]
    return RuleResult(
        name=name,
        positions=len(taken),
        sessions=len(by_day),
        mean_net=float(net.mean()),
        total_net=float(np.sum(daily)),
        win_rate=float((net > 0).mean()),
        direction_accuracy=float(np.mean(correct)),
    )


def buy_rules(rows: Sequence[dict[str, Any]]) -> list[RuleResult]:
    """The quantile conditions against the two the system already uses.

    The control is first. A rule that cannot beat "buy every name whose forecast
    is positive" has not earned the extra machinery.
    """

    cost = round_trip_cost()
    usable = [
        row
        for row in rows
        if row.get("actual_return") is not None
        and row.get("predicted_return") is not None
    ]

    def _taken(condition: Any) -> list[dict[str, Any]]:
        return [row for row in usable if condition(row)]

    def _quantile(row: dict[str, Any], name: str) -> float | None:
        quantiles = row.get("quantiles")
        if not quantiles or name not in quantiles:
            return None
        return float(quantiles[name])

    rules: list[tuple[str, Any]] = [
        ("対照 予測>0", lambda r: float(r["predicted_return"]) > 0),
        (
            "現行相当 予測>0.3% かつ P(up)>=0.60",
            lambda r: float(r["predicted_return"]) > 0.003
            and float(r.get("probability_up") or 0.0) >= 0.60,
        ),
        (
            "中央値>0.3%",
            lambda r: float(r["predicted_return"]) > 0.003,
        ),
        (
            "q25>0（下側25%が正）",
            lambda r: (_quantile(r, "q0.25") or -1.0) > 0.0,
        ),
        (
            "q25>0 かつ 中央値>0.3%",
            lambda r: (_quantile(r, "q0.25") or -1.0) > 0.0
            and float(r["predicted_return"]) > 0.003,
        ),
        (
            "q10>0（下側10%が正）",
            lambda r: (_quantile(r, "q0.1") or -1.0) > 0.0,
        ),
        (
            "q25>コスト（下側25%が費用を超える）",
            lambda r: (_quantile(r, "q0.25") or -1.0) > cost,
        ),
    ]
    return [_score(name, _taken(condition), cost) for name, condition in rules]


@dataclass(frozen=True, slots=True)
class ProbabilitySource:
    """One way of producing P(up), scored the same way."""

    name: str
    count: int
    brier: float
    log_loss: float
    accuracy_at_half: float


def _brier(probabilities: np.ndarray, outcomes: np.ndarray) -> float:
    return float(np.mean((probabilities - outcomes) ** 2))


def _log_loss(probabilities: np.ndarray, outcomes: np.ndarray) -> float:
    clipped = np.clip(probabilities, 1e-6, 1 - 1e-6)
    return float(
        -np.mean(outcomes * np.log(clipped) + (1 - outcomes) * np.log(1 - clipped))
    )


def probability_sources(
    quantile_rows: Sequence[dict[str, Any]],
    logistic_rows: Sequence[dict[str, Any]],
) -> list[ProbabilitySource]:
    """The distribution's own P(up) against the separate classifier.

    Both are restricted to the (date, ticker) pairs present in each, or the
    comparison is between different samples rather than between two answers to
    the same question.
    """

    def _index(rows: Sequence[dict[str, Any]]) -> dict[tuple[str, str], dict[str, Any]]:
        return {
            (str(row["date"]), str(row["ticker"])): row
            for row in rows
            if row.get("actual_return") is not None
            and row.get("probability_up") is not None
        }

    left, right = _index(quantile_rows), _index(logistic_rows)
    shared = sorted(set(left) & set(right))
    if not shared:
        return []

    outcomes = np.array(
        [1.0 if float(left[key]["actual_return"]) > 0 else 0.0 for key in shared]
    )
    out: list[ProbabilitySource] = []
    for name, table in (("分位点由来 P(up)", left), ("ロジスティック P(up)", right)):
        values = np.array(
            [float(table[key]["probability_up"]) for key in shared], dtype=float
        )
        out.append(
            ProbabilitySource(
                name=name,
                count=len(shared),
                brier=_brier(values, outcomes),
                log_loss=_log_loss(values, outcomes),
                accuracy_at_half=float(np.mean((values > 0.5) == (outcomes > 0.5))),
            )
        )
    return out


def report(
    quantile_rows: Sequence[dict[str, Any]],
    logistic_rows: Sequence[dict[str, Any]] | None = None,
) -> list[str]:
    lines = ["【区間の被覆】名目に対して実際に何%入ったか", ""]
    lines.append(
        f"  {'区間':<14}{'名目':>7}{'実測':>8}{'差':>8}{'平均幅':>9}{'標本':>8}"
    )
    lines.append("  " + "-" * 54)
    for item in coverage(quantile_rows):
        lines.append(
            f"  {item.low}〜{item.high:<8}{item.nominal:>7.0%}"
            f"{item.observed:>8.1%}{item.shortfall:>+8.1%}"
            f"{item.mean_width * 100:>8.2f}%{item.count:>8}"
        )
    lines += [
        "",
        "  被覆が名目を大きく下回るなら、この分布は自分の不確実性を過小評価しており、",
        "  そこから読んだ確率もすべてその誤りを引き継ぎます。",
        "",
        f"【BUY条件】往復コスト {round_trip_cost() * 100:.3f}% 控除後",
        "",
        f"  {'条件':<32}{'建玉':>7}{'取引日':>7}{'平均純':>9}"
        f"{'累積純':>10}{'勝率':>8}{'方向的中':>9}",
        "  " + "-" * 82,
    ]
    for rule in buy_rules(quantile_rows):
        lines.append(
            f"  {rule.name:<32}{rule.positions:>7}{rule.sessions:>7}"
            f"{rule.mean_net * 100:>+9.4f}%{rule.total_net * 100:>+10.2f}%"
            f"{rule.win_rate:>8.1%}{rule.direction_accuracy:>9.2%}"
        )
    if logistic_rows is not None:
        sources = probability_sources(quantile_rows, logistic_rows)
        lines += [
            "",
            "【P(up)の出どころ】同じ(日付,銘柄)だけで比較",
            "",
            f"  {'出どころ':<24}{'標本':>8}{'Brier':>9}{'LogLoss':>10}{'的中':>8}",
            "  " + "-" * 60,
        ]
        for source in sources:
            lines.append(
                f"  {source.name:<24}{source.count:>8}{source.brier:>9.4f}"
                f"{source.log_loss:>10.4f}{source.accuracy_at_half:>8.2%}"
            )
    return lines


__all__ = [
    "Coverage",
    "ProbabilitySource",
    "RuleResult",
    "buy_rules",
    "coverage",
    "probability_sources",
    "report",
]
