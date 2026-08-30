#!/usr/bin/env python3
"""Score every family's replayed forecasts, and say what each would have bought.

Reads the artifact ``run_all_method_backtest`` produced and applies buy rules
to it. Kept separate from the fitting on purpose: a threshold chosen by
re-running the fits until a number improves is a threshold fitted to the
outcome, and separating the two makes that impossible by construction rather
than by discipline.

The operator asked for the centre and the risk to be reconsidered as p50 and
p90, so the rules are built from those:

    中央値>閾値      the median beats a hurdle -- the plain reading of "this
                    is likely to rise by at least this much"
    p10>0           the *lower* tenth is positive -- buy only when even a bad
                    day is up. Far stricter, and it will trade rarely
    中央値>閾値かつP(上昇)  the current production shape, per family
    p90>閾値         the upside case clears the hurdle. Included because it is
                    the natural counterpart to p10, and because it should be
                    *bad*: selecting on the optimistic tail is what a lottery
                    ticket looks like, and seeing it fail is informative

Every number here is a replay of models that did not run at the time. It says
what these families would have said. It is not this system's live record, it
cannot be reported as one, and with fourteen sessions it cannot establish that
any family beats any other -- fourteen days of twenty-two names is far too few
for a difference in win rate to mean anything. What it can do is show whether
a family is obviously broken, and rank them for a longer test.
"""

from __future__ import annotations

import argparse
import json
import math
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True, slots=True)
class RuleResult:
    """One buy rule applied to one family."""

    arm: str
    label: str
    rule: str
    positions: int
    sessions: int
    wins: int
    win_rate: float | None
    mean_return: float
    total_return: float
    direction_accuracy: float | None


def _quantile(row: dict[str, Any], name: str) -> float | None:
    value = row.get("quantiles", {}).get(name)
    return None if value is None else float(value)


def _rules(threshold: float) -> dict[str, Callable[[dict[str, Any]], bool]]:
    def median_above(row: dict[str, Any]) -> bool:
        value = _quantile(row, "q0.5")
        return value is not None and value > threshold

    def lower_positive(row: dict[str, Any]) -> bool:
        value = _quantile(row, "q0.1")
        return value is not None and value > 0.0

    def median_and_probability(row: dict[str, Any]) -> bool:
        probability = row.get("probability_up")
        return (
            median_above(row) and probability is not None and float(probability) >= 0.60
        )

    def upper_above(row: dict[str, Any]) -> bool:
        value = _quantile(row, "q0.9")
        return value is not None and value > threshold

    return {
        f"中央値(p50)>{threshold:.1%}": median_above,
        f"中央値(p50)>{threshold:.1%} かつ P(上昇)>=60%": median_and_probability,
        "下位10%(p10)>0": lower_positive,
        f"上位10%(p90)>{threshold:.1%}": upper_above,
    }


def _score(
    arm: str, label: str, rule: str, taken: Sequence[dict[str, Any]]
) -> RuleResult:
    settled = [row for row in taken if row.get("actual_return") is not None]
    if not settled:
        return RuleResult(arm, label, rule, 0, 0, 0, None, 0.0, 0.0, None)
    returns = [float(row["actual_return"]) for row in settled]
    wins = sum(1 for value in returns if value > 0)
    # Per-session mean, then summed: buying five names on one day is one day's
    # exposure, not five. Summing every position would let a rule that piles
    # into a single session look like a diversified record.
    by_day: dict[str, list[float]] = {}
    for row, value in zip(settled, returns, strict=True):
        by_day.setdefault(str(row["date"]), []).append(value)
    directional = [
        row
        for row in settled
        if (predicted := _quantile(row, "q0.5")) is not None
        and not math.isclose(predicted, 0.0)
    ]
    # Parenthesised deliberately: ``a > 0.0 == b`` is a chained comparison in
    # Python and silently means ``(a > 0.0) and (0.0 == b)``, so it counted
    # almost nothing instead of failing loudly.
    hits = sum(
        1
        for row in directional
        if ((_quantile(row, "q0.5") or 0.0) > 0.0)
        == (float(row["actual_return"]) > 0.0)
    )
    return RuleResult(
        arm=arm,
        label=label,
        rule=rule,
        positions=len(settled),
        sessions=len(by_day),
        wins=wins,
        win_rate=wins / len(settled),
        mean_return=sum(returns) / len(returns),
        total_return=sum(sum(v) / len(v) for v in by_day.values()),
        direction_accuracy=(hits / len(directional)) if directional else None,
    )


def evaluate(payload: dict[str, Any], threshold: float) -> list[RuleResult]:
    rows = [row for row in payload["rows"] if row.get("status") == "OK"]
    by_arm: dict[str, list[dict[str, Any]]] = {}
    labels: dict[str, str] = {}
    for row in rows:
        by_arm.setdefault(str(row["arm"]), []).append(row)
        labels[str(row["arm"])] = str(row.get("label") or row["arm"])
    results: list[RuleResult] = []
    for arm, arm_rows in by_arm.items():
        for rule, predicate in _rules(threshold).items():
            results.append(
                _score(arm, labels[arm], rule, [r for r in arm_rows if predicate(r)])
            )
    return results


def _coverage(payload: dict[str, Any]) -> list[tuple[str, str, int, float | None]]:
    """How often the outcome fell inside each family's 80% band.

    The measurement the width comparison could only guess at. A band that
    covers far less than 80% is overconfident, and every probability read off
    the same curve inherits that error.
    """

    by_arm: dict[str, list[dict[str, Any]]] = {}
    labels: dict[str, str] = {}
    for row in payload["rows"]:
        if row.get("status") != "OK" or row.get("actual_return") is None:
            continue
        by_arm.setdefault(str(row["arm"]), []).append(row)
        labels[str(row["arm"])] = str(row.get("label") or row["arm"])
    out = []
    for arm, rows in by_arm.items():
        inside, total = 0, 0
        for row in rows:
            low, high = _quantile(row, "q0.1"), _quantile(row, "q0.9")
            if low is None or high is None:
                continue
            total += 1
            if low <= float(row["actual_return"]) <= high:
                inside += 1
        out.append((arm, labels[arm], total, (inside / total) if total else None))
    return sorted(out, key=lambda item: -(item[3] or 0.0))


def render(payload: dict[str, Any], threshold: float) -> list[str]:
    results = evaluate(payload, threshold)
    lines = [
        f"【全手法バックテスト】{payload['from']} 〜 {payload['to']}",
        f"  {len(payload['sessions'])}営業日 x {len(payload['tickers'])}銘柄"
        f"  閾値 {threshold:.1%}  コスト0%（本番設定に合わせています）",
        "",
        "  これは当時走っていなかったモデルの再現です。この系の実績ではありません。",
        f"  {len(payload['sessions'])}営業日では、"
        "手法間の勝率の差は偶然と区別できません。",
        "",
        "【80%区間の被覆】名目80%に対して実際に何%入ったか",
        "",
        f"  {'手法':<24}{'標本':>7}{'実測被覆':>10}{'差':>9}",
        "  " + "-" * 50,
    ]
    for _, label, total, covered in _coverage(payload):
        if covered is None:
            continue
        lines.append(f"  {label:<24}{total:>7}{covered:>9.1%}{covered - 0.80:>+9.1%}")
    lines += [
        "",
        "  被覆が名目を大きく下回る手法は、区間も、そこから読んだ確率も過信です。",
        "",
        "【買いルール別の成績】",
    ]
    for rule in _rules(threshold):
        lines += [
            "",
            f"  ◆ {rule}",
            "",
            f"  {'手法':<24}{'建玉':>6}{'取引日':>7}{'勝率':>8}"
            f"{'平均':>9}{'累積':>10}{'方向的中':>9}",
            "  " + "-" * 74,
        ]
        chosen = [r for r in results if r.rule == rule]
        for result in sorted(chosen, key=lambda r: -r.total_return):
            if result.positions == 0:
                lines.append(
                    f"  {result.label:<24}{'0':>6}{'—':>7}{'—':>8}"
                    f"{'—':>9}{'—':>10}{'—':>9}"
                )
                continue
            lines.append(
                f"  {result.label:<24}{result.positions:>6}{result.sessions:>7}"
                f"{result.win_rate or 0:>8.1%}{result.mean_return:>9.3%}"
                f"{result.total_return:>10.2%}"
                f"{(result.direction_accuracy or 0):>9.1%}"
            )
    lines += [
        "",
        "  建玉が数件しかないルールの勝率は読まないでください。0/2 も 2/2 も、",
        "  この標本数では何も意味しません。",
    ]
    return lines


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("artifact", type=Path)
    parser.add_argument("--threshold", type=float, default=0.003)
    parser.add_argument("--out", type=Path, default=None)
    parser.add_argument("--json-out", type=Path, default=None)
    args = parser.parse_args(argv)

    payload = json.loads(args.artifact.read_text(encoding="utf-8"))
    text = "\n".join(render(payload, args.threshold))
    print(text)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(text + "\n", encoding="utf-8")
    if args.json_out:
        args.json_out.parent.mkdir(parents=True, exist_ok=True)
        args.json_out.write_text(
            json.dumps(
                {
                    "from": payload["from"],
                    "to": payload["to"],
                    "sessions": len(payload["sessions"]),
                    "tickers": len(payload["tickers"]),
                    "threshold": args.threshold,
                    "coverage": [
                        {"arm": a, "label": lb, "samples": n, "covered": c}
                        for a, lb, n, c in _coverage(payload)
                    ],
                    "rules": [
                        {
                            "arm": r.arm,
                            "label": r.label,
                            "rule": r.rule,
                            "positions": r.positions,
                            "sessions": r.sessions,
                            "wins": r.wins,
                            "win_rate": r.win_rate,
                            "mean_return": r.mean_return,
                            "total_return": r.total_return,
                            "direction_accuracy": r.direction_accuracy,
                        }
                        for r in evaluate(payload, args.threshold)
                    ],
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
