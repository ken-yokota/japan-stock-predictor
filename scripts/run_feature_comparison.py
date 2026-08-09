"""Compare predictor sets on one window, and say which one actually won.

The rule this answers: keep the current system, add candidate factors beside
it, and adopt them only if accuracy improves. So every set runs over the *same*
dates, the *same* tickers, the *same* model code, and the *same* trading rule.
The only thing that varies is which columns reach the fit.

## Which number decides

Direction accuracy and MAE, over every prediction. Not win rate, not profit
factor, not P&L: those are computed over a handful of BUY signals, and a
handful of trades cannot separate a better model from a luckier month. Both are
still reported, and both are still labelled as evidence of nothing.

Aggregate accuracy alone is also weak, because two sets can differ by a
percentage point through noise. The verdict therefore comes from a paired sign
test on the predictions the two sets disagree about: of the sessions where
exactly one of them got the direction right, how often was it the candidate?

    python -m cli compare-features --from-date 2026-06-01 --to-date 2026-08-07
"""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass, replace
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from data.config import load_app_config
from models.base import ModelTrainingConfig
from research import feature_sets
from research.history import DEFAULT_CACHE_DIR
from research.walk import (
    WindowResult,
    default_history_start,
    require_complete_data,
    run_window,
)
from trading.strategy import BuySignalConfig, ExecutionConfig

# Below this many BUY signals, trade statistics are reported but never used to
# choose. The same floor the test page enforces.
MINIMUM_TRADES_FOR_EVIDENCE = 20


def _require(value: object, name: str) -> float:
    if value is None:
        raise SystemExit(
            f"config/trading.yaml の {name} が未設定です。"
            "コスト前提を確定してから実行してください。"
        )
    return float(value)  # type: ignore[arg-type]


def _parse_arguments() -> argparse.Namespace:
    today = date.today()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config-dir", type=Path, default=Path("config"))
    parser.add_argument(
        "--from-date", type=date.fromisoformat, default=today - timedelta(days=68)
    )
    parser.add_argument(
        "--to-date", type=date.fromisoformat, default=today - timedelta(days=2)
    )
    parser.add_argument("--history-start", type=date.fromisoformat, default=None)
    parser.add_argument("--training-window", type=int, default=120)
    parser.add_argument(
        "--sets",
        default=",".join(sorted(feature_sets.FEATURE_SETS)),
        help="比較する feature set をカンマ区切りで。既定は全部。",
    )
    parser.add_argument(
        "--baseline",
        default=feature_sets.DEFAULT_FEATURE_SET,
        help="基準にする variant。他はこれと対で比較される。",
    )
    parser.add_argument(
        "--recency-half-lives",
        default="none",
        help=(
            "直近を重く見る度合いを営業日で。カンマ区切りで複数指定でき、"
            "none は全期間を等しく扱う現行の挙動。例: none,60"
        ),
    )
    parser.add_argument("--allow-missing-indicators", action="store_true")
    parser.add_argument("--no-cache", action="store_true")
    parser.add_argument(
        "--output", type=Path, default=Path("artifacts/feature_comparison/latest.json")
    )
    return parser.parse_args()


def _accuracy_metrics(frame: pd.DataFrame) -> dict[str, Any]:
    """Return the well-powered metrics, plus the under-powered ones labelled."""

    predicted = frame["predicted_return"].to_numpy(dtype=float)
    actual = frame["actual_return"].to_numpy(dtype=float)
    error = predicted - actual
    traded = frame.loc[frame["signal"] == "BUY"]
    wins = traded.loc[traded["net_profit_jpy"] > 0.0]
    return {
        "predictions": len(frame),
        "direction_accuracy": float(frame["direction_correct"].mean()),
        "mean_absolute_error": float(np.mean(np.abs(error))),
        "root_mean_squared_error": float(np.sqrt(np.mean(error**2))),
        "buy_signals": len(traded),
        "win_rate": (float(len(wins) / len(traded)) if len(traded) else None),
        "net_profit_jpy": float(traded["net_profit_jpy"].sum()),
        "trade_stats_are_evidence": bool(len(traded) >= MINIMUM_TRADES_FOR_EVIDENCE),
    }


def _sign_test(candidate: pd.Series, baseline: pd.Series) -> dict[str, Any]:
    """Two-sided exact sign test over the predictions the two sets disagree on.

    Sessions where both were right, or both were wrong, carry no information
    about which set is better and are excluded — that is what makes this test
    far more sensitive than comparing two aggregate accuracies.
    """

    candidate_only = int((candidate & ~baseline).sum())
    baseline_only = int((~candidate & baseline).sum())
    discordant = candidate_only + baseline_only
    p_value: float | None = None
    if discordant:
        from scipy.stats import binomtest  # type: ignore[import-untyped]

        p_value = float(
            binomtest(candidate_only, discordant, 0.5, alternative="two-sided").pvalue
        )
    return {
        "candidate_only_correct": candidate_only,
        "baseline_only_correct": baseline_only,
        "discordant_pairs": discordant,
        "p_value": p_value,
    }


def _verdict(delta_accuracy: float, p_value: float | None) -> str:
    """Translate the paired test into the adopt / do-not-adopt decision."""

    if p_value is None:
        return "判定不能: 差が出た予測が1件もありません。"
    if p_value >= 0.05:
        return (
            f"不採用: 方向的中率の差 {delta_accuracy * 100:+.2f}pp は "
            f"符号検定 p={p_value:.3f} で、偶然と区別できません。"
        )
    if delta_accuracy > 0.0:
        return (
            f"採用候補: 方向的中率 {delta_accuracy * 100:+.2f}pp、"
            f"符号検定 p={p_value:.3f}。別期間でも再現するか確認してください。"
        )
    return (
        f"不採用: 方向的中率が {delta_accuracy * 100:+.2f}pp 悪化 "
        f"(符号検定 p={p_value:.3f})。"
    )


@dataclass(frozen=True, slots=True)
class Variant:
    """One thing being compared: a feature set plus how it weights history."""

    feature_set: str
    half_life: int | None

    @property
    def key(self) -> str:
        return (
            self.feature_set
            if self.half_life is None
            else f"{self.feature_set}@hl{self.half_life}"
        )

    @property
    def label(self) -> str:
        weighting = (
            "全期間を等しく扱う"
            if self.half_life is None
            else f"直近重視 (半減期{self.half_life}営業日)"
        )
        return f"{feature_sets.resolve(self.feature_set).label} / {weighting}"


def _variants(set_names: list[str], half_lives: list[int | None]) -> list[Variant]:
    """Return every (feature set x weighting) combination, in a stable order."""

    return [Variant(name, half_life) for name in set_names for half_life in half_lives]


def _parse_half_lives(raw: str) -> list[int | None]:
    values: list[int | None] = []
    for token in str(raw).split(","):
        token = token.strip()
        if not token:
            continue
        values.append(None if token.lower() in {"none", "off", "flat"} else int(token))
    return values or [None]


def _paired_frame(results: dict[str, WindowResult], names: list[str]) -> pd.DataFrame:
    """Return one row per (date, ticker) present in *every* set.

    An unpaired comparison would let a set look better simply by predicting on
    an easier subset of days.
    """

    merged: pd.DataFrame | None = None
    for name in names:
        frame = pd.DataFrame(results[name].predictions)
        if frame.empty:
            return pd.DataFrame()
        part = frame.loc[:, ["date", "ticker", "direction_correct"]].rename(
            columns={"direction_correct": name}
        )
        merged = part if merged is None else merged.merge(part, on=["date", "ticker"])
    return merged if merged is not None else pd.DataFrame()


def main() -> int:
    arguments = _parse_arguments()
    config = load_app_config(arguments.config_dir)
    trading = config.trading
    signal_config = BuySignalConfig(
        return_threshold=_require(
            trading.signal.predicted_intraday_return_threshold,
            "predicted_intraday_return_threshold",
        ),
        probability_threshold=_require(
            trading.signal.probability_up_threshold, "probability_up_threshold"
        ),
    )
    execution_config = ExecutionConfig(
        capital_per_stock=_require(
            trading.position.capital_per_stock_jpy, "capital_per_stock_jpy"
        ),
        lot_size=int(_require(trading.position.lot_size, "lot_size")),
        commission_bps=_require(
            trading.costs.commission_bps_per_side, "commission_bps_per_side"
        ),
        slippage_bps=_require(
            trading.costs.slippage_bps_per_side, "slippage_bps_per_side"
        ),
    )
    training_config = ModelTrainingConfig(
        window_size=arguments.training_window,
        minimum_training_sessions=max(20, arguments.training_window // 2),
        time_series_splits=5,
    )
    history_start = arguments.history_start or default_history_start(
        arguments.from_date, arguments.training_window
    )

    set_names = [
        name.strip() for name in str(arguments.sets).split(",") if name.strip()
    ]
    half_lives = _parse_half_lives(arguments.recency_half_lives)
    variants = _variants(set_names, half_lives)
    baseline_key = arguments.baseline
    if all(variant.key != baseline_key for variant in variants):
        variants.insert(0, Variant(baseline_key, None))
        baseline_key = variants[0].key
    names = [variant.key for variant in variants]
    by_key = {variant.key: variant for variant in variants}

    results: dict[str, WindowResult] = {}
    for variant in variants:
        feature_set = feature_sets.resolve(variant.feature_set)
        print(f"実行中: {variant.key} ({variant.label})", flush=True)
        results[variant.key] = run_window(
            stocks=list(config.stocks.stocks),
            feature_set=feature_set,
            from_date=arguments.from_date,
            to_date=arguments.to_date,
            history_start=history_start,
            training_config=replace(
                training_config, recency_half_life_sessions=variant.half_life
            ),
            signal_config=signal_config,
            execution_config=execution_config,
            cache_dir=None if arguments.no_cache else DEFAULT_CACHE_DIR,
        )
        require_complete_data(
            results[variant.key],
            feature_set,
            allow_missing=arguments.allow_missing_indicators,
        )

    paired = _paired_frame(results, names)
    summaries: dict[str, Any] = {}
    for name in names:
        frame = pd.DataFrame(results[name].predictions)
        if frame.empty:
            summaries[name] = {"predictions": 0}
            continue
        if not paired.empty:
            keys = set(zip(paired["date"], paired["ticker"], strict=True))
            frame = frame.loc[
                [
                    (row_date, row_ticker) in keys
                    for row_date, row_ticker in zip(
                        frame["date"], frame["ticker"], strict=True
                    )
                ]
            ]
        summaries[name] = {
            **_accuracy_metrics(frame),
            "label": by_key[name].label,
            "feature_set": by_key[name].feature_set,
            "recency_half_life_sessions": by_key[name].half_life,
            "feature_count": len(results[name].distinct_features()),
            "excluded_tickers": len(results[name].failures),
            "missing_symbols": sorted(set(results[name].missing_series)),
        }

    comparisons: list[dict[str, Any]] = []
    baseline_name = baseline_key
    for name in names:
        if name == baseline_name or paired.empty:
            continue
        test = _sign_test(paired[name].astype(bool), paired[baseline_name].astype(bool))
        delta = float(
            summaries[name]["direction_accuracy"]
            - summaries[baseline_name]["direction_accuracy"]
        )
        comparisons.append(
            {
                "candidate": name,
                "baseline": baseline_name,
                "direction_accuracy_delta": delta,
                "mean_absolute_error_delta": float(
                    summaries[name]["mean_absolute_error"]
                    - summaries[baseline_name]["mean_absolute_error"]
                ),
                **test,
                "verdict": _verdict(delta, test["p_value"]),
            }
        )

    report = {
        "generated_for": {
            "from": arguments.from_date.isoformat(),
            "to": arguments.to_date.isoformat(),
            "training_window_sessions": arguments.training_window,
            "paired_predictions": len(paired),
            "recency_half_lives": [
                "none" if value is None else value for value in half_lives
            ],
        },
        "baseline": baseline_name,
        "sets": summaries,
        "comparisons": comparisons,
        "caveats": [
            "採否は方向的中率と符号検定で判断しています。"
            "勝率・損益はBUY件数が少なく、証拠になりません。",
            "1期間で勝っただけでは採用理由になりません。"
            "別の期間でも同じ向きに出るかを必ず確認してください。",
            "Yahooの非公式データを使用し、Provider品質ゲートと"
            "PIT lineageを通していません。",
        ],
    }
    arguments.output.parent.mkdir(parents=True, exist_ok=True)
    arguments.output.write_text(
        json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    _print_summary(report, arguments.output)
    return 0


def _print_summary(report: dict[str, Any], output: Path) -> None:
    window = report["generated_for"]
    print(f"\n期間: {window['from']} 〜 {window['to']}")
    print(f"共通の予測件数: {window['paired_predictions']}\n")
    header = (
        f"{'set':10} {'指標数':>5} {'方向的中率':>9} "
        f"{'MAE':>9} {'BUY':>5} {'純損益':>12}"
    )
    print(header)
    print("-" * len(header))
    for name, summary in report["sets"].items():
        if not summary.get("predictions"):
            print(f"{name:10} 予測なし")
            continue
        print(
            f"{name:10} {summary['feature_count']:>5} "
            f"{summary['direction_accuracy'] * 100:>8.2f}% "
            f"{summary['mean_absolute_error']:>9.5f} "
            f"{summary['buy_signals']:>5} "
            f"{summary['net_profit_jpy']:>11,.0f}円"
        )
    print("\n判定 (基準: " + report["baseline"] + ")")
    for comparison in report["comparisons"]:
        print(f"  {comparison['candidate']}: {comparison['verdict']}")
        print(
            f"    片方だけ正解: 候補 {comparison['candidate_only_correct']} / "
            f"基準 {comparison['baseline_only_correct']} "
            f"(判定に使えた {comparison['discordant_pairs']}件)"
        )
    print(f"\n出力: {output}")
    for caveat in report["caveats"]:
        print(f"注意: {caveat}")


if __name__ == "__main__":
    raise SystemExit(main())
