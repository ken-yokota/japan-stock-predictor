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
"""

from __future__ import annotations

import argparse
import json
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import pandas as pd

from data.config import load_app_config
from features.builder import PRICE_FEATURE_COLUMNS, build_price_features
from models.base import InsufficientTrainingData, ModelTrainingConfig
from models.training import train_ticker_model
from trading.strategy import BuySignalConfig, ExecutionConfig, simulate_intraday_trade

# Overseas series that close before 08:30 JST and are free to fetch.
INDICATOR_SYMBOLS: dict[str, str] = {
    "spy": "SPY",
    "qqq": "QQQ",
    "vix": "^VIX",
    "usdjpy": "JPY=X",
    "wti": "CL=F",
    "copper": "HG=F",
    "gold": "GC=F",
}


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
        "--output",
        type=Path,
        default=Path("artifacts/week_test/latest.json"),
    )
    return parser.parse_args()


def _download(symbol: str, start: date, end: date) -> pd.DataFrame:
    import yfinance as yf

    frame = yf.Ticker(symbol).history(
        start=start.isoformat(),
        end=(end + timedelta(days=1)).isoformat(),
        interval="1d",
        auto_adjust=False,
    )
    if frame.empty:
        return pd.DataFrame()
    frame = frame.rename(
        columns={"Open": "open", "High": "high", "Low": "low", "Close": "close"}
    )
    frame["market_date"] = [index.date() for index in frame.index]
    return frame.loc[:, ["market_date", "open", "high", "low", "close"]].reset_index(
        drop=True
    )


def _indicator_features(start: date, end: date) -> pd.DataFrame:
    """Build one row per calendar date of overseas indicator returns."""

    merged: pd.DataFrame | None = None
    for name, symbol in INDICATOR_SYMBOLS.items():
        raw = _download(symbol, start, end)
        if raw.empty:
            continue
        series = raw.loc[:, ["market_date", "close"]].copy()
        series["close"] = pd.to_numeric(series["close"], errors="coerce")
        series = series.sort_values("market_date")
        series[f"{name}_return_1d"] = series["close"].pct_change(fill_method=None)
        series[f"{name}_return_5d"] = series["close"].pct_change(
            periods=5, fill_method=None
        )
        columns = ["market_date", f"{name}_return_1d", f"{name}_return_5d"]
        part = series.loc[:, columns]
        merged = (
            part
            if merged is None
            else merged.merge(part, on="market_date", how="outer")
        )
    if merged is None:
        return pd.DataFrame(columns=["market_date"])
    return merged.sort_values("market_date").reset_index(drop=True)


def _stock_frame(ticker: str, symbol: str, start: date, end: date) -> pd.DataFrame:
    raw = _download(symbol, start, end)
    if raw.empty:
        return pd.DataFrame()
    raw["ticker"] = ticker
    featured = build_price_features(
        raw, ticker_column="ticker", date_column="market_date"
    )
    featured = featured.sort_values("market_date").reset_index(drop=True)

    # The target is same-session; every predictor must come from earlier rows.
    lagged = featured.copy()
    for column in PRICE_FEATURE_COLUMNS:
        lagged[column] = featured[column].shift(1)
    lagged["prev_close"] = pd.to_numeric(featured["close"], errors="coerce").shift(1)
    opening = pd.to_numeric(featured["open"], errors="coerce")
    closing = pd.to_numeric(featured["close"], errors="coerce")
    lagged["intraday_return"] = (closing / opening.where(opening > 0.0)) - 1.0
    return lagged


def _attach_indicators(
    stock: pd.DataFrame, indicators: pd.DataFrame
) -> tuple[pd.DataFrame, list[str]]:
    if indicators.empty or len(indicators.columns) <= 1:
        return stock, []
    feature_names = [name for name in indicators.columns if name != "market_date"]
    merged = stock.merge(indicators, on="market_date", how="left")
    merged = merged.sort_values("market_date").reset_index(drop=True)
    for name in feature_names:
        # Carry the last known overseas value forward, then lag one JPX session
        # so day t can never read a value stamped on day t.
        merged[name] = merged[name].ffill().shift(1)
    return merged, feature_names


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
    )

    history_start = arguments.history_start or (
        arguments.from_date - timedelta(days=int(arguments.training_window * 2.2) + 90)
    )
    indicators = _indicator_features(history_start, arguments.to_date)

    predictions: list[dict[str, Any]] = []
    coefficients: list[dict[str, Any]] = []
    failures: dict[str, str] = {}

    for stock in config.stocks.stocks:
        if not stock.enabled:
            continue
        symbol = stock.provider_symbols.get("yahoo_finance")
        if symbol is None:
            failures[stock.ticker] = "Yahoo symbol is unresolved"
            continue
        try:
            frame = _stock_frame(stock.ticker, symbol, history_start, arguments.to_date)
        except Exception as error:
            failures[stock.ticker] = f"{type(error).__name__}: {str(error)[:160]}"
            continue
        if frame.empty:
            failures[stock.ticker] = "no price history returned"
            continue

        frame, indicator_names = _attach_indicators(frame, indicators)
        feature_names = tuple([*PRICE_FEATURE_COLUMNS, *indicator_names])

        test_positions = frame.index[
            (frame["market_date"] >= arguments.from_date)
            & (frame["market_date"] <= arguments.to_date)
        ]
        for position in test_positions:
            target_date = frame.at[position, "market_date"]
            history = frame.iloc[:position]
            usable = history.loc[history["intraday_return"].notna()]
            if len(usable) < training_config.minimum_training_sessions:
                failures.setdefault(
                    stock.ticker, f"insufficient history at {target_date}"
                )
                continue
            try:
                model = train_ticker_model(
                    stock.ticker,
                    usable.loc[:, list(feature_names)],
                    usable["intraday_return"],
                    feature_names=feature_names,
                    config=training_config,
                )
            except InsufficientTrainingData as error:
                failures.setdefault(stock.ticker, str(error)[:160])
                continue

            current = frame.iloc[[position]]
            prediction = model.predict_one(current.loc[:, list(feature_names)])
            actual_open = float(current.iloc[0]["open"])
            actual_close = float(current.iloc[0]["close"])
            previous_close = current.iloc[0]["prev_close"]
            previous_close = float(previous_close) if pd.notna(previous_close) else None
            is_buy = (
                prediction.predicted_return > signal_config.return_threshold
                and prediction.probability_up >= signal_config.probability_threshold
            )
            trade = simulate_intraday_trade(
                actual_open, actual_close, execute=is_buy, config=execution_config
            )
            actual_return = actual_close / actual_open - 1.0
            predictions.append(
                {
                    "date": target_date.isoformat(),
                    "ticker": stock.ticker,
                    "predicted_return": prediction.predicted_return,
                    "probability_up": prediction.probability_up,
                    "training_sessions": model.training_sessions,
                    "ridge_alpha": prediction.ridge_alpha,
                    "logistic_c": prediction.logistic_c,
                    # Morning view: the Open is unknown, so the reference is the
                    # previous close. Post-open view uses the realized Open.
                    "reference_close": previous_close,
                    "morning_predicted_close": (
                        previous_close * (1.0 + prediction.predicted_return)
                        if previous_close is not None
                        else None
                    ),
                    "actual_open": actual_open,
                    "post_open_predicted_close": actual_open
                    * (1.0 + prediction.predicted_return),
                    "actual_close": actual_close,
                    "actual_return": actual_return,
                    # Spec's auxiliary target: the same move stated in yen
                    # rather than as a ratio, measured from the realized Open.
                    "predicted_price_difference": actual_open
                    * prediction.predicted_return,
                    "actual_price_difference": actual_close - actual_open,
                    "signal": "BUY" if is_buy else "NO_BUY",
                    "direction_correct": bool(
                        (prediction.predicted_return > 0.0) == (actual_return > 0.0)
                    ),
                    "shares": trade.shares,
                    "gross_profit_jpy": trade.gross_profit,
                    "cost_jpy": trade.commission_cost + trade.slippage_cost,
                    "net_profit_jpy": trade.net_profit,
                }
            )
            for feature_name, value in model.regression_coefficients().items():
                coefficients.append(
                    {
                        "date": target_date.isoformat(),
                        "ticker": stock.ticker,
                        "feature": feature_name,
                        "coefficient": value,
                    }
                )

    report = _build_report(
        predictions, coefficients, arguments, signal_config, execution_config, failures
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

    return {
        "generated_for": {
            "from": arguments.from_date.isoformat(),
            "to": arguments.to_date.isoformat(),
            "training_window_sessions": arguments.training_window,
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
        "failures": failures,
        "caveats": [
            "Yahooの非公式データを使用し、Provider品質ゲートと"
            "PIT lineageを通していません。",
            "自銘柄の価格特徴量と海外指標は1営業日ラグさせ、当日情報を使っていません。",
            "サンプルが小さいため、勝率もProfit Factorも有効性の証拠になりません。",
            "紙上シミュレーションであり、実際の約定、板、税金を再現しません。",
        ],
    }


def _print_summary(report: dict[str, Any], output: Path) -> None:
    window = report["generated_for"]
    totals = report["totals"]
    rule = report["rule"]
    print(f"期間: {window['from']} 〜 {window['to']}")
    print(f"学習: 各予測日の直前 {window['training_window_sessions']} 営業日")
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
