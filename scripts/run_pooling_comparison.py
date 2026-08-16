"""Measure whether pooling a sector predicts better than fitting each ticker.

This is the experiment the indicator work has been waiting on. Four indicator
proposals were measured here at +0.19 to +0.93pp of direction accuracy against
a detectable effect of about 3.1pp: the tests could not tell, and a fifth would
not either. Pooling multiplies the training rows by the number of tickers in a
sector without adding a series, so it moves the threshold rather than spending
against it.

Both arms run over the same sessions and the same tickers, and the verdict
comes from a paired sign test on the predictions where exactly one arm got the
direction right. Aggregate accuracy alone cannot separate a percentage point of
signal from a percentage point of noise.

Trade statistics are printed and never used to judge: they rest on a handful of
BUY signals, and a difference there is not measurable.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Callable
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import pandas as pd

from data.config import load_app_config
from models.base import ModelTrainingConfig
from research.feature_sets import resolve
from research.metrics import (
    paired_rank_ic,
    pearson_ic_series,
    rank_ic_series,
    summarise_rank_ic,
)
from research.trading_metrics import evaluate_all
from research.walk import (
    WindowResult,
    default_history_start,
    run_cross_sectional_window,
    run_pooled_window,
    run_window,
)
from scripts.run_feature_comparison import _require, _sign_test
from trading.strategy import BuySignalConfig, ExecutionConfig


def _frame(predictions: list[dict[str, Any]]) -> pd.DataFrame:
    frame = pd.DataFrame(predictions)
    if frame.empty:
        return frame
    return frame.set_index(["date", "ticker"]).sort_index()


def _accuracy(frame: pd.DataFrame) -> float:
    return float(frame["direction_correct"].mean()) if not frame.empty else 0.0


def _fitting_arms() -> dict[str, tuple[str, Any, dict[str, Any]]]:
    """The arms that hold the predictors fixed and vary how the fit is done."""

    return {
        "A": ("銘柄別・全特徴量（本番）", run_window, {}),
        "B": ("銘柄別・共通特徴量のみ（対照）", run_pooled_window,
              {"share_training": False}),
        "C": ("業種プール・共通特徴量", run_pooled_window, {"share_training": True}),
        "D": ("銘柄別・相対リターンを学習", run_cross_sectional_window, {}),
    }


def _divergence(frame: pd.DataFrame) -> pd.Series:
    """Absolute prediction-vs-outcome gap, per prediction, in points."""

    gap = frame["predicted_return"].astype(float) - frame[
        "actual_return"
    ].astype(float)
    return gap.abs() * 100


def _paired_error_test(candidate: pd.Series, baseline: pd.Series) -> dict[str, Any]:
    """Did the candidate miss by less, on the same predictions?

    Paired on purpose: two aggregate MAEs differ by whichever days each arm
    happened to see, and the same 22 tickers share one market every morning.
    """

    difference = (candidate - baseline).dropna()
    if difference.empty or float(difference.std(ddof=1) or 0.0) == 0.0:
        return {"mean_delta_pp": 0.0, "p_value": None, "paired": len(difference)}
    from scipy.stats import ttest_rel  # type: ignore[import-untyped]

    result = ttest_rel(candidate, baseline, nan_policy="omit")
    return {
        "mean_delta_pp": float(difference.mean()),
        "p_value": float(result.pvalue),
        "paired": len(difference),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--feature-set", default="baseline")
    parser.add_argument(
        "--arms",
        default="A,B,C",
        help="which arms to run; A is always the baseline to compare against",
    )
    parser.add_argument(
        "--feature-sets",
        default=None,
        help=(
            "compare feature sets instead of fitting modes: each named set is "
            "run through the production path and paired against the first"
        ),
    )
    parser.add_argument("--sessions", type=int, default=60)
    parser.add_argument("--to-date", default=None)
    parser.add_argument("--training-window", type=int, default=120)
    parser.add_argument("--output", type=Path, default=None)
    parser.add_argument(
        "--dump-predictions",
        type=Path,
        default=None,
        help="write every arm's predictions so later analysis needs no refit",
    )
    arguments = parser.parse_args(argv)

    config = load_app_config()
    feature_set = resolve(arguments.feature_set)
    to_date = (
        date.fromisoformat(arguments.to_date)
        if arguments.to_date
        else date.today() - timedelta(days=1)
    )
    from_date = to_date - timedelta(days=int(arguments.sessions * 1.5))
    history_start = default_history_start(from_date, arguments.training_window)

    trading = config.trading
    training_config = ModelTrainingConfig(
        window_size=arguments.training_window,
        minimum_training_sessions=max(20, arguments.training_window // 2),
        time_series_splits=5,
    )
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

    def _run(runner: Callable[..., WindowResult], **extra: Any) -> pd.DataFrame:
        """Every arm takes the same arguments; only the fitting differs."""

        return _frame(
            runner(
                stocks=list(config.stocks.stocks),
                feature_set=extra.pop("feature_set", feature_set),
                from_date=from_date,
                to_date=to_date,
                history_start=history_start,
                training_config=training_config,
                signal_config=signal_config,
                execution_config=execution_config,
                **extra,
            ).predictions
        )

    set_names = [
        name.strip()
        for name in str(arguments.feature_sets or "").split(",")
        if name.strip()
    ]
    if set_names:
        # One arm per feature set, all through the production fitting path, so
        # the only thing that differs between them is the predictor list.
        definitions = {
            name: (
                f"銘柄別・特徴量セット {name}",
                run_window,
                {"feature_set": resolve(name)},
            )
            for name in set_names
        }
        wanted = list(definitions)
    else:
        definitions = _fitting_arms()
        wanted = [name.strip().upper() for name in str(arguments.arms).split(",")]
        wanted = [name for name in wanted if name in definitions]
        if "A" not in wanted:
            wanted.insert(0, "A")

    built: dict[str, pd.DataFrame] = {}
    for name in wanted:
        label, runner, extra = definitions[name]
        print(f"{name} {label} を実行中 ...", flush=True)
        built[name] = _run(runner, **extra)
        if built[name].empty:
            print(f"arm {name} produced no predictions", file=sys.stderr)
            return 1

    shared = built[wanted[0]].index
    for name in wanted[1:]:
        shared = shared.intersection(built[name].index)
    arms = {name: frame.loc[shared] for name, frame in built.items()}

    print("")
    print(f"paired predictions : {len(shared):,}")
    print(f"sessions           : {shared.get_level_values('date').nunique()}")
    print("")
    for name in wanted:
        print(f"{name} = {definitions[name][0]}")
    print("")

    print(f"{'arm':22}{'MAE':>8}{'RMSE':>8}{'方向的中':>10}"
          f"{'RankIC':>9}{'IC p':>8}{'PearsonIC':>11}")
    print("-" * 76)
    summaries = {}
    for name, frame in arms.items():
        gap = _divergence(frame)
        summary = summarise_rank_ic(rank_ic_series(frame.reset_index()))
        pearson = summarise_rank_ic(pearson_ic_series(frame.reset_index()))
        summaries[name] = (gap, summary, pearson)
        print(
            f"{name:22}{gap.mean():>8.4f}{float((gap ** 2).mean() ** 0.5):>8.4f}"
            f"{_accuracy(frame):>10.4f}{summary.mean:>9.4f}"
            f"{(summary.p_value or float('nan')):>8.3f}{pearson.mean:>11.4f}"
        )

    # The selection rules. Rank IC says the ordering is right; only this says
    # whether acting on it paid, and a set is preferred only when both agree.
    print("")
    print("=== BUY戦略（往復コスト差引後、リターン空間） ===")
    print(f"{'arm':22}{'rule':10}{'件数':>7}{'勝率':>8}{'PF':>8}"
          f"{'期待値':>10}{'純損益':>10}{'最大DD':>9}{'Sharpe':>9}{'Sortino':>9}")
    print("-" * 102)
    strategies: dict[str, Any] = {}
    for name, frame in arms.items():
        by_rule = evaluate_all(
            frame.reset_index(),
            commission_bps=execution_config.commission_bps,
            slippage_bps=execution_config.slippage_bps,
        )
        strategies[name] = {
            rule: {
                "trades": r.trades,
                "win_rate": r.win_rate,
                "profit_factor": r.profit_factor,
                "expectancy": r.expectancy,
                "net_profit": r.net_profit,
                "max_drawdown": r.max_drawdown,
                "sharpe": r.sharpe,
                "sortino": r.sortino,
                "measurable": r.is_measurable,
            }
            for rule, r in by_rule.items()
        }
        for rule, r in by_rule.items():
            flag = "" if r.is_measurable else "  ※20件未満"
            print(
                f"{name:22}{rule:10}{r.trades:>7}{r.win_rate:>8.3f}"
                f"{min(r.profit_factor, 99.9):>8.2f}{r.expectancy:>10.5f}"
                f"{r.net_profit:>10.4f}{r.max_drawdown:>9.4f}"
                f"{r.sharpe:>9.2f}{r.sortino:>9.2f}{flag}"
            )

    first = wanted[0]
    pairs = [
        (f"{first}→{name}", f"{first} に対する {name}", name, first)
        for name in wanted[1:]
    ]
    if "B" in arms and "C" in arms:
        pairs.append(("B→C", "プール学習そのものの効果", "C", "B"))

    results: dict[str, Any] = {
        "paired": len(shared),
        "sessions": int(shared.get_level_values("date").nunique()),
        "arms": {
            name: {
                "mae": float(summaries[name][0].mean()),
                "rank_ic": summaries[name][1].mean,
                "rank_ic_p": summaries[name][1].p_value,
                "pearson_ic": summaries[name][2].mean,
                "direction": _accuracy(arms[name]),
                "strategy": strategies[name],
            }
            for name in wanted
        },
    }
    for label, question, candidate, baseline in pairs:
        left, right = arms[candidate], arms[baseline]
        gap_test = _paired_error_test(
            summaries[candidate][0], summaries[baseline][0]
        )
        sign = _sign_test(
            left["direction_correct"].astype(bool),
            right["direction_correct"].astype(bool),
        )
        ic = paired_rank_ic(left.reset_index(), right.reset_index())
        accuracy_delta = (_accuracy(left) - _accuracy(right)) * 100
        print("")
        print(f"=== {label}  {question} ===")
        print(
            f"  予実乖離 : {gap_test['mean_delta_pp']:+.4f}pp  "
            f"p={gap_test['p_value']}  （正なら悪化）"
        )
        print(
            f"  方向的中 : {accuracy_delta:+.2f}pp  p={sign['p_value']}  "
            f"不一致={sign['discordant_pairs']}"
        )
        print(
            f"  Rank IC  : {ic.mean:+.4f}  p={ic.p_value}  "
            f"95%CI [{ic.confidence_low:+.4f}, {ic.confidence_high:+.4f}]"
        )
        print(f"  {ic.verdict()}")
        results[label] = {
            "mae_delta_pp": gap_test["mean_delta_pp"],
            "mae_p_value": gap_test["p_value"],
            "accuracy_delta_pp": accuracy_delta,
            "accuracy_p_value": sign["p_value"],
            "rank_ic_delta": ic.mean,
            "rank_ic_p_value": ic.p_value,
            "rank_ic_detectable": ic.detectable_ic,
            "rank_ic_ci": [ic.confidence_low, ic.confidence_high],
            "verdict": ic.verdict(),
        }

    print("")
    print("※ BUY件数は少なすぎるため採否判断には使いません: " + ", ".join(
        f"{name}={int((frame['signal'] == 'BUY').sum())}"
        for name, frame in arms.items()
    ))

    if arguments.dump_predictions:
        combined = pd.concat(
            [frame.assign(arm=name) for name, frame in arms.items()]
        ).reset_index()
        combined.to_csv(arguments.dump_predictions, index=False)
        print("")
        print(f"predictions written to {arguments.dump_predictions}")

    if arguments.output:
        arguments.output.write_text(
            json.dumps(results, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
