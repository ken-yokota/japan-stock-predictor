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
from research.metrics import paired_rank_ic, rank_ic_series, summarise_rank_ic
from research.walk import (
    WindowResult,
    default_history_start,
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
        "paired": int(len(difference)),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--feature-set", default="baseline")
    parser.add_argument("--sessions", type=int, default=60)
    parser.add_argument("--to-date", default=None)
    parser.add_argument("--training-window", type=int, default=120)
    parser.add_argument("--output", type=Path, default=None)
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

    def _run(runner: Callable[..., WindowResult]) -> pd.DataFrame:
        """Both arms take the same arguments; only the fitting differs."""

        return _frame(
            runner(
                stocks=list(config.stocks.stocks),
                feature_set=feature_set,
                from_date=from_date,
                to_date=to_date,
                history_start=history_start,
                training_config=training_config,
                signal_config=signal_config,
                execution_config=execution_config,
            ).predictions
        )

    print(f"feature set   : {feature_set.name}")
    print(f"window        : {from_date} .. {to_date}")
    print(f"history from  : {history_start}")
    print("")

    print("fitting per ticker ...", flush=True)
    baseline = _run(run_window)
    print("fitting per sector ...", flush=True)
    pooled = _run(run_pooled_window)

    if baseline.empty or pooled.empty:
        print("one arm produced no predictions", file=sys.stderr)
        return 1

    shared = baseline.index.intersection(pooled.index)
    baseline = baseline.loc[shared]
    pooled = pooled.loc[shared]

    base_accuracy = _accuracy(baseline)
    pooled_accuracy = _accuracy(pooled)
    delta = (pooled_accuracy - base_accuracy) * 100
    test = _sign_test(
        pooled["direction_correct"].astype(bool),
        baseline["direction_correct"].astype(bool),
    )

    base_gap = _divergence(baseline)
    pooled_gap = _divergence(pooled)
    gap_test = _paired_error_test(pooled_gap, base_gap)

    base_ic = summarise_rank_ic(rank_ic_series(baseline.reset_index()))
    pooled_ic = summarise_rank_ic(rank_ic_series(pooled.reset_index()))
    ic_test = paired_rank_ic(pooled.reset_index(), baseline.reset_index())

    print("")
    print(f"paired predictions : {len(shared):,}")
    print(f"sessions           : {baseline.index.get_level_values('date').nunique()}")
    print("")
    print("=== 予実乖離（小さいほど良い、単位=%ポイント） ===")
    print(f"  MAE  銘柄別 : {base_gap.mean():.4f}")
    print(f"  MAE  プール : {pooled_gap.mean():.4f}")
    print(f"  差          : {gap_test['mean_delta_pp']:+.4f}  p={gap_test['p_value']}")
    print(f"  RMSE 銘柄別 : {float((base_gap ** 2).mean() ** 0.5):.4f}")
    print(f"  RMSE プール : {float((pooled_gap ** 2).mean() ** 0.5):.4f}")
    print("")
    print("=== 方向的中（従来指標） ===")
    print(f"  銘柄別 : {base_accuracy:.4f}")
    print(f"  プール : {pooled_accuracy:.4f}")
    print(f"  差     : {delta:+.2f}pp   p={test['p_value']}")
    print(f"  不一致ペア : {test['discordant_pairs']}")
    print("")
    print("=== Rank IC（日次断面、市場要因が相殺される） ===")
    for name, summary in (("銘柄別", base_ic), ("プール", pooled_ic)):
        print(f"  {name} : IC={summary.mean:+.4f} IR={summary.information_ratio:+.3f} "
              f"p={summary.p_value} 日数={summary.days}")
        print(f"         検出下限={summary.detectable_ic:.4f} "
              f"lag1={summary.lag1_autocorrelation}")
    print(f"  差    : {ic_test.mean:+.4f}  p={ic_test.p_value}")
    print(f"  {ic_test.verdict()}")
    print("")
    print(f"BUY 銘柄別 {int((baseline['signal'] == 'BUY').sum())} / "
          f"プール {int((pooled['signal'] == 'BUY').sum())}"
          "   ※件数が少なく採否判断には使いません")

    if arguments.output:
        arguments.output.write_text(
            json.dumps(
                {
                    "paired": len(shared),
                    "accuracy_per_ticker": base_accuracy,
                    "accuracy_pooled": pooled_accuracy,
                    "delta_pp": delta,
                    "mae_per_ticker": float(base_gap.mean()),
                    "mae_pooled": float(pooled_gap.mean()),
                    "mae_delta_pp": gap_test["mean_delta_pp"],
                    "mae_p_value": gap_test["p_value"],
                    "rank_ic_per_ticker": base_ic.mean,
                    "rank_ic_pooled": pooled_ic.mean,
                    "rank_ic_delta": ic_test.mean,
                    "rank_ic_p_value": ic_test.p_value,
                    "rank_ic_detectable": ic_test.detectable_ic,
                    **test,
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
