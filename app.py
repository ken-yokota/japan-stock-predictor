"""Read-only Streamlit entrypoint for persisted prediction results."""

from __future__ import annotations

import streamlit as st

from dashboard.presenters import format_jst
from dashboard.ui import (
    cached_database_health,
    cached_latest_run,
    cached_prediction_set,
    configure_page,
    render_header,
    render_query_state,
    require_service,
)


def main() -> None:
    configure_page("Overview", "📊")
    render_header(
        "日本株 短期予測 Dashboard",
        "08:30 JST cutoffで保存された予測・品質・監査結果を読み取り専用で表示します。",
    )
    service = require_service()
    if service is None:
        return

    health = cached_database_health(service)
    latest_run = cached_latest_run(service)
    prediction_set = cached_prediction_set(service)

    columns = st.columns(3)
    columns[0].metric(
        "Database",
        "READ ONLY" if health.ready else health.state.value,
    )
    run = latest_run.first
    columns[1].metric(
        "Latest Pipeline",
        str(run.get("status", "PENDING")) if run else "PENDING",
    )
    published = prediction_set.first
    columns[2].metric(
        "Latest Prediction",
        str(published.get("status", "PENDING")) if published else "PENDING",
    )

    st.subheader("最新の保存状態")
    if render_query_state(latest_run, empty_message="日次pipeline runがありません。"):
        assert run is not None
        st.write(
            {
                "Prediction Date": str(run.get("prediction_date", "—")),
                "Data Cutoff": format_jst(run.get("cutoff_at")),
                "Started At": format_jst(run.get("started_at")),
                "Finished At": format_jst(run.get("finished_at")),
                "Current Step": str(run.get("current_step") or "—"),
                "Run ID": str(run.get("run_id") or "—"),
            }
        )

    st.subheader("ページ")
    pages = (
        ("pages/1_Today.py", "Today", "当日の予測、BUY候補、cutoff・品質警告"),
        ("pages/2_Stock_Detail.py", "Stock Detail", "銘柄別の予測・実績履歴"),
        ("pages/3_Factor_Analysis.py", "Factor Analysis", "標準化係数とモデル感応度"),
        ("pages/4_Sector_Analysis.py", "Sector Analysis", "業種別の横断比較"),
        ("pages/5_Backtest.py", "Backtest", "OOS評価とpaper trade"),
        ("pages/6_System_Status.py", "System Status", "run、Provider、鮮度、DB状態"),
    )
    for path, label, help_text in pages:
        st.page_link(path, label=label, help=help_text, icon="➡️")

    st.divider()
    st.caption(
        "このUIは外部Providerへの接続、データ取得、特徴量生成、モデル学習、メール送信を"
        "実行しません。画面更新はDBのSELECTだけです。"
    )


if __name__ == "__main__":
    main()
