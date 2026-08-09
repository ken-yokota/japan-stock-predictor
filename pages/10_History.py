"""What the system actually predicted, and what happened next.

This is the live record: predictions the morning pipeline published and the
closes that later settled them. The Test page answers "how did this idea do on
past data"; this page answers "how is the thing that is running doing". They
are drawn by the same code from the same report shape so the two can be
compared directly rather than through two different layouts.

Reads persisted rows only. A day whose close has not been observed yet is shown
with its prediction and no outcome, never with a guessed one.
"""

from __future__ import annotations

from datetime import date, timedelta

import streamlit as st

from dashboard.history import build_history_report
from dashboard.report_view import render_report
from dashboard.ui import (
    cached_prediction_history_window,
    configure_page,
    render_header,
    render_query_state,
    require_service,
)

# Longest window first is tempting, but a reader opening this page wants the
# most recent days; the widest view is one tab away.
WINDOWS: tuple[tuple[str, int | None], ...] = (
    ("直近1週間", 7),
    ("直近1ヶ月", 31),
    ("全期間", None),
)


def main() -> None:
    configure_page("実績", "📊")
    render_header(
        "実績",
        "本番pipelineが公開した予測と、その後に観測された実績です。",
    )

    service = require_service()
    if service is None:
        return

    st.caption(
        "研究用の検証(テストページ)とは別物です。こちらは実際に動いたシステムの記録で、"
        "同じ表の作りで並べてあるので、検証結果と直接見比べられます。"
    )

    today = date.today()
    for tab, (label, days) in zip(
        st.tabs([label for label, _ in WINDOWS]), WINDOWS, strict=True
    ):
        with tab:
            since = (
                (today - timedelta(days=days)).isoformat() if days is not None else None
            )
            result = cached_prediction_history_window(service, since)
            if not render_query_state(
                result,
                empty_message=(
                    "この期間に公開された予測がありません。"
                    "朝のpipelineが動くと、ここに積み上がっていきます。"
                ),
            ):
                continue
            report = build_history_report([dict(row) for row in result.rows])

            render_report(report, f"history_{label}")


if __name__ == "__main__":
    main()
