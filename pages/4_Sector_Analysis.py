"""Cross-sectional sector summary for the latest prediction set."""

from __future__ import annotations

import pandas as pd  # type: ignore[import-untyped]
import streamlit as st

from dashboard.presenters import (
    derive_operational_alerts,
    format_number,
    format_percent,
    format_probability,
    sector_rows,
)
from dashboard.query_service import DashboardQueryService
from dashboard.sector_history import sector_timeseries
from dashboard.ui import (
    cached_latest_run,
    cached_metrics,
    cached_oos_scenario_rows,
    cached_prediction_set,
    cached_selections,
    cached_today_predictions,
    configure_page,
    display_rows,
    render_alerts,
    render_header,
    render_query_state,
    require_service,
)


def main() -> None:
    configure_page("Sector Analysis", "🏭")
    render_header(
        "Sector Analysis",
        "最新予測を海運・エネルギー・自動車・金融・商社で集約します。",
    )
    service = require_service()
    if service is None:
        return

    predictions = cached_today_predictions(service)
    if not render_query_state(
        predictions,
        empty_message="集約対象の予測がありません。",
    ):
        return
    metrics = cached_metrics(service)
    selections = cached_selections(service)
    latest_run = cached_latest_run(service)
    prediction_set = cached_prediction_set(service)
    render_alerts(
        derive_operational_alerts(
            run=latest_run.first,
            prediction_set=prediction_set.first,
            predictions=predictions.rows,
            selections=selections.rows if selections.ready else (),
        )
    )

    aggregates = sector_rows(
        predictions.rows,
        metrics.rows if metrics.ready else (),
    )
    chart = pd.DataFrame(
        {
            "業種": [str(row["業種"]) for row in aggregates],
            "平均予測リターン (%)": [
                (float(value) * 100 if value is not None else None)
                for value in (row["平均予測リターン"] for row in aggregates)
            ],
        }
    ).set_index("業種")
    st.subheader("業種別 平均予測リターン")
    st.bar_chart(chart, use_container_width=True)

    display_rows(
        [
            {
                "業種": row["業種"],
                "銘柄数": row["銘柄数"],
                "SUCCESS": row["SUCCESS"],
                "BUY": row["BUY"],
                "平均予測リターン": format_percent(row["平均予測リターン"]),
                "平均上昇確率": format_probability(row["平均上昇確率"]),
                "平均Readability": format_number(row["平均Readability"], digits=1),
            }
            for row in aggregates
        ]
    )

    st.caption(
        "単純平均です。業種ごとの銘柄数、欠損、Provider、学習期間が異なる場合は"
        "直接比較できません。業種平均は個別銘柄の売買推奨ではありません。"
    )

    st.divider()
    _render_timeseries(service)


def _render_timeseries(service: DashboardQueryService) -> None:
    """Predicted vs realised sector average over the settled sessions."""

    st.subheader("業種別 予測平均と実績平均の推移")
    scenario = cached_oos_scenario_rows(service)
    if not scenario.ready:
        st.info("実績を読み取れないため推移を表示できません。")
        return
    series = sector_timeseries(scenario.rows)
    if not series:
        st.info(
            "確定した実績がまだありません。大引け後の答え合わせが済んだ日から"
            "推移が表示されます。"
        )
        return

    sectors = sorted({day.sector for day in series})
    sessions = sorted({day.date for day in series})
    st.caption(
        f"確定済み {len(sessions)}営業日 / {len(sectors)}業種。"
        "実績は寄り付き→大引け（close/open-1）で、予測と同じ量です。"
        "前日終値からの騰落とは異なります。"
    )

    selected = st.selectbox("業種", sectors, index=0)
    days = [day for day in series if day.sector == selected]
    st.line_chart(
        pd.DataFrame(
            {
                "日付": [day.date for day in days],
                "予測平均 (%)": [day.predicted_mean * 100 for day in days],
                "実績平均 (%)": [day.actual_mean * 100 for day in days],
            }
        ).set_index("日付"),
        use_container_width=True,
    )

    agreed = sum(
        1 for day in days if (day.predicted_mean >= 0.0) == (day.actual_mean >= 0.0)
    )
    columns = st.columns(4)
    columns[0].metric("営業日数", len(days))
    columns[1].metric(
        "予測平均", format_percent(sum(d.predicted_mean for d in days) / len(days))
    )
    columns[2].metric(
        "実績平均", format_percent(sum(d.actual_mean for d in days) / len(days))
    )
    columns[3].metric("方向一致", f"{agreed}/{len(days)}")

    with st.expander("全業種の平均を比較する", expanded=False):
        display_rows(
            [
                {
                    "業種": sector,
                    "営業日数": len(group),
                    "予測平均": format_percent(
                        sum(day.predicted_mean for day in group) / len(group)
                    ),
                    "実績平均": format_percent(
                        sum(day.actual_mean for day in group) / len(group)
                    ),
                }
                for sector in sectors
                if (group := [day for day in series if day.sector == sector])
            ]
        )

    with st.expander(f"{selected} の日次内訳", expanded=False):
        display_rows(
            [
                {
                    "日付": day.date,
                    "銘柄数": day.count,
                    "予測平均": format_percent(day.predicted_mean),
                    "実績平均": format_percent(day.actual_mean),
                }
                for day in reversed(days)
            ]
        )

    st.caption(
        "業種平均どうしの比較です。銘柄数が業種ごとに異なるため、方向一致率は"
        "個別銘柄の的中率ではありません。売買判断の根拠には使えません。"
    )


if __name__ == "__main__":
    main()
