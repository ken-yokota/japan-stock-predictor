#!/usr/bin/env python3
"""Is the model better than answering nothing?

A 72-column model that cannot beat "predict zero" or "predict this ticker's
recent average" has no claim on the complexity it carries. These benchmarks are
the floor every arm has to clear before its metrics are worth reading, and they
were missing -- the arms had only been compared with each other, which cannot
tell you whether any of them is doing anything.

Each benchmark is built from the same rows the model was scored on, using only
sessions strictly before the one being predicted, so the comparison is like for
like and the benchmark cannot see further ahead than the model could.

    A  予測ゼロ
    B  直近120営業日の平均（銘柄別・ローリング）
    C  それまでの全履歴の平均（銘柄別・拡大窓）
    D  少数特徴量のRidge   ← 別アームとして走らせたものを --artifact で渡す

    python -m scripts.benchmark_null_models artifacts/oos/production_*.json
"""

from __future__ import annotations

import argparse
import json
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from research.evaluation import (
    Prediction,
    evaluate,
    from_research_rows,
)

# A trailing mean over fewer sessions than this is noise wearing the name of an
# average, so those rows are dropped from every arm alike rather than filled in.
MINIMUM_PRIOR_SESSIONS = 20

ROLLING_WINDOW = 120


@dataclass(frozen=True, slots=True)
class Benchmark:
    name: str
    description: str
    rows: list[Prediction]


def _by_ticker(rows: Sequence[Prediction]) -> dict[str, list[Prediction]]:
    grouped: dict[str, list[Prediction]] = {}
    for row in rows:
        grouped.setdefault(row.ticker, []).append(row)
    for values in grouped.values():
        values.sort(key=lambda r: r.date)
    return grouped


def _replace(row: Prediction, predicted: float) -> Prediction:
    """Same session, same outcome, a different forecast. Nothing else moves."""

    return Prediction(
        date=row.date,
        ticker=row.ticker,
        predicted_return=predicted,
        actual_return=row.actual_return,
        probability_up=None,
        signal="NO_BUY",
        sector=row.sector,
    )


def build_benchmarks(rows: Sequence[Prediction]) -> list[Benchmark]:
    """A, B and C, each on the rows where its own history is long enough."""

    grouped = _by_ticker(rows)
    zero: list[Prediction] = []
    rolling: list[Prediction] = []
    expanding: list[Prediction] = []
    for values in grouped.values():
        history: list[float] = []
        for row in values:
            if len(history) >= MINIMUM_PRIOR_SESSIONS:
                zero.append(_replace(row, 0.0))
                window = history[-ROLLING_WINDOW:]
                rolling.append(_replace(row, float(np.mean(window))))
                expanding.append(_replace(row, float(np.mean(history))))
            history.append(row.actual_return)
    return [
        Benchmark("A 予測ゼロ", "常に 0 と答える", zero),
        Benchmark(
            f"B 直近{ROLLING_WINDOW}日平均",
            "銘柄ごとに直近の平均を答える（ローリング）",
            rolling,
        ),
        Benchmark(
            "C 過去全体の平均",
            "銘柄ごとにそれまでの全平均を答える（拡大窓）",
            expanding,
        ),
    ]


def restrict(
    rows: Sequence[Prediction], keys: set[tuple[str, str]]
) -> list[Prediction]:
    """Keep only the sessions every arm has, so the comparison is like for like."""

    return [row for row in rows if (row.date, row.ticker) in keys]


def _num(value: float | None, digits: int = 4) -> str:
    return "—" if value is None else f"{value:+.{digits}f}"


def render(named: Sequence[tuple[str, list[Prediction]]]) -> str:
    shared: set[tuple[str, str]] | None = None
    for _, rows in named:
        keys = {(r.date, r.ticker) for r in rows}
        shared = keys if shared is None else (shared & keys)
    shared = shared or set()

    lines = [
        "=== モデル vs ベンチマーク（同一の予測対象に揃えて比較）===",
        f"  共通の予測対象 {len(shared):,} 件",
        "",
        f"  {'':<24}{'MAE':>10}{'RMSE':>10}{'Pearson':>10}"
        f"{'Spearman':>10}{'方向的中':>10}{'RankIC':>10}{'Top5-Univ':>11}",
    ]
    for name, rows in named:
        result = evaluate(restrict(rows, shared), label=name)
        model, selection = result.model, result.selection
        lines.append(
            f"  {name:<24}{model.mae:>9.4%}{model.rmse:>10.4%}"
            f"{_num(model.pearson):>10}{_num(model.spearman):>10}"
            f"{model.direction_accuracy:>9.2%}"
            f"{_num(selection.rank_ic_mean):>10}"
            f"{(selection.top5_alpha or 0):>10.3%}"
        )
    lines.append("")
    lines.append(
        "  ベンチマークA〜Cは順位を持たない（全銘柄同値、または銘柄ごとに固定）ため、"
        "RankICとTop5-Universeは意味を持ちません。"
        "そこはモデル同士でのみ比較してください。"
    )
    return "\n".join(lines)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("artifacts", nargs="+", type=Path)
    parser.add_argument("--output", type=Path, default=None)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    named: list[tuple[str, list[Prediction]]] = []
    reference: list[Prediction] = []
    for path in args.artifacts:
        payload = json.loads(path.read_text(encoding="utf-8"))
        rows = from_research_rows(payload.get("predictions", []))
        label = str(payload.get("feature_set", path.stem))
        if payload.get("top_k"):
            label = f"{label} k={payload['top_k']}"
        named.append((label, rows))
        if not reference:
            reference = rows
    for benchmark in build_benchmarks(reference):
        named.append((benchmark.name, benchmark.rows))
    report = render(named)
    if args.output:
        args.output.write_text(report, encoding="utf-8")
    print(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
