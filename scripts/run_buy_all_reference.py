"""Control case: buy every configured stock at the open, sell at the close.

This answers "what if I had simply bought everything?" over a date window. It is
a **reference point, not a strategy result**. There is no model and no
prediction here: every stock trades every session, so the outcome measures what
the market did, not what the system forecast.

Use it to keep filtered results honest. If the BUY rule earns less than buying
everything over the same window, the filter destroyed value; if it earns more,
the difference is the only part attributable to the model.

Prices come from Yahoo via ``yfinance``, which is unofficial and best effort.
Sessions where an Open or Close is missing are skipped rather than filled, so a
short window can legitimately return fewer sessions than you expect.

    python -m cli buy-all --from-date 2026-08-01 --to-date 2026-08-08
"""

from __future__ import annotations

import argparse
import json
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from backtest.scenario import ScenarioConfig, evaluate_scenario
from data.config import load_app_config


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
    parser.add_argument("--to-date", type=date.fromisoformat, default=today)
    parser.add_argument("--capital", type=float, default=None)
    parser.add_argument("--commission-bps", type=float, default=None)
    parser.add_argument("--slippage-bps", type=float, default=None)
    parser.add_argument("--json", action="store_true", dest="as_json")
    return parser.parse_args()


def _fetch_sessions(
    ticker: str, symbol: str, from_date: date, to_date: date
) -> list[dict[str, Any]]:
    import yfinance as yf

    history = yf.Ticker(symbol).history(
        start=from_date.isoformat(),
        # yfinance treats ``end`` as exclusive.
        end=(to_date + timedelta(days=1)).isoformat(),
        interval="1d",
        auto_adjust=False,
    )
    rows: list[dict[str, Any]] = []
    for index, row in history.iterrows():
        open_price = float(row["Open"])
        close_price = float(row["Close"])
        if open_price <= 0.0 or close_price <= 0.0:
            continue
        rows.append(
            {
                "ticker": ticker,
                "prediction_date": index.date().isoformat(),
                # No model is involved; these placeholders exist only so the
                # shared scenario engine can score an unfiltered trade.
                "predicted_return": 0.0,
                "probability_up": 1.0,
                "actual_open": open_price,
                "actual_close": close_price,
            }
        )
    return rows


def main() -> int:
    """Fetch realized prices and score the unfiltered control case."""

    arguments = _parse_arguments()
    config = load_app_config(arguments.config_dir)
    trading = config.trading

    capital = arguments.capital or _require(
        trading.position.capital_per_stock_jpy, "capital_per_stock_jpy"
    )
    commission = (
        arguments.commission_bps
        if arguments.commission_bps is not None
        else _require(trading.costs.commission_bps_per_side, "commission_bps_per_side")
    )
    slippage = (
        arguments.slippage_bps
        if arguments.slippage_bps is not None
        else _require(trading.costs.slippage_bps_per_side, "slippage_bps_per_side")
    )
    lot_size = int(_require(trading.position.lot_size, "lot_size"))

    rows: list[dict[str, Any]] = []
    failures: dict[str, str] = {}
    for stock in config.stocks.stocks:
        if not stock.enabled:
            continue
        symbol = stock.provider_symbols.get("yahoo_finance")
        if symbol is None:
            failures[stock.ticker] = "Yahoo symbol is unresolved"
            continue
        try:
            rows.extend(
                _fetch_sessions(
                    stock.ticker, symbol, arguments.from_date, arguments.to_date
                )
            )
        except Exception as error:
            failures[stock.ticker] = f"{type(error).__name__}: {str(error)[:160]}"

    outcome = evaluate_scenario(
        rows,
        ScenarioConfig.buy_everything(
            date_from=arguments.from_date.isoformat(),
            date_to=arguments.to_date.isoformat(),
            capital_per_stock=capital,
            commission_bps=commission,
            slippage_bps=slippage,
            lot_size=lot_size,
        ),
    )
    portfolio = outcome.portfolio
    executed = outcome.trades.loc[outcome.trades["selected"]]
    sessions = sorted({str(value) for value in executed["prediction_date"]})

    report = {
        "window": {
            "from": arguments.from_date.isoformat(),
            "to": arguments.to_date.isoformat(),
            "sessions": sessions,
        },
        "assumptions": {
            "capital_per_stock_jpy": capital,
            "commission_bps_per_side": commission,
            "slippage_bps_per_side": slippage,
            "lot_size": lot_size,
            "rule": "buy every stock at the open, sell at that day's close",
        },
        "result": {
            "trades": portfolio.number_of_trades,
            "wins": portfolio.wins,
            "losses": portfolio.losses,
            "win_rate": portfolio.win_rate,
            "gross_profit_jpy": portfolio.gross_profit,
            "gross_loss_jpy": portfolio.gross_loss,
            "net_profit_jpy": portfolio.net_profit,
            "average_win_jpy": portfolio.average_win,
            "average_loss_jpy": portfolio.average_loss,
            "largest_win_jpy": portfolio.largest_win,
            "largest_loss_jpy": portfolio.largest_loss,
            "profit_factor": portfolio.profit_factor,
            "expectancy_jpy": portfolio.expectancy,
            "max_drawdown": portfolio.maximum_drawdown,
        },
        "failures": failures,
        "warnings": list(outcome.warnings),
        "caveat": (
            "No model is involved. This is the unfiltered market outcome over "
            "the window, using unofficial Yahoo prices and simulated costs."
        ),
    }

    if arguments.as_json:
        print(json.dumps(report, indent=2, ensure_ascii=False))
        return 0

    print(f"期間: {arguments.from_date} 〜 {arguments.to_date}")
    print(f"営業日: {len(sessions)}日 ({', '.join(sessions) or '該当なし'})")
    print(
        f"前提: 1銘柄 {capital:,.0f}円 / {lot_size}株単元 / "
        f"手数料 {commission} bps・スリッページ {slippage} bps (各片側)"
    )
    print("ルール: 全銘柄を寄り付きで買い、同日大引けで売る: モデル判定なし\n")
    print(f"  取引数        {portfolio.number_of_trades}")
    print(f"  勝ち / 負け   {portfolio.wins} / {portfolio.losses}")
    print(f"  勝率          {portfolio.win_rate * 100:.1f}%")
    print(f"  総利益        {portfolio.gross_profit:>12,.0f} 円")
    print(f"  総損失        {portfolio.gross_loss:>12,.0f} 円")
    print(f"  純損益        {portfolio.net_profit:>12,.0f} 円")
    print(f"  Profit Factor {portfolio.profit_factor:.3f}")
    print(f"  1取引平均     {portfolio.expectancy:>12,.0f} 円")
    print(f"  最大の勝ち    {portfolio.largest_win:>12,.0f} 円")
    print(f"  最大の負け    {portfolio.largest_loss:>12,.0f} 円")
    print(f"  最大DD        {portfolio.maximum_drawdown * 100:.2f}%")
    if failures:
        print(f"\n取得失敗: {len(failures)}銘柄 -> {', '.join(sorted(failures))}")
    for warning in outcome.warnings:
        print(f"注意: {warning}")
    print(f"\n{report['caveat']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
