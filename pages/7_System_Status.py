"""Operational status derived exclusively from persisted audit records."""

from __future__ import annotations

import streamlit as st

from dashboard.presenters import (
    derive_operational_alerts,
    format_jst,
    format_number,
    operational_counts,
    safe_text,
    string_list,
)
from dashboard.ui import (
    cached_batches,
    cached_database_health,
    cached_latest_run,
    cached_prediction_set,
    cached_raw_summary,
    cached_run_steps,
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
    configure_page("System Status", "🩺")
    render_header(
        "System Status",
        "DBに保存されたrun・Provider選択・鮮度・ingestion監査を確認します。",
    )
    service = require_service()
    if service is None:
        return

    health = cached_database_health(service)
    latest_run = cached_latest_run(service)
    prediction_set = cached_prediction_set(service)
    predictions = cached_today_predictions(service)
    selections = cached_selections(service)
    batches = cached_batches(service)
    steps = cached_run_steps(service)
    raw_summary = cached_raw_summary(service)

    run = latest_run.first
    publication = prediction_set.first
    selection_rows = selections.rows if selections.ready else ()
    prediction_rows = predictions.rows if predictions.ready else ()
    counts = operational_counts(selection_rows)
    render_alerts(
        derive_operational_alerts(
            run=run,
            prediction_set=publication,
            predictions=prediction_rows,
            selections=selection_rows,
        )
    )

    columns = st.columns(4)
    columns[0].metric("Database", "READ ONLY" if health.ready else health.state.value)
    columns[1].metric("Fallback", str(counts.fallback))
    columns[2].metric("Stale / Missing", str(counts.stale_or_missing))
    columns[3].metric("Unverified / Delayed", str(counts.unverified_or_delayed))

    if publication is not None:
        render_cutoff_summary(
            cutoff_at=publication.get("cutoff_at"),
            generated_at=publication.get("generated_at"),
            status=publication.get("status"),
            run_id=publication.get("run_id"),
        )
    elif run is not None:
        render_cutoff_summary(
            cutoff_at=run.get("cutoff_at"),
            generated_at=run.get("finished_at"),
            status=run.get("status"),
            run_id=run.get("run_id"),
        )

    st.subheader("Latest Pipeline Run")
    if render_query_state(latest_run, empty_message="pipeline runがありません。"):
        assert run is not None
        display_rows(
            [
                {
                    "Run ID": safe_text(run.get("run_id", "—")),
                    "Type": safe_text(run.get("run_type", "—")),
                    "Prediction Date": str(run.get("prediction_date", "—")),
                    "Status": safe_text(run.get("status", "—")),
                    "Step": safe_text(run.get("current_step", "—")),
                    "Started": format_jst(run.get("started_at")),
                    "Finished": format_jst(run.get("finished_at")),
                    "Failed Symbols": "、".join(string_list(run.get("failed_symbols")))
                    or "0",
                    "Data Version": safe_text(run.get("data_version", "—")),
                    "Model Version": safe_text(run.get("model_version", "—")),
                }
            ]
        )

    st.subheader("Provider Selection")
    if render_query_state(
        selections,
        empty_message="最新runのProvider選択監査がありません。",
    ):
        display_rows(
            [
                {
                    "Series": safe_text(row.get("canonical_symbol", "—")),
                    "Interval": safe_text(row.get("interval", "—")),
                    "Provider": safe_text(row.get("selected_provider", "—")),
                    "Role": safe_text(row.get("selection_role", "—")),
                    "Quality": safe_text(row.get("data_quality", "—")),
                    "Freshness": safe_text(row.get("freshness_status", "—")),
                    "Coverage": format_number(row.get("coverage"), digits=3),
                    "Cutoff": format_jst(row.get("cutoff_at")),
                }
                for row in selection_rows
            ],
            height=520,
        )

    left, right = st.columns(2)
    with left:
        st.subheader("Ingestion Batches")
        if render_query_state(batches, empty_message="ingestion batchがありません。"):
            display_rows(
                [
                    {
                        "Provider": safe_text(row.get("provider", "—")),
                        "Status": safe_text(row.get("status", "—")),
                        "Succeeded": (
                            f"{row.get('succeeded_symbols', 0)}/"
                            f"{row.get('requested_symbols', 0)}"
                        ),
                        "Inserted": row.get("inserted_rows", 0),
                        "Reused": row.get("reused_rows", 0),
                        "Failed": "、".join(string_list(row.get("failed_symbols")))
                        or "0",
                        "Finished": format_jst(row.get("finished_at")),
                    }
                    for row in batches.rows
                ]
            )
    with right:
        st.subheader("Run Steps")
        if render_query_state(steps, empty_message="run step監査がありません。"):
            display_rows(
                [
                    {
                        "Step": safe_text(row.get("step_name", "—")),
                        "Attempt": row.get("attempt_number", "—"),
                        "Status": safe_text(row.get("status", "—")),
                        "Started": format_jst(row.get("started_at")),
                        "Finished": format_jst(row.get("finished_at")),
                    }
                    for row in steps.rows
                ]
            )

    st.subheader("Raw Data Storage")
    if render_query_state(raw_summary, empty_message="raw market dataがありません。"):
        display_rows(
            [
                {
                    "Table": safe_text(row.get("source_table", "—")),
                    "Rows": row.get("row_count", 0),
                    "Last Retrieved": format_jst(row.get("last_retrieved_at")),
                    "Delayed Rows": row.get("delayed_rows", 0),
                    "Unverified Rows": row.get("unverified_rows", 0),
                }
                for row in raw_summary.rows
            ]
        )

    st.caption(
        "API connection欄はlive pingではなく、最後にDBへ保存された実行監査です。"
        "この画面を開いてもYahoo、Treasury、EODHD等へ接続しません。DB例外や接続文字列、"
        "API key、recipient等の秘密情報は表示しません。"
    )


if __name__ == "__main__":
    main()
