"""Persisted OOS metrics plus interactive re-simulation of stored predictions."""

from __future__ import annotations

import pandas as pd  # type: ignore[import-untyped]
import streamlit as st

from backtest.scenario import ScenarioConfig, evaluate_scenario
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
from dashboard.query_service import DashboardQueryService
from dashboard.ui import (
    cached_metrics,
    cached_oos_scenario_rows,
    cached_trades,
    configure_page,
    display_rows,
    render_header,
    render_query_state,
    require_service,
)

_SCENARIO_COUNT_KEY = "backtest_scenarios_evaluated"


def _scenario_controls() -> ScenarioConfig:
    """Render the adjustable trading rules and return them as one config."""

    with st.form("scenario_form"):
        window = st.columns(3)
        date_from = window[0].text_input(
            "開始日 (YYYY-MM-DD、空欄で全期間)", value="", placeholder="2026-08-01"
        )
        date_to = window[1].text_input(
            "終了日 (YYYY-MM-DD、空欄で全期間)", value="", placeholder="2026-08-08"
        )
        buy_everything = window[2].checkbox(
            "全銘柄を無条件で買う: 対照ケース",
            value=False,
            help=(
                "BUY判定を無視して全予測を売買した場合の結果です。"
                "モデルの絞り込みに意味があったかを比べる基準になります。"
            ),
        )

        first = st.columns(3)
        return_threshold = first[0].number_input(
            "予測リターン閾値 (%)",
            min_value=-5.0,
            max_value=5.0,
            value=0.30,
            step=0.05,
            help="この値を上回る予測リターンだけをBUY候補にします。",
        )
        probability_threshold = first[1].slider(
            "上昇確率の下限 (%)",
            min_value=0,
            max_value=100,
            value=60,
            step=1,
        )
        top_n_raw = first[2].number_input(
            "1日のTop N: 0で制限なし",
            min_value=0,
            max_value=22,
            value=0,
            step=1,
            help="同日の候補を予測リターン降順で上位N件に絞ります。",
        )

        second = st.columns(3)
        capital = second[0].number_input(
            "1銘柄あたり投資額 (円)",
            min_value=100_000,
            max_value=50_000_000,
            value=1_000_000,
            step=100_000,
        )
        commission_bps = second[1].number_input(
            "手数料 (bps / 片側)",
            min_value=0.0,
            max_value=100.0,
            value=5.0,
            step=0.5,
        )
        slippage_bps = second[2].number_input(
            "スリッページ (bps / 片側)",
            min_value=0.0,
            max_value=100.0,
            value=5.0,
            step=0.5,
        )
        submitted = st.form_submit_button("この条件で再計算", use_container_width=True)

    if submitted:
        st.session_state[_SCENARIO_COUNT_KEY] = (
            int(st.session_state.get(_SCENARIO_COUNT_KEY, 0)) + 1
        )
    window_from = date_from.strip() or None
    window_to = date_to.strip() or None
    shared: dict[str, object] = {
        "capital_per_stock": float(capital),
        "commission_bps": float(commission_bps),
        "slippage_bps": float(slippage_bps),
        "top_n": None if int(top_n_raw) == 0 else int(top_n_raw),
        "date_from": window_from,
        "date_to": window_to,
    }
    if buy_everything:
        return ScenarioConfig.buy_everything(
            date_from=window_from,
            date_to=window_to,
            capital_per_stock=float(capital),
            commission_bps=float(commission_bps),
            slippage_bps=float(slippage_bps),
            top_n=None if int(top_n_raw) == 0 else int(top_n_raw),
        )
    return ScenarioConfig(
        return_threshold=float(return_threshold) / 100.0,
        probability_threshold=float(probability_threshold) / 100.0,
        **shared,  # type: ignore[arg-type]
    )


def _render_scenario(service: DashboardQueryService) -> None:
    scenario_result = cached_oos_scenario_rows(service)
    st.subheader("条件を変えてOOSを再計算")
    st.caption(
        "保存済みのwalk-forward予測、つまり1営業日先だけを予測した結果に、"
        "下の売買条件を当てはめて再集計します。モデルや学習期間の変更は"
        "予測そのものを作り直す必要があるため、この画面では変更できません。"
        "その場合は `python -m cli walk-forward` を実行してください。"
    )
    if not render_query_state(
        scenario_result,
        empty_message="再計算できる確定済みOOS結果がまだありません。",
    ):
        return

    config = _scenario_controls()
    outcome = evaluate_scenario(
        list(scenario_result.rows),
        config,
        scenarios_evaluated=int(st.session_state.get(_SCENARIO_COUNT_KEY, 1)) or 1,
    )
    for warning in outcome.warnings:
        st.warning(warning)

    portfolio = outcome.portfolio
    columns = st.columns(4)
    columns[0].metric("Trades", str(portfolio.number_of_trades))
    columns[1].metric("Win Rate", format_probability(portfolio.win_rate))
    columns[2].metric("Net Profit", format_yen(portfolio.net_profit))
    columns[3].metric("Profit Factor", format_number(portfolio.profit_factor))

    second_row = st.columns(4)
    second_row[0].metric("Expectancy / trade", format_yen(portfolio.expectancy))
    second_row[1].metric("Sharpe", format_number(portfolio.sharpe_ratio))
    second_row[2].metric("Sortino", format_number(portfolio.sortino_ratio))
    second_row[3].metric("Max Drawdown", format_percent(portfolio.maximum_drawdown))

    third_row = st.columns(4)
    third_row[0].metric(
        "Direction Accuracy", format_probability(portfolio.direction_accuracy)
    )
    third_row[1].metric("Pearson", format_number(portfolio.pearson_correlation))
    third_row[2].metric("MAE", format_percent(portfolio.mean_absolute_error))
    third_row[3].metric("RMSE", format_percent(portfolio.root_mean_squared_error))

    if not outcome.daily_returns.empty:
        equity = (1.0 + outcome.daily_returns).cumprod()
        st.line_chart(
            pd.DataFrame({"OOS Equity (1銘柄=1.0起点)": equity}),
            use_container_width=True,
        )

    if not outcome.per_ticker.empty:
        with st.expander("銘柄別の再計算結果", expanded=False):
            display_rows(
                [
                    {
                        "銘柄": stock_label(str(row["ticker"])),
                        "Trades": int(row["number_of_trades"]),
                        "Win Rate": format_probability(row["win_rate"]),
                        "Net Profit": format_yen(row["net_profit"]),
                        "Profit Factor": format_number(row["profit_factor"]),
                        "Expectancy": format_yen(row["expectancy"]),
                        "Max Drawdown": format_percent(row["maximum_drawdown"]),
                        "MAE": format_percent(row["mean_absolute_error"]),
                    }
                    for row in outcome.per_ticker.to_dict("records")
                ],
                height=420,
            )

    executed = outcome.trades.loc[outcome.trades["selected"]]
    if not executed.empty:
        with st.expander("再計算されたtrade明細", expanded=False):
            display_rows(
                [
                    {
                        "日付": str(row["prediction_date"]),
                        "銘柄": stock_label(str(row["ticker"])),
                        "Rank": row["rank"],
                        "予測Return": format_percent(row["predicted_return"]),
                        "実績Return": format_percent(row["actual_return"]),
                        "Shares": int(row["shares"]),
                        "Net Profit": format_yen(row["net_profit"]),
                    }
                    for row in executed.tail(300).to_dict("records")
                ],
                height=420,
            )

    st.caption(
        f"対象 {outcome.rows_considered} 予測 / 除外 {outcome.rows_skipped} 件。"
        "コスト・約定前提はすべて仮定であり、実際の約定を再現しません。"
    )


def _render_persisted(service: DashboardQueryService) -> None:
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

    saved_tab, scenario_tab = st.tabs(["保存済みOOS結果", "条件を変えて再計算"])
    with saved_tab:
        _render_persisted(service)
    with scenario_tab:
        _render_scenario(service)

    st.caption(
        "評価値はOut-of-Sample結果だけで解釈してください。LOW_SAMPLE、取引0件、"
        "設定変更後のselection biasを必ず確認してください。"
    )


if __name__ == "__main__":
    main()
