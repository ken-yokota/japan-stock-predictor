"""Measure what each indicator group is worth by removing it.

A Ridge coefficient's size is not the amount of out-of-sample accuracy the
feature buys. Ridge never zeroes anything, correlated features split a single
effect between them, and the ranking that results says which weights are large
rather than which ones earn their place. The only answer to "is this group
carrying its weight" is to take it out and measure the same OOS days again.

Every arm differs from the baseline in exactly one group, and the window, the
tickers, the training length, the model and the cutoff are all held fixed. A
group that can be removed without loss is a removal candidate; one whose removal
hurts is kept; one that cannot be told apart is kept too, because absence of
evidence is not a reason to delete.

The multiple-testing cost is real and is reported rather than hidden: with a
group count in the thirties, a Bonferroni threshold near 0.0016 is what a single
claim has to clear, which is why the window matters more than the arm count.
"""

from __future__ import annotations

import argparse
import csv
from dataclasses import replace
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import pandas as pd

from data.config import load_app_config
from models.base import ModelTrainingConfig
from research.feature_sets import FeatureSet, resolve
from research.metrics import paired_rank_ic, rank_ic_series, summarise_rank_ic
from research.walk import default_history_start, run_window
from scripts.run_feature_comparison import _require
from scripts.run_pooling_comparison import _divergence, _paired_error_test
from trading.strategy import BuySignalConfig, ExecutionConfig


def _without(feature_set: FeatureSet, key: str) -> FeatureSet:
    return replace(
        feature_set,
        name=f"{feature_set.name}_minus_{key}",
        indicators=tuple(s for s in feature_set.indicators if s.key != key),
    )


def _frame(predictions: list[dict[str, Any]]) -> pd.DataFrame:
    frame = pd.DataFrame(predictions)
    return frame.set_index(["date", "ticker"]).sort_index()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--feature-set", default="production")
    parser.add_argument("--sessions", type=int, default=250)
    parser.add_argument("--to-date", default="2026-08-14")
    parser.add_argument("--training-window", type=int, default=120)
    parser.add_argument("--groups", default=None, help="comma list; default all")
    parser.add_argument(
        "--output", type=Path, default=Path("reports/group_ablation.csv")
    )
    arguments = parser.parse_args(argv)

    config = load_app_config()
    trading = config.trading
    base = resolve(arguments.feature_set)
    to_date = date.fromisoformat(arguments.to_date)
    from_date = to_date - timedelta(days=int(arguments.sessions * 1.5))
    history_start = default_history_start(from_date, arguments.training_window)

    common: dict[str, Any] = {
        "stocks": list(config.stocks.stocks),
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
                trading.signal.predicted_intraday_return_threshold, "threshold"
            ),
            probability_threshold=_require(
                trading.signal.probability_up_threshold, "probability"
            ),
        ),
        "execution_config": ExecutionConfig(
            capital_per_stock=_require(
                trading.position.capital_per_stock_jpy, "capital"
            ),
            lot_size=int(_require(trading.position.lot_size, "lot")),
            commission_bps=_require(trading.costs.commission_bps_per_side, "comm"),
            slippage_bps=_require(trading.costs.slippage_bps_per_side, "slip"),
        ),
    }

    groups = (
        [g.strip() for g in str(arguments.groups).split(",") if g.strip()]
        if arguments.groups
        else [spec.key for spec in base.indicators]
    )
    threshold = 0.05 / max(1, len(groups))
    print(f"baseline    : {base.name} ({len(base.indicators)} 指標)")
    print(f"window      : {from_date} .. {to_date}")
    print(f"groups      : {len(groups)}")
    print(f"補正線      : p < {threshold:.5f} （Bonferroni）")
    print("", flush=True)

    print("baseline を実行中 ...", flush=True)
    baseline = _frame(run_window(feature_set=base, **common).predictions)

    rows: list[dict[str, Any]] = []
    for index, key in enumerate(groups, 1):
        print(f"[{index}/{len(groups)}] {key} を除いて実行中 ...", flush=True)
        arm = _frame(run_window(feature_set=_without(base, key), **common).predictions)
        shared = baseline.index.intersection(arm.index)
        left, right = arm.loc[shared], baseline.loc[shared]

        gap = _paired_error_test(_divergence(left), _divergence(right))
        ic = paired_rank_ic(left.reset_index(), right.reset_index())
        base_ic = summarise_rank_ic(rank_ic_series(right.reset_index())).mean
        accuracy = (
            left["direction_correct"].mean() - right["direction_correct"].mean()
        ) * 100

        # Positive mae_delta means removal made it worse, so the group earns its
        # place. Negative means the model was better without it.
        keep = bool(gap["p_value"] is not None and gap["p_value"] < threshold
                    and gap["mean_delta_pp"] > 0)
        rows.append({
            "group": key,
            "paired": len(shared),
            "mae_delta_on_removal_pp": round(gap["mean_delta_pp"], 5),
            "mae_p": gap["p_value"],
            "direction_delta_pp": round(float(accuracy), 3),
            "rank_ic_delta_on_removal": round(ic.mean, 5),
            "rank_ic_p": ic.p_value,
            "baseline_rank_ic": round(base_ic, 5),
            "verdict": "KEEP (removal hurts)" if keep else "REMOVAL CANDIDATE",
        })
        print(f"      MAE {gap['mean_delta_pp']:+.5f}pp p={gap['p_value']}  "
              f"IC {ic.mean:+.5f}  {rows[-1]['verdict']}", flush=True)

    arguments.output.parent.mkdir(parents=True, exist_ok=True)
    with arguments.output.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    print(f"\nwritten: {arguments.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
