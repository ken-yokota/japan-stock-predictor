#!/usr/bin/env python3
"""Indicators, transformations, and which of the two made the column count.

"Too many features" is not an actionable finding: 20 indicators crossed with 10
transformations is 200 columns, and the fix is completely different depending on
which of the two numbers is the large one. This separates them, per ticker, for
both the production pipeline and the research mirror.

It reads the feature *names* the fits actually recorded rather than the config,
because the config says what was requested and the coefficients say what
arrived.

    python -m scripts.analyze_feature_composition --production --research ARTIFACT
"""

from __future__ import annotations

import argparse
import json
import os
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

# Pairs that carry the same information twice. Named rather than detected so the
# claim is arguable without a measurement: log(1+x) and x agree to within a
# fraction of a basis point over a daily move, and a 2-day return is two thirds
# of a 3-day return by construction.
KNOWN_REDUNDANCIES: tuple[tuple[str, str, str], ...] = (
    ("return_1d", "log_return_1d", "日次では log(1+x) ≈ x で、ほぼ同一"),
    ("return_2d", "return_3d", "重なる期間が2/3。独立な情報はごく一部"),
    ("return_3d", "return_5d", "重なる期間が3/5"),
    ("volatility_5d", "volatility_20d", "短い方が長い方に含まれる"),
)


@dataclass(frozen=True, slots=True)
class Composition:
    """One ticker's columns, split into what they came from and what was done."""

    label: str
    ticker: str
    series: dict[str, list[str]]

    @property
    def indicators(self) -> int:
        return len(self.series)

    @property
    def features(self) -> int:
        return sum(len(v) for v in self.series.values())

    @property
    def per_indicator(self) -> float:
        return self.features / self.indicators if self.indicators else 0.0

    @property
    def transformations(self) -> dict[str, int]:
        counts: dict[str, int] = defaultdict(int)
        for columns in self.series.values():
            for column in columns:
                counts[column] += 1
        return dict(counts)


def _split(name: str) -> tuple[str, str]:
    """Split a feature name into its source series and its transformation."""

    if "__" in name:
        base, transformation = name.split("__", 1)
        return base, transformation
    for separator in ("_return_", "_log_return_", "_volatility_", "_ma", "_level"):
        if separator in name:
            index = name.index(separator)
            return name[:index], name[index + 1 :]
    return "自銘柄の価格・出来高", name


def from_production(ticker: str) -> Composition | None:
    from sqlalchemy import text

    from database.connection import create_database_engine

    url = os.environ.get("DATABASE_URL", "").strip()
    if not url:
        return None
    engine = create_database_engine(url)
    try:
        with engine.connect() as connection:
            names = [
                row[0]
                for row in connection.execute(
                    text(
                        """
                        SELECT DISTINCT mc.feature_name
                        FROM model_coefficients AS mc
                        JOIN model_runs AS mr
                          ON mr.model_run_id = mc.model_run_id
                        WHERE mr.ticker = :ticker AND mr.task = 'REGRESSION'
                          AND mr.model_run_id = (
                              SELECT model_run_id FROM model_runs
                              WHERE ticker = :ticker AND task = 'REGRESSION'
                              ORDER BY started_at DESC LIMIT 1
                          )
                        """
                    ),
                    {"ticker": ticker},
                )
            ]
    finally:
        engine.dispose()
    if not names:
        return None
    series: dict[str, list[str]] = defaultdict(list)
    for name in names:
        base, transformation = _split(name)
        series[base].append(transformation)
    return Composition("本番", ticker, dict(series))


def from_artifact(path: Path, ticker: str) -> Composition | None:
    payload = json.loads(path.read_text(encoding="utf-8"))
    names = sorted(
        {
            str(item["feature"])
            for item in payload.get("coefficients", [])
            if str(item.get("ticker")) == ticker
        }
    )
    if not names:
        return None
    series: dict[str, list[str]] = defaultdict(list)
    for name in names:
        base, transformation = _split(name)
        series[base].append(transformation)
    label = str(payload.get("feature_set", path.stem))
    return Composition(f"研究 {label}", ticker, dict(series))


def render(compositions: Sequence[Composition]) -> str:
    lines = ["=== Indicator数 / Feature数 / 1系列あたりの変換数 ==="]
    lines.append(f"  {'':<20}{'Indicator':>11}{'Feature':>10}{'1系列あたり':>13}")
    for item in compositions:
        lines.append(
            f"  {item.label:<20}{item.indicators:>11}{item.features:>10}"
            f"{item.per_indicator:>12.1f}本"
        )
    for item in compositions:
        lines.append("")
        lines.append(f"  --- {item.label} ({item.ticker}) の変換 ---")
        for name, count in sorted(
            item.transformations.items(), key=lambda kv: (-kv[1], kv[0])
        ):
            lines.append(f"    {name:<28} {count}系列に適用")
    biggest = max(compositions, key=lambda c: c.per_indicator, default=None)
    if biggest is not None:
        present = set(biggest.transformations)
        overlaps = [
            (a, b, why)
            for a, b, why in KNOWN_REDUNDANCIES
            if a in present and b in present
        ]
        lines.append("")
        lines.append(f"  --- {biggest.label} で重複している変換の組 ---")
        if not overlaps:
            lines.append("    既知の重複はありません。")
        for a, b, why in overlaps:
            lines.append(f"    {a} と {b}: {why}")
        lines.append("")
        lines.append(
            "  Indicatorを減らす話とTransformationを減らす話は別です。"
            "1系列あたりの本数が大きいなら、削るべきは変換の側です。"
        )
    return "\n".join(lines)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ticker", default="7203")
    parser.add_argument("--production", action="store_true")
    parser.add_argument("--research", type=Path, action="append", default=None)
    parser.add_argument("--output", type=Path, default=None)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    compositions: list[Composition] = []
    if args.production:
        found = from_production(args.ticker)
        if found is None:
            print("本番の係数を読めませんでした（DATABASE_URL 未設定か記録なし）。")
        else:
            compositions.append(found)
    for path in args.research or []:
        found = from_artifact(path, args.ticker)
        if found is not None:
            compositions.append(found)
    if not compositions:
        print("比較できる構成がありません。")
        return 1
    report = render(compositions)
    if args.output:
        args.output.write_text(report, encoding="utf-8")
    print(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
