#!/usr/bin/env python3
"""Walk the universe rules, the buy rules, and the threshold choice itself.

Three questions, each answered against a control that must be beaten:

    which tickers    universe rules A-E, buy rule held at what production runs
    when to buy      buy rules A-G, universe held at all twenty-two
    which threshold  chosen with hindsight, versus chosen each session

The third is the one worth the run. Sweeping a probability threshold over the
whole period and reporting the best one will always produce a winner from four
candidates on 250 sessions, and it will not survive the next 250. Choosing from
history alone at each session measures the same idea without the hindsight, and
the gap between the two is what the sweep was worth.

Usage:
    python -m scripts.run_universe_study artifacts/oos/<arm>.json
"""

from __future__ import annotations

import argparse
import json
from collections.abc import Sequence
from pathlib import Path

from research.evaluation import Prediction, from_research_rows
from research.universe import (
    BUY_RULES,
    UNIVERSE_RULES,
    AdaptiveBuy,
    BacktestResult,
    all_tickers,
    backtest,
    backtest_adaptive,
    buy_production,
    round_trip_cost,
)


def _row(result: BacktestResult) -> str:
    return (
        f"  {result.name:<34}"
        f"{result.positions:>6}建玉"
        f"{result.traded_sessions:>6}日"
        f"{(result.mean_daily or 0.0) * 100:>+9.4f}%"
        f"{result.daily_t or 0.0:>+8.2f}"
        f"{result.total_return * 100:>+9.2f}%"
        f"{result.max_drawdown * 100:>+9.2f}%"
    )


def _header(title: str) -> list[str]:
    return [
        "",
        title,
        f"  {'':<34}{'建玉':>8}{'取引日':>7}{'日次平均':>9}"
        f"{'t値':>8}{'累積':>10}{'最大DD':>10}",
        "  " + "-" * 76,
    ]


def study(predictions: Sequence[Prediction]) -> list[str]:
    cost = round_trip_cost()
    lines = [
        f"往復コスト {cost * 100:.3f}%（config の commission + slippage から算出）",
        f"予測 {len(predictions)}件 / {len({p.date for p in predictions})}営業日",
    ]

    lines += _header("【銘柄選択】BUY条件は現行で固定")
    for name, rule in UNIVERSE_RULES.items():
        lines.append(
            _row(backtest(predictions, name=name, universe=rule, buy=buy_production()))
        )

    lines += _header("【BUY条件】銘柄は全22で固定")
    for name, rule in BUY_RULES.items():
        lines.append(
            _row(backtest(predictions, name=name, universe=all_tickers, buy=rule))
        )

    lines += _header("【閾値の選び方】同じ候補、選ぶ時点だけが違う")
    best = max(
        (
            backtest(
                predictions,
                name=f"【禁止】全期間を見て最良({candidate:.2f})",
                universe=all_tickers,
                buy=buy_production(probability=candidate),
            )
            for candidate in AdaptiveBuy().candidates
        ),
        key=lambda item: item.total_return,
    )
    adaptive, chosen = backtest_adaptive(
        predictions,
        name="過去だけ見て毎日選び直す",
        universe=all_tickers,
        adaptive=AdaptiveBuy(),
    )
    current = backtest(
        predictions,
        name="現行の固定0.60",
        universe=all_tickers,
        buy=buy_production(),
    )
    for result in (best, adaptive, current):
        lines.append(_row(result))

    counts: dict[float, int] = {}
    for value in chosen:
        counts[value] = counts.get(value, 0) + 1
    lines += [
        "",
        "  選ばれた閾値: "
        + "、".join(f"{k:.2f} が {v}日" for k, v in sorted(counts.items())),
        f"  後知恵の上乗せ: {(best.total_return - adaptive.total_return) * 100:+.2f}"
        "ポイント（これは実力ではありません）",
    ]
    return lines


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("artifact", type=Path)
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args(argv)

    payload = json.loads(args.artifact.read_text(encoding="utf-8"))
    predictions = from_research_rows(payload.get("predictions", []))
    lines = study(predictions)
    text = "\n".join(lines)
    print(text)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(text + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
