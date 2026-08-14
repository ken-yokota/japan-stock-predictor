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
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import pandas as pd

from data.config import load_app_config
from models.base import ModelTrainingConfig
from research.feature_sets import resolve
from research.walk import default_history_start, run_pooled_window, run_window
from scripts.run_feature_comparison import _require, _sign_test
from trading.strategy import BuySignalConfig, ExecutionConfig


def _frame(predictions: list[dict[str, Any]]) -> pd.DataFrame:
    frame = pd.DataFrame(predictions)
    if frame.empty:
        return frame
    return frame.set_index(["date", "ticker"]).sort_index()


def _accuracy(frame: pd.DataFrame) -> float:
    return float(frame["direction_correct"].mean()) if not frame.empty else 0.0


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
    common = {
        "stocks": list(config.stocks.stocks),
        "feature_set": feature_set,
        "from_date": from_date,
        "to_date": to_date,
        "history_start": history_start,
        "training_config": ModelTrainingConfig(
            window_size=arguments.training_window,
            minimum_training_sessions=max(20, arguments.training_window // 2),
            time_series_splits=5,
        ),
        "signal_config": BuySignalConfig(
            return_threshold=_require(
                trading.signal.predicted_intraday_return_threshold,
                "predicted_intraday_return_threshold",
            ),
            probability_threshold=_require(
                trading.signal.probability_up_threshold, "probability_up_threshold"
            ),
        ),
        "execution_config": ExecutionConfig(
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
        ),
    }

    print(f"feature set   : {feature_set.name}")
    print(f"window        : {from_date} .. {to_date}")
    print(f"history from  : {history_start}")
    print("")

    print("fitting per ticker ...", flush=True)
    baseline = _frame(run_window(**common).predictions)
    print("fitting per sector ...", flush=True)
    pooled = _frame(run_pooled_window(**common).predictions)

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

    print("")
    print(f"paired predictions        : {len(shared):,}")
    print(f"per-ticker training rows  : median "
          f"{baseline['training_sessions'].median():.0f}")
    print(f"pooled training rows      : median "
          f"{pooled['training_sessions'].median():.0f}")
    print("")
    print(f"direction accuracy (ticker) : {base_accuracy:.4f}")
    print(f"direction accuracy (pooled) : {pooled_accuracy:.4f}")
    print(f"difference                  : {delta:+.2f}pp")
    print("")
    print(f"pooled only correct   : {test['candidate_only_correct']}")
    print(f"ticker only correct   : {test['baseline_only_correct']}")
    print(f"discordant pairs      : {test['discordant_pairs']}")
    print(f"p-value               : {test['p_value']}")
    print("")
    print(f"BUY (ticker) : {int((baseline['signal'] == 'BUY').sum())}")
    print(f"BUY (pooled) : {int((pooled['signal'] == 'BUY').sum())}")
    print("※ 売買統計は件数が少なく、採否の判断には使いません。")

    if arguments.output:
        arguments.output.write_text(
            json.dumps(
                {
                    "paired": len(shared),
                    "accuracy_per_ticker": base_accuracy,
                    "accuracy_pooled": pooled_accuracy,
                    "delta_pp": delta,
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
