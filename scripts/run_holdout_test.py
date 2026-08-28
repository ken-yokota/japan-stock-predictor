#!/usr/bin/env python3
"""Run the pre-registered hold-out test, exactly as registered.

The criterion lives here rather than in a shell one-liner so it cannot drift
between runs, and so the version that produced a result is the version in the
commit history. It is stated in `docs/PREREGISTRATION_2026-08-29_HOLDOUT.md`,
committed before the first run:

    The rule passes only if its cumulative net return exceeds the 95th
    percentile of a matched random control, on all three candidate grids.
    Close is a fail.

All three grids are always printed. Reporting only the one that passed is the
failure mode the registration exists to prevent, so the code does not offer a
way to select one.

Usage:
    python -m scripts.run_holdout_test artifacts/oos/<arm>.json
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from collections.abc import Sequence
from pathlib import Path

import numpy as np

from research.evaluation import evaluate, from_research_rows, session_selection
from research.universe import (
    AdaptiveReturnThreshold,
    all_tickers,
    backtest,
    backtest_adaptive_return,
    buy_production,
    random_filter_control,
    round_trip_cost,
)

# Registered before the first run. Not to be edited to fit a result.
GRIDS: dict[str, tuple[float, ...]] = {
    "A 後から選んだ4候補 (0.3/0.5/0.8/1.0)": (0.003, 0.005, 0.008, 0.010),
    "B 0.1%刻み 0.1〜2.0% の20候補": tuple(
        round(0.001 * step, 4) for step in range(1, 21)
    ),
    "C 粗い5候補 (0.0/0.5/1.0/1.5/2.0)": (0.0, 0.005, 0.010, 0.015, 0.020),
}


def _lines(path: Path) -> list[str]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    predictions = from_research_rows(payload.get("predictions", []))
    cost = round_trip_cost()
    sessions = session_selection(predictions)
    days = len({row.date for row in predictions})

    out = [
        "事前登録した判定: 候補グリッド3種すべてで無作為対照の95%点を超えれば PASS",
        f"対象: {path.name}",
        f"  {payload.get('feature_set')} / {payload.get('from_date')}"
        f" 〜 {payload.get('to_date')} / {len(predictions)}予測 / {days}営業日",
        f"  往復コスト {cost * 100:.3f}%（config のまま）",
        "",
        "【対照】",
    ]
    universe_free = float(np.sum([item.universe_mean for item in sessions]))
    everything = backtest(
        predictions, name="all", universe=all_tickers, buy=lambda _row: True
    )
    current = backtest(
        predictions, name="cur", universe=all_tickers, buy=buy_production()
    )
    out += [
        f"  全22銘柄を毎日買う（コスト0）        {universe_free * 100:>+8.2f}%",
        "  全22銘柄を毎日買う（コスト込み）      "
        f"{everything.total_return * 100:>+8.2f}%  {everything.positions}建玉",
        f"  現行の本番ルール                     {current.total_return * 100:>+8.2f}%"
        f"  {current.positions}建玉  t={current.daily_t or 0:+.2f}",
        "",
        "【主要主張】",
        f"  {'グリッド':<36}{'建玉':>6}{'累積':>10}{'t値':>7}"
        f"{'無作為中央':>12}{'無作為95%':>11}{'判定':>7}",
        "  " + "-" * 82,
    ]

    passes = 0
    for name, candidates in GRIDS.items():
        result, chosen = backtest_adaptive_return(
            predictions,
            name=name,
            adaptive=AdaptiveReturnThreshold(candidates=candidates),
        )
        _, mid, high = random_filter_control(predictions, keep=result.positions)
        passed = result.total_return > high
        passes += passed
        out.append(
            f"  {name:<36}{result.positions:>6}{result.total_return * 100:>+10.2f}"
            f"{result.daily_t or 0:>+7.2f}{mid * 100:>+12.2f}{high * 100:>+11.2f}"
            f"{('PASS' if passed else 'FAIL'):>7}"
        )
        top = Counter(chosen).most_common(3)
        out.append(
            "      選ばれた閾値: "
            + "、".join(f"{value * 100:.1f}% {count}日" for value, count in top)
            + f" / 最大DD {result.max_drawdown * 100:+.2f}%"
            f" / 取引日 {result.traded_sessions}"
        )
    out += [
        "",
        f"  結果: 3グリッド中 {passes} が PASS"
        + ("（判定: PASS）" if passes == 3 else "（判定: FAIL）"),
    ]

    quality = evaluate(predictions)
    model, pick = quality.model, quality.selection
    out += [
        "",
        "【記録する指標（判定には使わない）】",
        f"  方向的中 {model.direction_accuracy:.2%}  MAE {model.mae * 100:.4f}%"
        f"  Pearson {model.pearson or 0:+.4f}  Spearman {model.spearman or 0:+.4f}"
        f"  較正傾き {model.calibration_slope or 0:.3f}",
        f"  順位IC {pick.rank_ic_mean or 0:+.4f} (t={pick.rank_ic_t or 0:+.2f})"
        f"  Top5超過 {(pick.top5_alpha or 0) * 100:+.4f}%"
        f" (t={pick.top5_alpha_t or 0:+.2f})"
        f"  上下差 {(pick.top_bottom_spread or 0) * 100:+.4f}%",
        f"  Brier {quality.probability.brier or 0:.4f}"
        f"  実際の上昇率 {quality.probability.base_rate:.2%}",
    ]
    return out


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("artifact", type=Path)
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args(argv)

    text = "\n".join(_lines(args.artifact))
    print(text)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(text + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
