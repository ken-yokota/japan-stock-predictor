"""Persisted out-of-sample metrics and simulated trades."""

from __future__ import annotations

import pandas as pd  # type: ignore[import-untyped]
import streamlit as st

from dashboard.catalog import stock_label
from dashboard.presenters import (
    as_number,
    format_jst,
    format_number,
    format_percent,
    format_probability,
    format_yen,
    latest_by,
    safe_text,
)
from dashboard.ui import (
    cached_metrics,
    cached_trades,
    configure_page,
    display_rows,
    render_header,
    render_query_state,
    require_service,
)


def main() -> None:
    configure_page("Backtest", "🧪")
    render_header(
        "Backtest",
        "Walk-forward OOS metricとpaper-only simulated tradeを表示します。",
    )
    st.warning(
        "すべてシミュレーションです。実注文ではありません。手数料・スリッページ・"
        "売買単位がPENDINGの場合、Net Profitを確定値として表示しません。"
    )
    service = require_service()
    if service is None:
        return

    metrics_result = cached_metrics(service)
    trades_result = cached_trades(service)
    metrics_ok = render_query_state(
        metrics_result,
        empty_message="OOS metric snapshotがありません。",
    )
    trades_ok = render_query_state(
        trades_result,
        empty_message="simulated tradeがありません。",
    )
    if not metrics_ok and not trades_ok:
        return

    latest_metrics = latest_by(
        metrics_result.rows if metrics_ok else (),
        identity="ticker",
    )
    metric_rows = list(latest_metrics.values())
    trade_rows = list(trades_result.rows if trades_ok else ())
    cost_pending = not trade_rows or any(
        (
            str(row.get("status", "")).upper() in {"PENDING", "INSUFFICIENT_CONFIG"}
            or row.get("commission_cost_jpy") is None
            or row.get("slippage_cost_jpy") is None
        )
        for row in trade_rows
    )
    if trade_rows and cost_pending:
        st.error(
            "PENDING: コスト前提が不足するtradeがあります。Net Profitの集計を売買判断に"
            "使用しないでください。"
        )

    total_trades = sum(
        int(as_number(row.get("trade_count")) or 0) for row in metric_rows
    )
    sufficient = sum(
        str(row.get("sample_status", "")).upper() == "SUFFICIENT" for row in metric_rows
    )
    net_values = [
        value
        for value in (as_number(row.get("net_profit_jpy")) for row in metric_rows)
        if value is not None
    ]
    drawdowns = [
        value
        for value in (as_number(row.get("max_drawdown")) for row in metric_rows)
        if value is not None
    ]
    columns = st.columns(4)
    columns[0].metric("OOS Trades", str(total_trades))
    columns[1].metric("Sufficient Samples", f"{sufficient}/{len(metric_rows)}")
    columns[2].metric(
        "Net Profit",
        "PENDING" if cost_pending else format_yen(sum(net_values)),
    )
    columns[3].metric(
        "Worst Max Drawdown",
        format_percent(min(drawdowns) if drawdowns else None),
    )

    if metric_rows:
        st.subheader("銘柄別OOS指標")
        profit_frame = pd.DataFrame(
            {
                "銘柄": [stock_label(str(row["ticker"])) for row in metric_rows],
                "Net Profit (JPY)": [
                    as_number(row.get("net_profit_jpy")) for row in metric_rows
                ],
            }
        ).set_index("銘柄")
        if not cost_pending:
            st.bar_chart(profit_frame, use_container_width=True)
        display_rows(
            [
                {
                    "銘柄": stock_label(str(row["ticker"])),
                    "As Of": str(row.get("as_of_date")),
                    "Status": safe_text(row.get("status", "—")),
                    "Sample": safe_text(row.get("sample_status", "—")),
                    "Trades": row.get("trade_count", 0),
                    "Win Rate": format_probability(row.get("win_rate")),
                    "Profit Factor": format_number(row.get("profit_factor")),
                    "Expectancy": format_yen(row.get("expectancy_jpy")),
                    "Net Profit": (
                        "PENDING"
                        if cost_pending
                        else format_yen(row.get("net_profit_jpy"))
                    ),
                    "Sharpe": format_number(row.get("sharpe_ratio")),
                    "Sortino": format_number(row.get("sortino_ratio")),
                    "Max Drawdown": format_percent(row.get("max_drawdown")),
                    "Readability": format_number(
                        row.get("readability_score"), digits=1
                    ),
                }
                for row in metric_rows
            ],
            height=520,
        )

    if trade_rows:
        st.subheader("Paper Trade History")
        display_rows(
            [
                {
                    "日付": str(row.get("prediction_date")),
                    "銘柄": stock_label(str(row.get("ticker", ""))),
                    "状態": safe_text(row.get("status", "—")),
                    "Simulated": bool(row.get("is_simulated")),
                    "Shares": row.get("shares", 0),
                    "Entry": format_number(row.get("entry_price")),
                    "Exit": format_number(row.get("exit_price")),
                    "Net Profit": (
                        "PENDING"
                        if row.get("commission_cost_jpy") is None
                        or row.get("slippage_cost_jpy") is None
                        else format_yen(row.get("net_profit_jpy"))
                    ),
                    "Return": format_percent(row.get("realized_return")),
                    "Opened": format_jst(row.get("opened_at")),
                    "Closed": format_jst(row.get("closed_at")),
                }
                for row in trade_rows
            ],
            height=520,
        )

    st.caption(
        "評価値はOut-of-Sample結果だけで解釈してください。LOW_SAMPLE、取引0件、"
        "設定変更後のselection biasを必ず確認してください。"
    )


if __name__ == "__main__":
    main()
