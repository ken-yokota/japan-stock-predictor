"""Research test results, one tab per tested window.

This page reads artifacts produced by ``python -m cli week-test``. It does not
recompute anything: the numbers shown are exactly what those runs wrote, so the
page and the artifacts cannot disagree.

Every ``*.json`` under ``artifacts/week_test/`` becomes a tab, ordered by start
date, so generating a new window is enough to make it appear here. Each tab is
a separate run over a separate window; a longer window is a larger sample, not a
superset of the shorter ones' numbers.

The windows tested here are research estimates, not a live track record. They
run outside the database pipeline, so they skip the provider quality gates and
point-in-time lineage that the production path enforces.
"""

from __future__ import annotations

from typing import Any

import streamlit as st

from dashboard.oos_view import load_evaluations, render_evaluation
from dashboard.presenters import format_number, format_percent, format_yen
from dashboard.report_view import render_report
from dashboard.research_artifacts import (
    COMPARISON_DIRECTORY,
    WEEK_TEST_DIRECTORY,
    labelled_runs,
    load_artifact,
)
from dashboard.ui import configure_page, display_rows, render_header


def _render_comparison(report: dict[str, Any], key_prefix: str) -> None:
    """Show one comparison run: what was tried, and whether it was adopted."""

    window = report.get("generated_for", {})
    half_lives = window.get("recency_half_lives") or ["none"]
    st.caption(
        f"学習: 各予測日の直前 {window.get('training_window_sessions', '—')} 営業日 / "
        f"共通の予測件数 {window.get('paired_predictions', '—')} / "
        f"履歴の重み付け {', '.join(str(value) for value in half_lives)}"
    )

    display_rows(
        [
            {
                "候補": name,
                "予測要素": summary.get("feature_count", "—"),
                "履歴の重み": (
                    "全期間を等しく"
                    if summary.get("recency_half_life_sessions") is None
                    else f"直近重視 (半減期{summary['recency_half_life_sessions']}日)"
                ),
                "方向的中率": format_percent(summary.get("direction_accuracy")),
                "予測誤差(MAE)": format_number(
                    summary.get("mean_absolute_error"), digits=5
                ),
                "BUY": summary.get("buy_signals", 0),
                "勝率": (
                    format_percent(summary["win_rate"])
                    if summary.get("win_rate") is not None
                    else "—"
                ),
                "純損益": format_yen(summary.get("net_profit_jpy")),
            }
            for name, summary in report.get("sets", {}).items()
            if summary.get("predictions")
        ]
    )
    st.caption(
        "**判定に使うのは方向的中率と予測誤差だけです。** 勝率と純損益は"
        "BUY件数が少なく、良い方法と運の良い月を区別できないので載せているだけです。"
        "予測要素を増やすと予測値のばらつきが広がり、閾値を跨ぐ回数が増えます。"
        "そのぶん取引数と損益は動きますが、当たるようになったことを意味しません。"
    )

    for comparison in report.get("comparisons", []):
        verdict = str(comparison.get("verdict", ""))
        renderer = st.success if verdict.startswith("採用候補") else st.info
        renderer(
            f"**{comparison['candidate']}** vs {comparison['baseline']} — {verdict}"
        )
        st.caption(
            f"片方だけ正解した予測: 候補 {comparison['candidate_only_correct']}件 / "
            f"基準 {comparison['baseline_only_correct']}件 "
            f"(判定に使えた {comparison['discordant_pairs']}件) / "
            f"符号検定 p = {comparison['p_value']:.4f}"
        )
    with st.expander("なぜ符号検定なのか", expanded=False):
        st.markdown(
            "全体の的中率どうしを引き算すると、1ポイント程度の差は簡単に偶然で出ます。\n\n"
            "そこで**同じ日・同じ銘柄の予測を1件ずつ突き合わせ**、どちらか一方だけが"
            "当てた予測を数えます。両方当てた日と両方外した日は、どちらが優れているかの"
            "情報を持たないので除外します。残った件数の偏りが偶然で説明できないとき"
            "(p < 0.05) だけ、採用候補になります。"
        )


def _render_comparison_section() -> None:
    """Render every feature/weighting comparison that has been run."""

    reports: list[tuple[str, dict[str, Any]]] = []
    for path in sorted(COMPARISON_DIRECTORY.glob("*.json")):
        report = load_artifact(path)
        if report is None or not report.get("sets"):
            continue
        window = report.get("generated_for", {})
        reports.append(
            (f"学習窓 {window.get('training_window_sessions', '?')}営業日", report)
        )
    if not reports:
        return

    st.subheader("予測要素と重み付けの比較")
    st.caption(
        "「指標を増やす」「直近を重く見る」といった変更を、現行と同じ期間・同じ銘柄・"
        "同じモデルで走らせて突き合わせた結果です。有意に勝った候補が無ければ、"
        "現行のまま何も変えません。"
    )
    for tab, (label, report) in zip(
        st.tabs([label for label, _ in reports]), reports, strict=True
    ):
        with tab:
            _render_comparison(report, label.replace(" ", ""))
    st.divider()


def _render_oos_section() -> None:
    """The three-layer evaluation, one tab per saved run.

    Kept apart from the week-test tabs below because it answers a different
    question. Those report one window's trading result; this reports whether
    the predictions carried information at all, and separates that from whether
    a rule built on them made money -- the two have already disagreed here.
    """

    evaluations = load_evaluations()
    st.subheader("OOS 3層評価")
    if not evaluations:
        st.info(
            "PENDING: `docs/oos/` に評価結果がありません。"
            "`python -m scripts.evaluate_predictions --live "
            "--output docs/oos/xxx.json` などで生成してください。"
        )
        st.divider()
        return
    st.caption(
        "Model（予測が当たっているか）・Selection（その日の銘柄を並べられるか）"
        "・Probability（確率が確率か）・Trading（ルールを通していくらか）を"
        "分けて出しています。層ごとに標本の大きさが違うので、"
        "モデルの採否は最も標本の大きい Model / Selection で判定します。"
    )
    for tab, (_, evaluation) in zip(
        st.tabs([label for label, _ in evaluations]), evaluations, strict=True
    ):
        with tab:
            render_evaluation(evaluation)
    st.divider()


def main() -> None:
    configure_page("テスト", "🧪")
    render_header(
        "テスト",
        "過去の教師データで学習したモデルを、直近の期間で検証した結果です。",
    )

    _render_oos_section()
    _render_comparison_section()

    windows = labelled_runs(WEEK_TEST_DIRECTORY)
    if not windows:
        st.warning(
            f"PENDING: 検証結果が `{WEEK_TEST_DIRECTORY}` にありません。先に "
            "`python -m cli week-test` を実行してください。"
        )
        return

    st.caption(
        "タブごとに検証期間が違います。期間が長いほど予測件数が多く、"
        "方向的中率の数字は信頼できます。逆にBUY件数は期間を延ばしても"
        "そこまで増えないので、勝率と損益はどのタブでも証拠になりません。"
    )
    for tab, (label, report) in zip(
        st.tabs([label for label, _ in windows]), windows, strict=True
    ):
        with tab:
            # Every window renders the same widgets, so each tab needs its own
            # key namespace or Streamlit rejects the second tab as a duplicate.
            render_report(report, label.replace(" ", ""))


if __name__ == "__main__":
    main()
