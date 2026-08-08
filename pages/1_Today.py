"""Today's persisted prediction publication."""

from __future__ import annotations

import streamlit as st

from dashboard.catalog import stock_label
from dashboard.presenters import (
    derive_operational_alerts,
    format_percent,
    format_probability,
    today_table_rows,
)
from dashboard.ui import (
    cached_latest_run,
    cached_metrics,
    cached_prediction_set,
    cached_selections,
    cached_today_predictions,
    configure_page,
    display_rows,
    render_alerts,
    render_cutoff_summary,
    render_header,
    render_query_state,
    require_service,
)


def main() -> None:
    configure_page("Today", "🌅")
    render_header(
        "Today",
        "最新のREADY prediction setと、その08:30 JST cutoff・品質根拠を表示します。",
    )
    service = require_service()
    if service is None:
        return

    latest_run = cached_latest_run(service)
    prediction_set = cached_prediction_set(service)
    predictions = cached_today_predictions(service)
    selections = cached_selections(service)
    metrics = cached_metrics(service)

    run = latest_run.first
    publication = prediction_set.first
    prediction_rows = predictions.rows if predictions.ready else ()
    selection_rows = selections.rows if selections.ready else ()
    render_alerts(
        derive_operational_alerts(
            run=run,
            prediction_set=publication,
            predictions=prediction_rows,
            selections=selection_rows,
        )
    )

    if publication is not None:
        render_cutoff_summary(
            cutoff_at=publication.get("cutoff_at"),
            generated_at=publication.get("generated_at"),
            status=publication.get("status"),
            run_id=publication.get("run_id"),
        )

    if not render_query_state(
        predictions,
        empty_message="個別予測が未作成です。pipeline完了後に表示されます。",
    ):
        return

    metric_rows = metrics.rows if metrics.ready else ()
    table = today_table_rows(prediction_rows, metric_rows)
    buy_rows = [
        row
        for row in prediction_rows
        if str(row.get("status", "")).upper() == "SUCCESS"
        and str(row.get("signal", "")).upper() == "BUY"
        and str(row.get("prediction_set_status", "")).upper() == "READY"
    ]

    st.subheader(f"BUY候補 {len(buy_rows)}件")
    if not buy_rows:
        st.info("BUY条件を満たす公開済み銘柄はありません。0件も正常な結果です。")
    else:
        for start in range(0, min(len(buy_rows), 6), 2):
            columns = st.columns(2)
            for column, row in zip(columns, buy_rows[start : start + 2], strict=False):
                with column.container(border=True):
                    st.markdown(f"#### {stock_label(str(row['ticker']))}")
                    st.metric(
                        "予測リターン",
                        format_percent(row.get("predicted_intraday_return")),
                    )
                    st.caption(
                        "上昇確率 "
                        f"{format_probability(row.get('probability_up'))} • "
                        f"Rank {row.get('rank') or '—'} • "
                        "Feature Coverage "
                        f"{format_percent(row.get('feature_coverage'), digits=1)}"
                    )

    st.subheader("全銘柄")
    st.caption(
        "判定は丸め前の保存値で確定済みです。INSUFFICIENT_DATA・FAILEDはBUY対象外です。"
    )
    display_rows(table, height=620)

    with st.expander("品質表示の読み方"):
        st.markdown(
            """
            - **Data Cutoff**: 特徴量へ入れてよい情報の上限時刻です。
            - **FREE_UNVERIFIED / DELAYED**:
              無料・遅延データで、公式リアルタイム保証ではありません。
            - **FALLBACK**:
              Primaryが品質・coverage条件を満たさず、系列全体を代替Providerへ切替済みです。
            - **STALE / MISSING**: BUY判断を抑止すべき状態です。
            - **LOW_SAMPLE / PENDING**: 指標の母数または前提が未確定です。
            """
        )


if __name__ == "__main__":
    main()
