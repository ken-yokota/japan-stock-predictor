"""Per-stock prediction, outcome and metric history."""

from __future__ import annotations

import pandas as pd  # type: ignore[import-untyped]
import streamlit as st

from dashboard.catalog import stock_label
from dashboard.presenters import (
    as_number,
    format_jst,
    format_number,
    format_percent,
    format_percent_range,
    format_probability,
    buy_hit_ratio,
    latest_by,
    outcome_table_rows,
    safe_text,
    string_list,
)
from dashboard.ui import (
    cached_actual_results,
    cached_metrics,
    cached_prediction_history,
    configure_page,
    display_rows,
    render_header,
    render_query_state,
    require_service,
)


def _hit_text(predicted: float | None, observed: float | None) -> str:
    """Direction agreement, blank while the session is unsettled."""

    if predicted is None or observed is None:
        return "—"
    if observed == 0:
        return "±0"
    return "的中" if (predicted > 0) == (observed > 0) else "外れ"


def main() -> None:
    configure_page("Stock Detail", "🔎")
    render_header(
        "Stock Detail",
        "銘柄別に、保存済み予測と後日確定した実績を時系列で比較します。",
    )
    service = require_service()
    if service is None:
        return

    history = cached_prediction_history(service)
    if not render_query_state(history, empty_message="予測履歴がありません。"):
        return

    tickers = sorted({str(row["ticker"]) for row in history.rows})
    ticker = st.selectbox(
        "銘柄",
        tickers,
        format_func=stock_label,
    )
    selected = [row for row in history.rows if str(row["ticker"]) == ticker]
    actuals = cached_actual_results(service)
    latest_actual = latest_by(
        actuals.rows if actuals.ready else (),
        identity="prediction_id",
    )
    metrics = cached_metrics(service)
    latest_metrics = latest_by(
        metrics.rows if metrics.ready else (),
        identity="ticker",
    )
    metric = latest_metrics.get(ticker, {})

    current = selected[0]
    columns = st.columns(4)
    columns[0].metric(
        "最新予測リターン",
        format_percent(current.get("predicted_intraday_return")),
    )
    columns[1].metric(
        "上昇確率",
        format_probability(current.get("probability_up")),
    )
    columns[2].metric("Signal", str(current.get("signal") or "NONE"))
    columns[3].metric("OOS取引数", str(metric.get("trade_count", "PENDING")))

    chart_rows: list[dict[str, object]] = []
    table_rows: list[dict[str, object]] = []
    # Oldest first. The chart reads left to right in time; the table below is
    # reversed at render so the newest day is on top.
    for prediction in selected:
        prediction_id = str(prediction["prediction_id"])
        actual = latest_actual.get(prediction_id, {})
        predicted = as_number(prediction.get("predicted_intraday_return"))
        observed = as_number(actual.get("actual_intraday_return"))
        # Plotted as percentages: a y-axis in raw ratios puts every series in
        # the third decimal place and reads as a flat line.
        chart_rows.append(
            {
                "日付": str(prediction.get("prediction_date")),
                "予測リターン(%)": None if predicted is None else predicted * 100,
                "実績リターン(%)": None if observed is None else observed * 100,
            }
        )
        table_rows.append(
            {
                "日付": str(prediction.get("prediction_date")),
                "Cutoff": format_jst(prediction.get("cutoff_at")),
                "予測状態": safe_text(prediction.get("status", "—")),
                "予測リターン": format_percent(predicted),
                "実績リターン": format_percent(observed) if observed is not None else "—",
                "方向": _hit_text(predicted, observed),
                "予測区間": format_percent_range(
                    prediction.get("prediction_interval_low"),
                    prediction.get("prediction_interval_high"),
                ),
                "上昇確率": format_probability(prediction.get("probability_up")),
                "Signal": safe_text(prediction.get("signal", "—")),
                "Feature Coverage": format_percent(
                    prediction.get("feature_coverage"), digits=1
                ),
                "Positive Factors": "、".join(
                    string_list(prediction.get("positive_factors"))
                )
                or "—",
                "Negative Factors": "、".join(
                    string_list(prediction.get("negative_factors"))
                )
                or "—",
                "実績状態": safe_text(actual.get("status", "PENDING")),
            }
        )

    st.subheader("予測と実績")
    if any(row["実績リターン(%)"] is not None for row in chart_rows):
        frame = pd.DataFrame(chart_rows).set_index("日付")
        st.line_chart(frame, use_container_width=True)
        st.caption(
            "縦軸は%。実績が未確定の日は線が途切れます（0として描きません）。"
        )
    else:
        st.info("PENDING: 確定した実績リターンがまだありません。")
    display_rows(list(reversed(table_rows)), height=420)

    # The same builder the Today page and History use, so one prediction reads
    # identically wherever it is opened from.
    record = outcome_table_rows([dict(row) for row in selected])
    st.subheader(f"この銘柄の予測と結果 {len(record)}件")
    st.caption(
        "公開された予測を新しい順に並べています。"
        "「方向」は予測の符号が実績と一致したかどうかで、"
        "実績が未確定の日は空欄です。"
    )
    display_rows(list(reversed(record)), height=420)

    positive, issued, unsettled = buy_hit_ratio([dict(row) for row in selected])
    st.metric("買いの的中", f"{positive}/{issued}")
    st.caption(
        "実際にプラスになった日 / 買いシグナルが出た日。"
        "分母は出したシグナル全部で、未確定の日も含みます"
        + (f"（未確定 {unsettled}日）" if unsettled else "")
        + "。確定分だけを分母にすると、引けが取れていない日の分だけ"
        "見かけの成績が良くなります。"
    )

    buys = outcome_table_rows([dict(row) for row in selected], buy_only=True)
    with st.expander(f"BUYを出した日だけ {len(buys)}件"):
        if buys:
            display_rows(list(reversed(buys)), height=320)
        else:
            st.info("この銘柄でBUYを出した日はまだありません。")

    st.subheader("最新OOS評価")
    if metric:
        display_rows(
            [
                {
                    "As Of": str(metric.get("as_of_date")),
                    "Status": safe_text(metric.get("status", "—")),
                    "Sample": safe_text(metric.get("sample_status", "—")),
                    "Win Rate": format_probability(metric.get("win_rate")),
                    "Profit Factor": format_number(metric.get("profit_factor")),
                    "Expectancy": format_number(metric.get("expectancy_jpy")),
                    "Max Drawdown": format_percent(metric.get("max_drawdown")),
                    "Pearson": format_number(metric.get("pearson_correlation")),
                    "Spearman": format_number(metric.get("spearman_correlation")),
                    "Readability": format_number(
                        metric.get("readability_score"), digits=1
                    ),
                }
            ]
        )
    else:
        st.info("PENDING: この銘柄のmetric snapshotはまだありません。")

    st.caption(
        "少数日の一致は再現性を意味しません。全OOS期間、取引件数、最大損失、"
        "Provider品質を合わせて確認してください。"
    )


if __name__ == "__main__":
    main()
