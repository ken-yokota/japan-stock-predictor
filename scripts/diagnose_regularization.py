#!/usr/bin/env python3
"""How hard is cross-validation pushing these features away?

Production chose ridge alpha 100.0 in 264 of 264 fits and the research arms
chose it in 5,500 of 5,500 -- always the largest value the grid offers. A value
pinned at the edge of a grid is not a measurement, it is the grid running out,
so this widens the grid until it stops being the edge and reports what CV picks
when it can pick anything.

This is a diagnostic and not a proposal. Turning the shrinkage up until the
model predicts nothing would score well on error and be worthless; the number
worth having is how far past 100 the search wants to go, because that says how
strongly the evidence in the training window rejects these columns.

Sampled rather than exhaustive: one fit per (ticker, session) pair is the same
work the walk-forward does, and the answer does not need 5,500 of them.

    python -m scripts.diagnose_regularization --sessions 12 --feature-set production
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from collections.abc import Sequence
from datetime import date
from pathlib import Path

import numpy as np

from data.config import load_app_config
from models.base import InsufficientTrainingData, ModelTrainingConfig
from models.training import train_ticker_model
from research import feature_sets
from research.dataset import build_indicator_frame, build_stock_frame
from research.feature_selection import select_top_k
from research.walk import default_history_start

# Four decades of shrinkage past the production ceiling. If CV still wants the
# largest value here, the grid is not what is limiting it.
WIDE_ALPHAS: tuple[float, ...] = (
    0.001, 0.01, 0.1, 1.0, 10.0, 100.0, 1_000.0, 10_000.0, 100_000.0,
)
PRODUCTION_ALPHAS: tuple[float, ...] = (0.01, 0.1, 1.0, 10.0, 100.0)
WIDE_CS: tuple[float, ...] = (0.0001, 0.001, 0.01, 0.1, 1.0, 10.0, 100.0)


def _sessions(frame: object, count: int, end: date) -> list[date]:
    import pandas as pd

    assert isinstance(frame, pd.DataFrame)
    days = [
        value
        for value in frame["market_date"].tolist()
        if isinstance(value, date) and value <= end
    ]
    return days[-count:] if len(days) >= count else days


def diagnose(
    *,
    feature_set_name: str,
    top_k: int | None,
    sessions: int,
    end: date,
    window: int,
) -> dict[str, object]:
    config = load_app_config()
    feature_set = feature_sets.resolve(feature_set_name)
    history_start = default_history_start(end, window)
    indicators = build_indicator_frame(feature_set.indicators, history_start, end)
    selector = select_top_k(top_k) if top_k else None

    narrow: Counter[float] = Counter()
    wide: Counter[float] = Counter()
    wide_c: Counter[float] = Counter()
    columns: list[int] = []
    fits = 0

    for stock in config.stocks.stocks:
        symbol = stock.provider_symbols.get("yahoo_finance")
        if symbol is None:
            continue
        built = build_stock_frame(
            stock.ticker,
            symbol,
            history_start,
            end,
            feature_set=feature_set,
            indicators=indicators,
        )
        if built.is_empty:
            continue
        frame = built.frame
        for target_date in _sessions(frame, sessions, end):
            history = frame.loc[frame["market_date"] < target_date]
            usable = history.loc[history["intraday_return"].notna()]
            if len(usable) < window:
                continue
            names = built.feature_names
            if selector is not None:
                tail = usable.tail(window)
                chosen = selector(
                    tail.loc[:, list(names)],
                    tail["intraday_return"].to_numpy(dtype=float),
                )
                if chosen:
                    names = tuple(chosen)
            columns.append(len(names))
            settings = (
                (PRODUCTION_ALPHAS, narrow, ModelTrainingConfig().logistic_cs, False),
                (WIDE_ALPHAS, wide, WIDE_CS, True),
            )
            for grid, sink, cs, record_c in settings:
                try:
                    bundle = train_ticker_model(
                        stock.ticker,
                        usable.loc[:, list(names)],
                        usable["intraday_return"],
                        feature_names=names,
                        config=ModelTrainingConfig(
                            window_size=window,
                            ridge_alphas=grid,
                            logistic_cs=cs,
                        ),
                    )
                except (InsufficientTrainingData, ValueError):
                    continue
                sink[float(bundle.ridge_alpha)] += 1
                if record_c and bundle.logistic_c is not None:
                    wide_c[float(bundle.logistic_c)] += 1
            fits += 1

    return {
        "feature_set": feature_set_name,
        "top_k": top_k,
        "sessions_per_ticker": sessions,
        "training_window": window,
        "fits": fits,
        "median_features": int(np.median(columns)) if columns else 0,
        "feature_row_ratio": (
            round(float(np.median(columns)) / window, 3) if columns else 0.0
        ),
        "production_grid": {str(k): v for k, v in sorted(narrow.items())},
        "wide_grid": {str(k): v for k, v in sorted(wide.items())},
        "wide_logistic_c": {str(k): v for k, v in sorted(wide_c.items())},
        "pinned_at_production_ceiling": (
            narrow[max(PRODUCTION_ALPHAS)] / sum(narrow.values()) if narrow else 0.0
        ),
        "beyond_production_ceiling": (
            sum(
                count
                for value, count in wide.items()
                if value > max(PRODUCTION_ALPHAS)
            )
            / sum(wide.values())
            if wide
            else 0.0
        ),
    }


def _counts(result: dict[str, object], key: str) -> list[tuple[float, int]]:
    raw = result.get(key)
    if not isinstance(raw, dict):
        return []
    return sorted((float(k), int(v)) for k, v in raw.items())


def render(result: dict[str, object]) -> str:
    suffix = f" k={result['top_k']}" if result["top_k"] else ""
    lines = [
        f"=== 正則化の探索範囲診断: {result['feature_set']}{suffix} ===",
        f"  fit数 {result['fits']} / 特徴量(中央値) {result['median_features']}"
        f" / 特徴量÷学習行 {result['feature_row_ratio']}",
        "",
        "  本番グリッド (0.01〜100) で選ばれた alpha:",
    ]
    for value, count in _counts(result, "production_grid"):
        lines.append(f"    {value:>10g}: {count}")
    pinned = float(str(result["pinned_at_production_ceiling"]))
    lines.append(f"  上限100に張り付いた割合: {pinned:.1%}")
    lines.append("")
    lines.append("  拡張グリッド (0.001〜100000) で選ばれた alpha:")
    for value, count in _counts(result, "wide_grid"):
        lines.append(f"    {value:>10g}: {count}")
    beyond = float(str(result["beyond_production_ceiling"]))
    lines.append(f"  100より強い収縮が選ばれた割合: {beyond:.1%}")
    lines.append("")
    lines.append("  拡張グリッドで選ばれた Logistic C:")
    for value, count in _counts(result, "wide_logistic_c"):
        lines.append(f"    {value:>10g}: {count}")
    lines.append("")
    lines.append(
        "  これは診断です。収縮を強くすれば良くなるという意味ではありません。"
        "CVが今の特徴量をどれだけ強く否定しているかを見ています。"
    )
    return "\n".join(lines)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--feature-set", default="production")
    parser.add_argument("--top-k", type=int, default=None)
    parser.add_argument("--sessions", type=int, default=12)
    parser.add_argument("--window", type=int, default=120)
    parser.add_argument(
        "--end", type=date.fromisoformat, default=date(2026, 8, 14)
    )
    parser.add_argument("--output", type=Path, default=None)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    result = diagnose(
        feature_set_name=args.feature_set,
        top_k=args.top_k,
        sessions=args.sessions,
        end=args.end,
        window=args.window,
    )
    if args.output:
        args.output.write_text(
            json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    print(render(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
