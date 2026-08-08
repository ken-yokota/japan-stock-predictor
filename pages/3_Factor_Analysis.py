"""BUY conditions and rolling indicator coefficients."""

from __future__ import annotations

import pandas as pd  # type: ignore[import-untyped]
import streamlit as st

from dashboard.catalog import stock_label
from dashboard.factors import (
    buy_rule_mismatches,
    coefficient_summary_rows,
    load_configured_buy_rule,
    summarize_coefficients,
)
from dashboard.presenters import as_number, format_jst, format_number, format_yen
from dashboard.query_service import DashboardQueryService
from dashboard.ui import (
    cached_applied_buy_thresholds,
    cached_coefficient_history,
    cached_coefficients,
    configure_page,
    display_rows,
    render_header,
    render_query_state,
    require_service,
)


def _percent(value: object, *, digits: int) -> str:
    """Format a stored 0..1 threshold as a percentage for display."""

    number = as_number(value)
    return "—" if number is None else f"{number * 100:.{digits}f}%"


_LOOKBACK_CHOICES = {
    "約6か月 (120営業日)": 120,
    "約3か月 (60営業日)": 60,
    "約1か月 (20営業日)": 20,
}


def _render_buy_rule(service: DashboardQueryService) -> None:
    """Show the BUY condition, and flag config drift from stored signals."""

    st.subheader("どういう条件で「買い」になるか")
    rule = load_configured_buy_rule()
    if rule is None:
        st.warning(
            "PENDING: config/trading.yaml を読めませんでした。"
            "下の保存済み判定条件だけを参照してください。"
        )
    else:
        st.success(f"**BUY = {rule.summary}**")
        st.caption(
            "両方を同時に満たした銘柄だけがBUYになります。片方だけではBUYになりません。"
            "0件の日も正常な結果です。"
        )
        columns = st.columns(4)
        columns[0].metric("予測リターン閾値", f"{rule.return_threshold * 100:.2f}%")
        columns[1].metric("上昇確率の下限", f"{rule.probability_threshold * 100:.0f}%")
        columns[2].metric("1銘柄あたり投資額", format_yen(rule.capital_per_stock))
        columns[3].metric("売買単位", f"{rule.lot_size}株")
        st.caption(
            f"約定前提: 手数料 {rule.commission_bps_per_side} bps  /  "
            f"スリッページ {rule.slippage_bps_per_side} bps: いずれも片側。"
            f"設定元は `{rule.source}` です。"
        )

    applied = cached_applied_buy_thresholds(service)
    if applied.ready:
        mismatches = buy_rule_mismatches(rule, applied.rows)
        if mismatches:
            st.warning(
                "保存済みの予測には、現在の設定と異なる条件で判定されたものがあります。"
                "画面の数字を解釈するときは、その予測がどの条件で作られたかを見てください。"
            )
            for message in mismatches:
                st.caption(f"・{message}")
        with st.expander("実際に適用された判定条件の履歴", expanded=False):
            display_rows(
                [
                    {
                        "予測リターン閾値": _percent(
                            row.get("return_threshold"), digits=2
                        ),
                        "上昇確率の下限": _percent(
                            row.get("probability_threshold"), digits=0
                        ),
                        "期間": f"{row.get('first_date')} 〜 {row.get('last_date')}",
                        "予測件数": row.get("prediction_count", 0),
                        "うちBUY": row.get("buy_count", 0),
                    }
                    for row in applied.rows
                ]
            )
    else:
        st.info(
            "PENDING: 保存済みの予測がまだないため、"
            "実際に適用された条件は表示できません。"
        )


def _render_rolling_coefficients(service: DashboardQueryService) -> None:
    """Summarize how each indicator's coefficient behaved over recent fits."""

    st.subheader("各指標の係数: 過去の学習まとめ")
    coefficients = cached_coefficients(service)
    if not render_query_state(
        coefficients,
        empty_message="model coefficientが未作成です。学習完了後に表示されます。",
    ):
        return

    latest_models: dict[tuple[str, str], str] = {}
    for row in coefficients.rows:
        latest_models.setdefault((str(row["ticker"]), str(row["task"])), "")

    tasks = sorted({task for _, task in latest_models})
    selectors = st.columns(3)
    task = selectors[0].radio("モデル種別", tasks, horizontal=True)
    tickers = sorted(ticker for ticker, row_task in latest_models if row_task == task)
    ticker = selectors[1].selectbox("銘柄", tickers, format_func=stock_label)
    lookback_label = selectors[2].selectbox("集計期間", list(_LOOKBACK_CHOICES))
    lookback = _LOOKBACK_CHOICES[lookback_label]

    history = cached_coefficient_history(service, ticker, task)
    if not render_query_state(
        history, empty_message="この銘柄の係数履歴がまだありません。"
    ):
        return

    report, fits_used = summarize_coefficients(history.rows, lookback=lookback)
    if not report:
        st.info("PENDING: 集計できる係数がありません。")
        return

    if fits_used < lookback:
        st.warning(
            f"この集計に使えた学習回数は {fits_used} 回で、選んだ {lookback} 回分に"
            "足りていません。回数が少ないほど平均も安定性も当てになりません。"
        )
    st.caption(
        f"{stock_label(ticker)} / {task} の直近 {fits_used} 回の学習を集計しました。"
        "係数は標準化後の値なので、同じモデル内でのみ大小を比較できます。"
    )

    summary = coefficient_summary_rows(report)
    top = summary[:15]
    if top:
        chart = pd.DataFrame(
            {
                "指標": [str(row["指標 (Feature)"]) for row in reversed(top)],
                "平均係数": [float(str(row["平均係数"])) for row in reversed(top)],
            }
        ).set_index("指標")
        st.bar_chart(chart, use_container_width=True)

    display_rows(summary, height=520)

    with st.expander("この表の読み方", expanded=False):
        st.markdown(
            """
            - **平均係数**: 直近の学習で、その指標が予測をどちらへ動かしたかの平均です。
              プラスなら上げ要因、マイナスなら下げ要因として働いていました。
            - **符号一致率**: 何%の学習で同じ向きだったか。
              100%に近いほど一貫しています。
              50%付近は、日によって向きが反転していたという意味です。
            - **安定性**: 符号の一貫性を、係数のばらつきで割り引いた0〜1の指標です。
            - **観測回数**: 集計に使えた学習回数です。少ないと平均の意味が薄くなります。

            係数が大きくても「その指標を見れば儲かる」という意味ではありません。
            同じモデル内での感応度であり、因果関係でも将来の再現性でもありません。
            符号が頻繁に反転する指標は、Readabilityスコアを下げる要因になります。
            """
        )

    model_row = history.first
    if model_row is not None:
        info = st.columns(4)
        info[0].metric("Algorithm", str(model_row.get("algorithm") or "—"))
        info[1].metric("Model Version", str(model_row.get("model_version") or "—"))
        info[2].metric("最新Training End", str(model_row.get("training_end") or "—"))
        info[3].metric("Finished", format_jst(model_row.get("finished_at")))


def _render_latest_fit(service: DashboardQueryService) -> None:
    """Show the single newest fit, for checking today's explanation."""

    coefficients = cached_coefficients(service)
    if not coefficients.ready:
        return
    st.subheader("最新の学習1回分の係数")
    latest_models: dict[tuple[str, str], str] = {}
    for row in coefficients.rows:
        key = (str(row["ticker"]), str(row["task"]))
        latest_models.setdefault(key, str(row["model_run_id"]))

    tasks = sorted({task for _, task in latest_models})
    task = st.radio("種別", tasks, horizontal=True, key="latest_fit_task")
    tickers = sorted(ticker for ticker, row_task in latest_models if row_task == task)
    ticker = st.selectbox(
        "対象銘柄", tickers, format_func=stock_label, key="latest_fit"
    )
    model_run_id = latest_models[(ticker, task)]
    selected = [
        row for row in coefficients.rows if str(row["model_run_id"]) == model_run_id
    ]
    selected.sort(
        key=lambda row: abs(as_number(row.get("coefficient")) or 0.0), reverse=True
    )
    display_rows(
        [
            {
                "Feature": str(row["feature_name"]),
                "Coefficient": format_number(row.get("coefficient"), digits=5),
                "Scaler Mean": format_number(row.get("scaler_mean"), digits=5),
                "Scaler Scale": format_number(row.get("scaler_scale"), digits=5),
            }
            for row in selected[:20]
        ],
        height=420,
    )


def main() -> None:
    configure_page("Factor Analysis", "🧭")
    render_header(
        "Factor Analysis",
        "BUY判定の条件と、各指標の係数が過去の学習でどう動いたかを表示します。",
    )
    service = require_service()
    if service is None:
        return

    _render_buy_rule(service)
    st.divider()
    _render_rolling_coefficients(service)
    st.divider()
    _render_latest_fit(service)

    st.warning(
        "係数は同一model・同一標準化条件内の感応度であり、因果関係、将来の安定性、"
        "投資収益を証明しません。係数安定性snapshotが未作成ならPENDINGとして扱ってください。"
    )


if __name__ == "__main__":
    main()
