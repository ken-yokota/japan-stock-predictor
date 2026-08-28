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
    BUY_RULE_POOLS,
    BUY_RULES,
    UNIVERSE_RULES,
    AdaptiveBuy,
    AdaptiveReturnThreshold,
    BacktestResult,
    all_tickers,
    backtest,
    backtest_adaptive,
    backtest_adaptive_return,
    buy_production,
    random_filter_control,
    round_trip_cost,
)


# Japanese characters occupy two columns in a monospace mail, which is where
# these tables are read. Padding by character count leaves every header adrift
# from its column, so width is counted the way the terminal counts it.
def _width(text: str) -> int:
    from unicodedata import east_asian_width

    return sum(2 if east_asian_width(ch) in "WF" else 1 for ch in text)


def _pad(text: str, width: int, *, right: bool = False) -> str:
    fill = " " * max(0, width - _width(text))
    return fill + text if right else text + fill


def _row(result: BacktestResult) -> str:
    return (
        "  "
        + _pad(result.name, 34)
        + _pad(f"{result.positions}", 7, right=True)
        + _pad(f"{result.traded_sessions}", 8, right=True)
        + _pad(f"{(result.mean_daily or 0.0) * 100:+.4f}%", 11, right=True)
        + _pad(f"{result.daily_t or 0.0:+.2f}", 8, right=True)
        + _pad(f"{result.total_return * 100:+.2f}%", 10, right=True)
        + _pad(f"{result.max_drawdown * 100:+.2f}%", 10, right=True)
    )


def _header(title: str) -> list[str]:
    return [
        "",
        title,
        "  "
        + _pad("", 34)
        + _pad("建玉", 7, right=True)
        + _pad("取引日", 8, right=True)
        + _pad("日次平均", 11, right=True)
        + _pad("t値", 8, right=True)
        + _pad("累積", 10, right=True)
        + _pad("最大DD", 10, right=True),
        "  " + "-" * 88,
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

    # Same critique, same control. B and D keep 41 and 43 of the control's 325
    # positions and come out positive; a coin keeping 41 of 325 has to be beaten
    # before that means the universe rule picked well.
    control = backtest(
        predictions, name="全22", universe=all_tickers, buy=buy_production()
    )
    taken = [row for row in predictions if buy_production()(row)]
    lines += [
        "",
        "【無作為フィルタとの比較】全22銘柄の建玉から同数を無作為に残した場合",
        "  " + _pad("", 34) + _pad("建玉", 7, right=True)
        + _pad("実績", 11, right=True) + _pad("無作為5%", 12, right=True)
        + _pad("無作為中央", 13, right=True) + _pad("無作為95%", 12, right=True),
        "  " + "-" * 88,
    ]
    for name, rule in UNIVERSE_RULES.items():
        result = backtest(predictions, name=name, universe=rule, buy=buy_production())
        if result.positions >= control.positions:
            cells = _pad("（対照そのもの）", 37, right=True)
        else:
            low, mid, high = random_filter_control(taken, keep=result.positions)
            cells = (
                _pad(f"{low * 100:+.2f}%", 12, right=True)
                + _pad(f"{mid * 100:+.2f}%", 13, right=True)
                + _pad(f"{high * 100:+.2f}%", 12, right=True)
            )
        lines.append(
            "  "
            + _pad(name, 34)
            + _pad(f"{result.positions}", 7, right=True)
            + _pad(f"{result.total_return * 100:+.2f}%", 11, right=True)
            + cells
        )
    lines.append(
        "  銘柄を絞ると建玉が減り、それだけで累積は改善します。"
        "無作為の95%点を超えて初めて、選び方に意味があったと言えます。"
    )

    lines += _header("【BUY条件】銘柄は全22で固定")
    for name, rule in BUY_RULES.items():
        lines.append(
            _row(backtest(predictions, name=name, universe=all_tickers, buy=rule))
        )

    # The control every rule above needs. With a 0.20% round trip, any rule
    # that trades less improves the record on its own, so "beats trading
    # everything" is not evidence. A coin discarding the same number of
    # positions is.
    lines += [
        "",
        "【無作為フィルタとの比較】同じ建玉数だけ無作為に残したら累積は何%になるか",
        "  " + _pad("", 34) + _pad("建玉", 7, right=True)
        + _pad("実績", 11, right=True) + _pad("無作為5%", 12, right=True)
        + _pad("無作為中央", 13, right=True) + _pad("無作為95%", 12, right=True),
        "  " + "-" * 88,
    ]
    for name, rule in BUY_RULES.items():
        result = backtest(predictions, name=name, universe=all_tickers, buy=rule)
        pool_rule = BUY_RULE_POOLS.get(name)
        if pool_rule is None:
            # No probability filter, so there is nothing for a coin to replace.
            cells = _pad("（確率を使わないルール）", 37, right=True)
        else:
            pool = [row for row in predictions if pool_rule(row)]
            low, mid, high = random_filter_control(pool, keep=result.positions)
            cells = (
                _pad(f"{low * 100:+.2f}%", 12, right=True)
                + _pad(f"{mid * 100:+.2f}%", 13, right=True)
                + _pad(f"{high * 100:+.2f}%", 12, right=True)
            )
        lines.append(
            "  "
            + _pad(name, 34)
            + _pad(f"{result.positions}", 7, right=True)
            + _pad(f"{result.total_return * 100:+.2f}%", 11, right=True)
            + cells
        )
    lines.append(
        "  実績が無作為の95%点を超えていなければ、"
        "そのルールは「取引を減らした」以上のことをしていません。"
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

    lines += _header("【予測閾値の選び方】確率フィルタを外し、閾値だけを毎日選び直す")
    grids = {
        "後から選んだ4候補 (0.3/0.5/0.8/1.0)": (0.003, 0.005, 0.008, 0.010),
        "0.1%刻み 0.1〜2.0% の20候補": tuple(
            round(0.001 * step, 4) for step in range(1, 21)
        ),
        "粗い5候補 (0.0/0.5/1.0/1.5/2.0)": (0.0, 0.005, 0.010, 0.015, 0.020),
    }
    grid_results = []
    for label, candidates in grids.items():
        result, _ = backtest_adaptive_return(
            predictions,
            name=label,
            adaptive=AdaptiveReturnThreshold(candidates=candidates),
        )
        grid_results.append(result)
        lines.append(_row(result))
    lines += [
        "",
        "  候補の並び自体を後から選べば、それも後知恵です。"
        "上の3行は同じ手順で候補だけを変えたもので、",
        "  結果が大きく動きます。動かないのは無作為対照との差の方です:",
        "",
    ]
    for result in grid_results:
        low, mid, high = random_filter_control(predictions, keep=result.positions)
        verdict = "有意" if result.total_return > high else "帯の中"
        lines.append(
            "  "
            + _pad(result.name, 40)
            + _pad(f"{result.total_return * 100:+.2f}%", 10, right=True)
            + _pad(f"無作為95% {high * 100:+.2f}%", 20, right=True)
            + _pad(verdict, 8, right=True)
        )

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
