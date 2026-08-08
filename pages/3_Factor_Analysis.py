"""Latest persisted linear-model coefficients."""

from __future__ import annotations

import pandas as pd  # type: ignore[import-untyped]
import streamlit as st

from dashboard.catalog import stock_label
from dashboard.presenters import as_number, format_jst, format_number
from dashboard.ui import (
    cached_coefficients,
    configure_page,
    display_rows,
    render_header,
    render_query_state,
    require_service,
)


def main() -> None:
    configure_page("Factor Analysis", "🧭")
    render_header(
        "Factor Analysis",
        "最新のSUCCESS model runに保存された標準化係数を表示します。",
    )
    service = require_service()
    if service is None:
        return

    coefficients = cached_coefficients(service)
    if not render_query_state(
        coefficients,
        empty_message="model coefficientが未作成です。学習完了後に表示されます。",
    ):
        return

    latest_models: dict[tuple[str, str], str] = {}
    for row in coefficients.rows:
        key = (str(row["ticker"]), str(row["task"]))
        latest_models.setdefault(key, str(row["model_run_id"]))

    tasks = sorted({task for _, task in latest_models})
    task = st.radio("Model task", tasks, horizontal=True)
    tickers = sorted(ticker for ticker, row_task in latest_models if row_task == task)
    ticker = st.selectbox("銘柄", tickers, format_func=stock_label)
    model_run_id = latest_models[(ticker, task)]
    selected = [
        row for row in coefficients.rows if str(row["model_run_id"]) == model_run_id
    ]
    selected.sort(
        key=lambda row: abs(as_number(row.get("coefficient")) or 0.0),
        reverse=True,
    )
    top = selected[:20]

    model = selected[0]
    columns = st.columns(4)
    columns[0].metric("Algorithm", str(model.get("algorithm") or "—"))
    columns[1].metric("Model Version", str(model.get("model_version") or "—"))
    columns[2].metric("Training End", str(model.get("training_end") or "—"))
    columns[3].metric("Finished", format_jst(model.get("finished_at")))

    st.subheader("絶対値上位20係数")
    frame = pd.DataFrame(
        {
            "Feature": [str(row["feature_name"]) for row in reversed(top)],
            "Coefficient": [as_number(row.get("coefficient")) for row in reversed(top)],
        }
    ).set_index("Feature")
    st.bar_chart(frame, use_container_width=True)

    display_rows(
        [
            {
                "Feature": str(row["feature_name"]),
                "Coefficient": format_number(row.get("coefficient"), digits=5),
                "Scaler Mean": format_number(row.get("scaler_mean"), digits=5),
                "Scaler Scale": format_number(row.get("scaler_scale"), digits=5),
            }
            for row in top
        ],
        height=520,
    )

    st.warning(
        "係数は同一model・同一標準化条件内の感応度であり、因果関係、将来の安定性、"
        "投資収益を証明しません。係数安定性snapshotが未作成ならPENDINGとして扱ってください。"
    )


if __name__ == "__main__":
    main()
