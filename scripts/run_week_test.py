"""In-memory walk-forward test over a short recent window.

Purpose: answer "train on the last ~half year, then judge this week" without a
PostgreSQL database, so the result can be produced before Neon exists. It uses
the same model code as production (``models.train_ticker_model``: Ridge for the
return, Logistic for the direction, both fitted inside their own rolling window)
and the same execution model (``trading.simulate_intraday_trade``).

## Look-ahead prevention

``features.build_price_features`` computes every column from the *same* row's
OHLC. One of those columns, ``open_close_return``, is literally the target
(``close / open - 1``). Feeding row ``t``'s features to predict row ``t`` would
therefore hand the model the answer.

Every predictor here is shifted by one session, so a prediction for date ``t``
sees only data through the close of ``t - 1``:

* Each stock's own price features are lagged one JPX session.
* Overseas indicators are aligned onto JPX dates and then lagged one session
  too. That is deliberately conservative: the previous US close is genuinely
  available before 08:30 JST, but lagging avoids any calendar edge case where a
  same-day value could slip in.

Only the target (``close / open - 1`` on day ``t``) uses day ``t`` data.

## What this is not

Prices come from Yahoo, which is unofficial. This path skips the provider
quality gates, freshness checks, and point-in-time lineage that the database
pipeline enforces, and it reconstructs availability from the calendar rather
than from observed timestamps. Treat the output as a research estimate that is
probably optimistic, not as a live track record.

    python -m cli week-test --from-date 2026-08-01 --to-date 2026-08-07
    python -m cli week-test --feature-set focused --from-date 2026-06-01
"""

from __future__ import annotations

import argparse
import json
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import pandas as pd

from data.config import load_app_config
from models.base import ModelTrainingConfig
from research import feature_sets
from research.history import DEFAULT_CACHE_DIR
from research.walk import default_history_start, require_complete_data, run_window
from trading.strategy import BuySignalConfig, ExecutionConfig


def _require(value: object, name: str) -> float:
    """Return a configured numeric setting, or fail with a clear message.

    These fields are optional in the config model because earlier phases left
    cost assumptions unconfirmed. Silently defaulting them would report a
    profit that assumed zero commission, so an unset value stops the run.
    """

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
        "--from-date", type=date.fromisoformat, default=today - timedelta(days=7)
    )
    parser.add_argument(
        "--to-date", type=date.fromisoformat, default=today - timedelta(days=1)
    )
    parser.add_argument("--history-start", type=date.fromisoformat, default=None)
    parser.add_argument("--training-window", type=int, default=120)
    parser.add_argument(
        "--feature-set",
        default=feature_sets.DEFAULT_FEATURE_SET,
        choices=sorted(feature_sets.FEATURE_SETS),
        help="使用する予測要素の組。既定は現行と同じ baseline。",
    )
    parser.add_argument(
        "--recency-half-life",
        type=int,
        default=None,
        help=(
            "直近の営業日ほど学習で重く扱う。半減期を営業日で指定する。"
            "未指定なら現行どおり全期間を等しく扱う。"
        ),
    )
    parser.add_argument(
        "--allow-missing-indicators",
        action="store_true",
        help="取得できなかった系列があっても、欠けたまま実行する。",
    )
    parser.add_argument(
        "--no-cache",
        action="store_true",
        help="artifacts/cache のダウンロード済み日足を使わず、必ず取得し直す。",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("artifacts/week_test/latest.json"),
    )
    return parser.parse_args()


def main() -> int:
    """Run the rolling test and write both a JSON artifact and a summary."""

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
        recency_half_life_sessions=arguments.recency_half_life,
    )

    history_start = arguments.history_start or default_history_start(
        arguments.from_date, arguments.training_window
    )
    feature_set = feature_sets.resolve(arguments.feature_set)
    result = run_window(
        stocks=list(config.stocks.stocks),
        feature_set=feature_set,
        from_date=arguments.from_date,
        to_date=arguments.to_date,
        history_start=history_start,
        training_config=training_config,
        signal_config=signal_config,
        execution_config=execution_config,
        cache_dir=None if arguments.no_cache else DEFAULT_CACHE_DIR,
    )

    require_complete_data(
        result, feature_set, allow_missing=arguments.allow_missing_indicators
    )

    report = _build_report(
        result.predictions,
        result.coefficients,
        arguments,
        signal_config,
        execution_config,
        result.failures,
        feature_set,
        result.missing_series,
    )
    arguments.output.parent.mkdir(parents=True, exist_ok=True)
    arguments.output.write_text(
        json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    _print_summary(report, arguments.output)
    return 0


def _build_report(
    predictions: list[dict[str, Any]],
    coefficients: list[dict[str, Any]],
    arguments: argparse.Namespace,
    signal_config: BuySignalConfig,
    execution_config: ExecutionConfig,
    failures: dict[str, str],
    feature_set: feature_sets.FeatureSet,
    missing_series: list[str],
) -> dict[str, Any]:
    frame = pd.DataFrame(predictions)
    coefficient_frame = pd.DataFrame(coefficients)

    daily: list[dict[str, Any]] = []
    if not frame.empty:
        for day, group in frame.groupby("date", sort=True):
            traded = group.loc[group["signal"] == "BUY"]
            wins = traded.loc[traded["net_profit_jpy"] > 0.0]
            losses = traded.loc[traded["net_profit_jpy"] < 0.0]
            gross_win = float(wins["net_profit_jpy"].sum())
            gross_loss = float(-losses["net_profit_jpy"].sum())
            daily.append(
                {
                    "date": str(day),
                    "predictions": len(group),
                    "buy_signals": len(traded),
                    "wins": len(wins),
                    "losses": len(losses),
                    "win_rate": (len(wins) / len(traded)) if len(traded) else None,
                    "gross_win_jpy": gross_win,
                    "gross_loss_jpy": gross_loss,
                    "money_win_ratio": (
                        gross_win / gross_loss if gross_loss > 0.0 else None
                    ),
                    "net_profit_jpy": float(traded["net_profit_jpy"].sum()),
                    "direction_accuracy": float(group["direction_correct"].mean()),
                    "mean_predicted_return": float(group["predicted_return"].mean()),
                    "mean_actual_return": float(group["actual_return"].mean()),
                }
            )

    traded_all = (
        frame.loc[frame["signal"] == "BUY"] if not frame.empty else pd.DataFrame()
    )
    wins_all = (
        traded_all.loc[traded_all["net_profit_jpy"] > 0.0]
        if not traded_all.empty
        else pd.DataFrame()
    )
    losses_all = (
        traded_all.loc[traded_all["net_profit_jpy"] < 0.0]
        if not traded_all.empty
        else pd.DataFrame()
    )
    gross_win_all = (
        float(wins_all["net_profit_jpy"].sum()) if not wins_all.empty else 0.0
    )
    gross_loss_all = (
        float(-losses_all["net_profit_jpy"].sum()) if not losses_all.empty else 0.0
    )

    coefficient_changes: list[dict[str, Any]] = []
    if not coefficient_frame.empty:
        pivot = coefficient_frame.pivot_table(
            index="date", columns="feature", values="coefficient", aggfunc="mean"
        ).sort_index()
        change = pivot.diff()
        for day in pivot.index:
            for feature in pivot.columns:
                delta = change.at[day, feature]
                coefficient_changes.append(
                    {
                        "date": str(day),
                        "feature": str(feature),
                        "mean_coefficient": float(pivot.at[day, feature]),
                        "change_from_previous_day": (
                            None if pd.isna(delta) else float(delta)
                        ),
                    }
                )

    per_company = _per_company_coefficients(coefficient_frame)

    return {
        "generated_for": {
            "from": arguments.from_date.isoformat(),
            "to": arguments.to_date.isoformat(),
            "training_window_sessions": arguments.training_window,
        },
        "training": {
            "window_sessions": arguments.training_window,
            "recency_half_life_sessions": arguments.recency_half_life,
        },
        "feature_set": {
            "name": feature_set.name,
            "label": feature_set.label,
            "indicator_symbols": {
                spec.key: spec.symbol for spec in feature_set.indicators
            },
            "adr_symbols": dict(feature_set.adr_symbols),
            "extra_price_features": list(feature_set.extra_price_features),
            "feature_count": len(sorted({str(row["feature"]) for row in coefficients})),
            "missing_symbols": sorted(set(missing_series)),
        },
        "rule": {
            "return_threshold": signal_config.return_threshold,
            "probability_threshold": signal_config.probability_threshold,
            "capital_per_stock_jpy": execution_config.capital_per_stock,
            "lot_size": execution_config.lot_size,
            "commission_bps_per_side": execution_config.commission_bps,
            "slippage_bps_per_side": execution_config.slippage_bps,
        },
        "totals": {
            "predictions": len(frame),
            "buy_signals": len(traded_all),
            "wins": len(wins_all),
            "losses": len(losses_all),
            "win_rate": (len(wins_all) / len(traded_all)) if len(traded_all) else None,
            "gross_win_jpy": gross_win_all,
            "gross_loss_jpy": gross_loss_all,
            "money_win_ratio": (
                gross_win_all / gross_loss_all if gross_loss_all > 0.0 else None
            ),
            "net_profit_jpy": (
                float(traded_all["net_profit_jpy"].sum())
                if not traded_all.empty
                else 0.0
            ),
            "direction_accuracy": (
                float(frame["direction_correct"].mean()) if not frame.empty else None
            ),
        },
        "daily": daily,
        "predictions": predictions,
        "coefficient_changes": coefficient_changes,
        "company_coefficients": per_company,
        "failures": failures,
        "caveats": [
            "Yahooの非公式データを使用し、Provider品質ゲートと"
            "PIT lineageを通していません。",
            "自銘柄の価格特徴量と海外指標は1営業日ラグさせ、当日情報を使っていません。",
            "サンプルが小さいため、勝率もProfit Factorも有効性の証拠になりません。",
            "紙上シミュレーションであり、実際の約定、板、税金を再現しません。",
        ],
    }


def _per_company_coefficients(frame: pd.DataFrame) -> list[dict[str, Any]]:
    """Return each company's own coefficient per date, with its day-over-day move.

    ``first_seen`` marks the first date a feature carried a non-zero
    coefficient for that company. Regularized fits drive irrelevant features to
    exactly zero, so a feature becoming non-zero is the model starting to use
    it -- which is what "a newly appeared indicator" means here.
    """

    if frame.empty:
        return []
    records: list[dict[str, Any]] = []
    for (ticker, feature), group in frame.groupby(["ticker", "feature"], sort=True):
        ordered = group.sort_values("date")
        previous: float | None = None
        seen_active = False
        for row in ordered.to_dict("records"):
            value = float(row["coefficient"])
            active = abs(value) > 0.0
            first_seen = active and not seen_active
            seen_active = seen_active or active
            records.append(
                {
                    "date": str(row["date"]),
                    "ticker": str(ticker),
                    "feature": str(feature),
                    "coefficient": value,
                    "change_from_previous_day": (
                        None if previous is None else value - previous
                    ),
                    "first_seen": first_seen,
                    "active": active,
                }
            )
            previous = value
    return records


def _print_summary(report: dict[str, Any], output: Path) -> None:
    window = report["generated_for"]
    totals = report["totals"]
    rule = report["rule"]
    chosen = report["feature_set"]
    print(f"期間: {window['from']} 〜 {window['to']}")
    print(f"学習: 各予測日の直前 {window['training_window_sessions']} 営業日")
    print(
        f"予測要素: {chosen['name']} ({chosen['feature_count']}個) — {chosen['label']}"
    )
    print(
        "BUY条件: 予測リターン > "
        f"{rule['return_threshold'] * 100:.2f}% かつ 上昇確率 >= "
        f"{rule['probability_threshold'] * 100:.0f}%\n"
    )
    print(f"  予測件数        {totals['predictions']}")
    print(f"  BUYシグナル     {totals['buy_signals']}")
    if totals["buy_signals"]:
        print(f"  勝ち / 負け     {totals['wins']} / {totals['losses']}")
        print(f"  勝率            {(totals['win_rate'] or 0) * 100:.1f}%")
        print(f"  勝ち金額        {totals['gross_win_jpy']:>12,.0f} 円")
        print(f"  負け金額        {totals['gross_loss_jpy']:>12,.0f} 円")
        ratio = totals["money_win_ratio"]
        print(
            "  金額ベース勝率  "
            + (f"{ratio:.3f}" if ratio is not None else "負けなし: 算出不能")
        )
        print(f"  純損益          {totals['net_profit_jpy']:>12,.0f} 円")
    accuracy = totals["direction_accuracy"]
    if accuracy is not None:
        print(f"  方向的中率      {accuracy * 100:.1f}% (全予測、BUY以外も含む)")

    print("\n日別:")
    for row in report["daily"]:
        rate = row["win_rate"]
        print(
            f"  {row['date']}  予測{row['predictions']:>3}"
            f"  BUY{row['buy_signals']:>3}"
            f"  勝率 {'--' if rate is None else format(rate * 100, '5.1f') + '%'}"
            f"  純損益 {row['net_profit_jpy']:>10,.0f}円"
        )
    if report["failures"]:
        print(f"\n除外/失敗: {len(report['failures'])}銘柄")
    print(f"\n出力: {output}")
    for caveat in report["caveats"]:
        print(f"注意: {caveat}")


if __name__ == "__main__":
    raise SystemExit(main())
