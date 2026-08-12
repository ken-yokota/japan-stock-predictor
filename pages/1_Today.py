"""Today's persisted prediction publication."""

from __future__ import annotations

import streamlit as st

from dashboard.catalog import stock_label
from dashboard.completeness import (
    NORMAL,
    UNKNOWN,
    WARNING,
    stock_from_details,
    summarise,
)
from dashboard.presenters import (
    derive_operational_alerts,
    format_percent,
    format_probability,
    today_table_rows,
)
from dashboard.ui import (
    cached_feature_completeness,
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

    # Data completeness sits above the candidates on purpose: a BUY built on an
    # incomplete input set has to be readable as such before it is read as a
    # recommendation.
    completeness = cached_feature_completeness(service)
    signals = {
        str(row.get("ticker")): str(row.get("signal") or "")
        for row in prediction_rows
    }
    coverages = {
        str(row.get("ticker")): row.get("feature_coverage") for row in prediction_rows
    }
    quality = summarise(
        [
            stock_from_details(
                str(row.get("ticker")),
                row.get("details"),
                feature_coverage=coverages.get(str(row.get("ticker"))),
                signal=signals.get(str(row.get("ticker")), ""),
            )
            for row in (completeness.rows if completeness.ready else ())
        ]
    )
    if quality.stock_count:
        status_text = {
            NORMAL: "NORMAL — 必須指標はすべて揃っています",
            WARNING: "WARNING — 必須指標が欠けた銘柄があります",
            UNKNOWN: "UNKNOWN — 欠損記録の導入前で、完全性を確認できません",
        }[quality.data_status]
        st.subheader("本日のデータ品質")
        top = st.columns(4)
        top[0].metric("対象銘柄", quality.stock_count)
        top[1].metric("CLEAN", quality.clean_count)
        top[2].metric("⚠ DEGRADED", quality.degraded_count)
        top[3].metric("UNKNOWN", quality.unknown_count)
        bottom = st.columns(3)
        bottom[0].metric("BUY候補", quality.buy_count)
        bottom[1].metric("CLEAN BUY", quality.clean_buy_count)
        bottom[2].metric("⚠ DEGRADED BUY", quality.degraded_buy_count)
        if quality.data_status == WARNING:
            st.warning(status_text)
        elif quality.data_status == UNKNOWN:
            st.info(status_text)
        else:
            st.success(status_text)

        if quality.degraded_buy_count:
            names = "、".join(
                f"{stock_label(item.ticker)}（{'・'.join(item.missing_required)}）"
                for item in quality.degraded_buys
            )
            st.warning(
                "⚠ DEGRADED BUY: BUY条件は満たしていますが、必須の海外指標が"
                f"欠けた状態で作られた予測です — {names}"
            )

        quality_table = [
            {
                "銘柄": stock_label(item.ticker),
                "状態": item.label,
                "Indicator Coverage": format_percent(item.indicator_coverage, digits=1),
                "Feature Coverage": format_percent(item.feature_coverage, digits=1),
                "欠損(必須)": "、".join(item.missing_required) or "—",
                "欠損(任意)": "、".join(item.missing_optional) or "—",
                "シグナル": item.signal or "—",
            }
            for item in quality.stocks
        ]
        with st.expander("銘柄別のデータ品質", expanded=bool(quality.degraded_count)):
            display_rows(quality_table, height=420)
            if quality.missing_required_ranking:
                st.caption("欠損した必須指標（影響銘柄数の多い順）")
                display_rows(
                    [
                        {"指標": name, "区分": "REQUIRED", "影響銘柄数": count}
                        for name, count in quality.missing_required_ranking
                    ],
                    height=200,
                )
            if quality.hidden_by_feature_coverage:
                st.caption(
                    "Feature Coverage が100%でも Indicator Coverage が100%未満の銘柄: "
                    + "、".join(quality.hidden_by_feature_coverage)
                )

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
            - **Indicator Coverage**: その銘柄に本来必要な海外指標のうち、実際に
              揃っていた割合です。設定から決まるので、まったく取得できなかった
              指標も「欠けている」と数えます。
            - **Feature Coverage**: 生成できた特徴量のうち値があった割合です。
              **必要指標全体の完全性とは別の指標**で、取得できなかった指標は
              分母に入りません。
            - **CLEAN / ⚠ DEGRADED / UNKNOWN**: 必須指標が揃っていたか、欠けて
              いたか、記録自体が無いか。UNKNOWNは「欠損なし」ではありません。
            """
        )


if __name__ == "__main__":
    main()
