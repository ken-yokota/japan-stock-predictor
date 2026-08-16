"""Per-ticker report: which indicators each stock's model actually leans on.

The system fits one model per ticker per session, so "which indicators matter"
has 22 different answers that each change daily. This walks the window, keeps
every coefficient, and asks three things of each ticker:

**What does it lean on?** Ridge never zeroes a coefficient, so importance is
rank by absolute standardised weight, not membership. Features are standardised
inside the pipeline, so weights are comparable across indicators of different
units - that is the only reason this ranking means anything.

**Is that stable?** A coefficient that changes sign between sessions is not
describing a relationship; it is absorbing noise. Sign stability and rank churn
are reported beside the weight, because a large unstable weight is worse than a
small stable one and looks better.

**Does it predict?** Per-ticker rank-IC contribution and direction accuracy,
so a ticker whose model is confidently wrong is visible as such.

Writes JSON and Markdown. Refits nothing that the cache already covers.
"""

from __future__ import annotations

import argparse
import json
from collections.abc import Callable
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import pandas as pd

from data.config import load_app_config
from models.base import ModelTrainingConfig
from research.feature_sets import resolve
from research.walk import default_history_start, run_window
from scripts.run_feature_comparison import _require
from trading.strategy import BuySignalConfig, ExecutionConfig

TOP_N = 8
ALL = None


OWN_PRICE = "自銘柄の価格"


def _indicator_mapper(keys: tuple[str, ...]) -> Callable[[str], str]:
    """Group a feature back to the series it came from.

    `IndicatorSpec.column_names` builds `{key}_{suffix}_{window}d` with a single
    underscore, so splitting on a separator cannot recover the key - `copper`
    and `us_10y_yield` have different underscore counts. Matching the longest
    configured key that the name starts with is exact, and anything unmatched is
    one of the eleven own-price columns.
    """

    ordered = sorted(keys, key=len, reverse=True)

    def resolve(feature: str) -> str:
        for key in ordered:
            if feature.startswith(f"{key}_"):
                return key
        return OWN_PRICE

    return resolve


def analyse(
    coefficients: pd.DataFrame,
    predictions: pd.DataFrame,
    indicator_keys: tuple[str, ...] = (),
    top_n: int | None = TOP_N,
) -> dict[str, dict[str, Any]]:
    """Per-ticker importance, stability, and predictive record."""

    _indicator_of = _indicator_mapper(indicator_keys)
    report: dict[str, dict[str, Any]] = {}
    for ticker, block in coefficients.groupby("ticker"):
        block = block.copy()
        block["indicator"] = block["feature"].map(_indicator_of)
        block["absolute"] = block["coefficient"].abs()

        # Rank within each session, so a day of large weights cannot dominate.
        block["rank"] = block.groupby("date")["absolute"].rank(
            ascending=False, method="min"
        )
        by_feature = block.groupby("feature").agg(
            mean_abs=("absolute", "mean"),
            mean_coef=("coefficient", "mean"),
            std_coef=("coefficient", "std"),
            mean_rank=("rank", "mean"),
            sessions=("coefficient", "size"),
            positive=("coefficient", lambda s: int((s > 0).sum())),
        )
        by_feature["sign_stability"] = (
            by_feature[["positive", "sessions"]]
            .assign(other=lambda d: d.sessions - d.positive)[["positive", "other"]]
            .max(axis=1)
            / by_feature["sessions"]
        )
        top = by_feature.sort_values("mean_abs", ascending=False)
        if top_n is not None:
            top = top.head(top_n)

        by_indicator = (
            block.groupby("indicator")["absolute"].mean().sort_values(ascending=False)
        )

        own = predictions.loc[predictions["ticker"] == ticker]
        direction = float(own["direction_correct"].mean()) if not own.empty else None
        mae = (
            float((own["predicted_return"] - own["actual_return"]).abs().mean() * 100)
            if not own.empty
            else None
        )
        buys = int((own["signal"] == "BUY").sum()) if not own.empty else 0
        buy_hits = (
            int(((own["signal"] == "BUY") & (own["actual_return"] > 0)).sum())
            if not own.empty
            else 0
        )

        report[str(ticker)] = {
            "sessions": int(block["date"].nunique()),
            "features": int(block["feature"].nunique()),
            "direction_accuracy": direction,
            "mae_pp": mae,
            "buys": buys,
            "buy_hits": buy_hits,
            "mean_sign_stability": float(by_feature["sign_stability"].mean()),
            "unstable_features": int((by_feature["sign_stability"] < 0.7).sum()),
            "top_features": [
                {
                    "feature": name,
                    "indicator": _indicator_of(name),
                    "mean_abs": float(r.mean_abs),
                    "mean_coef": float(r.mean_coef),
                    "std_coef": float(r.std_coef) if pd.notna(r.std_coef) else 0.0,
                    "mean_rank": float(r.mean_rank),
                    "sign_stability": float(r.sign_stability),
                }
                for name, r in top.iterrows()
            ],
            "top_indicators": [
                {"indicator": str(k), "mean_abs": float(v)}
                for k, v in (
                    by_indicator if top_n is None else by_indicator.head(top_n)
                ).items()
            ],
        }
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--feature-set", default="production")
    parser.add_argument("--sessions", type=int, default=60)
    parser.add_argument("--to-date", default="2026-08-14")
    parser.add_argument("--training-window", type=int, default=120)
    parser.add_argument("--json", type=Path, default=None)
    parser.add_argument(
        "--all",
        action="store_true",
        help="keep every feature and indicator, not only the top few",
    )
    arguments = parser.parse_args(argv)

    config = load_app_config()
    trading = config.trading
    feature_set = resolve(arguments.feature_set)
    to_date = date.fromisoformat(arguments.to_date)
    from_date = to_date - timedelta(days=int(arguments.sessions * 1.5))
    history_start = default_history_start(from_date, arguments.training_window)

    print(f"feature set : {feature_set.name} ({len(feature_set.indicators)} 指標)")
    print(f"window      : {from_date} .. {to_date}", flush=True)

    result = run_window(
        stocks=list(config.stocks.stocks),
        feature_set=feature_set,
        from_date=from_date,
        to_date=to_date,
        history_start=history_start,
        training_config=ModelTrainingConfig(
            window_size=arguments.training_window,
            minimum_training_sessions=max(20, arguments.training_window // 2),
            time_series_splits=5,
        ),
        signal_config=BuySignalConfig(
            return_threshold=_require(
                trading.signal.predicted_intraday_return_threshold, "threshold"
            ),
            probability_threshold=_require(
                trading.signal.probability_up_threshold, "probability"
            ),
        ),
        execution_config=ExecutionConfig(
            capital_per_stock=_require(
                trading.position.capital_per_stock_jpy, "capital"
            ),
            lot_size=int(_require(trading.position.lot_size, "lot")),
            commission_bps=_require(trading.costs.commission_bps_per_side, "comm"),
            slippage_bps=_require(trading.costs.slippage_bps_per_side, "slip"),
        ),
    )

    coefficients = pd.DataFrame(result.coefficients)
    predictions = pd.DataFrame(result.predictions)
    if coefficients.empty:
        print("no coefficients were produced")
        return 1

    report = analyse(
        coefficients,
        predictions,
        tuple(spec.key for spec in feature_set.indicators),
        top_n=None if arguments.all else TOP_N,
    )
    payload = {
        "feature_set": feature_set.name,
        "window": [from_date.isoformat(), to_date.isoformat()],
        "training_window": arguments.training_window,
        "tickers": report,
    }
    if arguments.json:
        arguments.json.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(f"written: {arguments.json}")

    for ticker, item in sorted(report.items()):
        print(f"\n=== {ticker} ===")
        print(
            f"  方向的中 {item['direction_accuracy']:.3f}  "
            f"MAE {item['mae_pp']:.3f}pt  BUY {item['buy_hits']}/{item['buys']}  "
            f"符号安定 {item['mean_sign_stability']:.3f}  "
            f"不安定特徴量 {item['unstable_features']}/{item['features']}"
        )
        for f in item["top_features"][:5]:
            print(
                f"    {f['feature'][:34]:34} 係数 {f['mean_coef']:+.5f} "
                f"±{f['std_coef']:.5f}  安定 {f['sign_stability']:.2f}"
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
