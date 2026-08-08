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
from dashboard.ui import (
    cached_latest_run,
    cached_metrics,
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


if __name__ == "__main__":
    main()
