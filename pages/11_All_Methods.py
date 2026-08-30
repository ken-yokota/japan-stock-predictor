"""Every model family, replayed over the same window and scored side by side.

The production path still decides with Ridge and Logistic. This page exists so
that choice can eventually be argued with evidence instead of habit: the same
sessions, the same features, ten families, one table.

Reading only. Everything shown is what ``scripts.report_all_method_backtest``
wrote into ``docs/all_methods``.
"""

from __future__ import annotations

import streamlit as st

from dashboard.method_comparison import load_reports, render_report
from dashboard.ui import configure_page

configure_page("全手法の比較")
st.title("全手法の比較")
st.caption(
    "Ridge / ロジスティック / ランダムフォレスト / LightGBM / XGBoost / "
    "Lasso / ElasticNet / MLP / LSTM / Transformer を、"
    "同じ営業日・同じ特徴量で学習させ、同じルールで採点したものです。"
)

reports = load_reports()
if not reports:
    st.info(
        "PENDING: まだ比較結果がありません。"
        "python -m scripts.run_all_method_backtest を実行し、"
        "その出力を scripts.report_all_method_backtest で採点してください。"
    )
else:
    labels = [label for label, _ in reports]
    for (_, payload), tab in zip(reports, st.tabs(labels), strict=True):
        with tab:
            render_report(payload)
