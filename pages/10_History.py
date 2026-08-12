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
from typing import Any

import pandas as pd
import streamlit as st

from dashboard.catalog import stock_label
from dashboard.history import build_history_report
from dashboard.presenters import format_number, format_percent, format_yen
from dashboard.progress import (
    DEFAULT_ROLLING_SESSIONS,
    daily_points,
    rolling_series,
    version_changes,
    version_summary,
)
from dashboard.presenters import outcome_table_rows
from dashboard.report_view import render_report
from dashboard.significance import (
    DISCOVERY_RATE,
    MINIMUM_SIGNALS_FOR_EVIDENCE,
    evaluate_overall,
    evaluate_tickers,
)
from dashboard.ui import (
    cached_prediction_history_window,
    configure_page,
    display_rows,
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


def _render_progress(report: dict[str, Any]) -> None:
    """Is the model improving? Plotted against what doing nothing would score."""

    points = daily_points(report["predictions"])
    st.subheader("モデルは良くなっているか")
    if not points:
        st.info("PENDING: 実績が確定した営業日がまだありません。")
        return

    window = DEFAULT_ROLLING_SESSIONS
    frame = pd.DataFrame(rolling_series(points, window)).set_index("date")
    st.caption(
        "**上の線が下の線を上回っていれば、モデルが「常に上昇」より当たっています。**"
        "毎日の値は上下に振れるので、移動平均と累積の両方を見てください。"
        f"移動平均は{window}営業日たまるまで表示されません。"
    )

    rolling_columns = [
        f"方向的中率({window}日移動平均)",
        f"常に上昇({window}日移動平均)",
    ]
    if frame[rolling_columns].notna().any().any():
        st.caption(f"{window}営業日の移動平均")
        st.line_chart(frame.loc[:, rolling_columns], use_container_width=True)

    st.caption("累積 (初日からの通算。日数が増えるほど安定します)")
    st.line_chart(
        frame.loc[:, ["累積の方向的中率", "累積の常に上昇"]], use_container_width=True
    )

    st.caption("日々の方向的中率 (振れが大きいので、単独では判断できません)")
    st.line_chart(
        frame.loc[:, ["方向的中率", "常に上昇と予測した場合"]],
        use_container_width=True,
    )

    if frame["累積損益"].abs().sum() > 0:
        st.caption("累積損益 (BUYシグナルのみ。件数が少ないうちは証拠になりません)")
        st.line_chart(frame.loc[:, ["累積損益"]], use_container_width=True)

    changes = version_changes(points)
    versions = version_summary(points)
    if len(versions) > 1 or changes:
        st.caption("モデル改良の履歴と、その版が担当した期間の成績")
        display_rows(
            [
                {
                    "model_version": row["model_version"],
                    "期間": f"{row['from']} 〜 {row['to']}",
                    "営業日": row["sessions"],
                    "予測数": row["predictions"],
                    "方向的中率": format_percent(row["direction_accuracy"]),
                    "常に上昇": format_percent(row["baseline_up_rate"]),
                    "差(pt)": (
                        f"{row['edge'] * 100:+.1f}" if row["edge"] is not None else "—"
                    ),
                    "純損益": format_yen(row["net_profit_jpy"]),
                }
                for row in versions
            ]
        )
        st.caption(
            "版ごとの比較は参考値です。期間が違えば相場も違うので、"
            "差がモデルの改良によるものか相場によるものかは、これだけでは分かりません。"
            "「差(pt)」は同じ日の「常に上昇」と比べているぶん、その影響を抑えてあります。"
        )
    st.divider()


def _render_significance(report: dict[str, Any]) -> None:
    """Has the signal beaten simply owning these stocks, and can we tell yet?"""

    rows = report["predictions"]
    overall = evaluate_overall(rows)
    st.subheader("買いシグナルは、適当に買うより当たっているか")

    if overall.signal_win_rate is None or overall.baseline_win_rate is None:
        st.info("PENDING: 判定に必要な実績がまだありません。")
        return

    columns = st.columns(4)
    columns[0].metric("BUY時の上昇率", format_percent(overall.signal_win_rate))
    columns[1].metric("それ以外の上昇率", format_percent(overall.baseline_win_rate))
    columns[2].metric(
        "差", f"{(overall.edge or 0) * 100:+.1f}pt", help="BUY時 マイナス それ以外"
    )
    columns[3].metric("対象営業日", f"{overall.trading_days} 日")

    renderer = (
        st.success
        if overall.block_bootstrap_p_value is not None
        and overall.block_bootstrap_p_value < 0.05
        and overall.signals >= MINIMUM_SIGNALS_FOR_EVIDENCE
        else st.info
    )
    renderer(f"**全銘柄まとめ** — {overall.verdict}")
    st.caption(
        f"BUY {overall.signals}回 "
        f"(上昇 {overall.signal_up} / 下落 {overall.signal_down})、"
        f"それ以外 {overall.other_up + overall.other_down}回 "
        f"(上昇 {overall.other_up} / 下落 {overall.other_down})。"
        f"平均リターンは BUY時 {format_percent(overall.signal_mean_return)}、"
        f"それ以外 {format_percent(overall.baseline_mean_return)}。"
    )
    with st.expander("なぜ日単位で検定するのか", expanded=False):
        st.markdown(
            f"同じ日の22銘柄は同じ相場に乗って一緒に動くので、**独立した22件の"
            f"観測ではありません**。独立とみなして計算すると "
            f"p = {overall.naive_p_value:.2e} まで小さくなりますが、これは"
            "「たまたま上がった1日」を22回数えた結果です。\n\n"
            f"そこで営業日ごと丸ごと再抽出するブートストラップ"
            f"({overall.iterations}回) で検定しています。1日は1観測です。"
            "合成データで確認したところ、両者は最大14桁ずれました。"
        )

    st.subheader("銘柄ごとの当たりやすさ")
    evidence = evaluate_tickers(rows)
    ready = [item for item in evidence if item.has_enough_signals]
    st.caption(
        f"BUYが{MINIMUM_SIGNALS_FOR_EVIDENCE}回以上出た銘柄は "
        f"{len(ready)}/{len(evidence)} です。"
        f"22銘柄を個別に検定すると、p<0.05 は偶然でも1銘柄ほど出ます。"
        f"そのため多重比較を補正した **q値** で判定し、"
        f"q < {DISCOVERY_RATE} を有意としています。"
    )
    display_rows(
        [
            {
                "銘柄": stock_label(item.ticker),
                "BUY回数": item.signals,
                "BUY時の上昇率": format_percent(item.signal_win_rate),
                "適当に買った場合": format_percent(item.baseline_win_rate),
                "差(pt)": (
                    f"{(item.edge or 0) * 100:+.1f}" if item.edge is not None else "—"
                ),
                "p値": format_number(item.p_value, digits=3),
                "q値(補正後)": format_number(item.q_value, digits=3),
                "判定": (
                    "有意"
                    if item.has_enough_signals and item.q_value < DISCOVERY_RATE
                    else ("差なし" if item.has_enough_signals else "判定不能")
                ),
                "実績日数": item.sessions,
            }
            for item in sorted(
                evidence, key=lambda item: (not item.has_enough_signals, item.q_value)
            )
        ],
        height=460,
    )
    st.caption(
        "「適当に買った場合」は、その銘柄でBUYが出なかった日の上昇率です。"
        "シグナルの価値は、この差がプラスで、かつ偶然で説明できないときにだけ認められます。"
        "日数が増えるほど判定できる銘柄が増えていきます。"
    )
    st.divider()


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

            _render_progress(report)
            _render_significance(report)

            # The record itself, before any aggregate of it: one row per
            # prediction beside what the session actually did. Unsettled days
            # keep their outcome columns empty rather than showing a zero,
            # which would read as a flat result rather than an unknown one.
            rows = [dict(row) for row in result.rows]
            buys = outcome_table_rows(rows, buy_only=True)
            st.subheader(f"過去の買い予測とその結果 {len(buys)}件")
            if buys:
                st.caption(
                    "BUYを出した日だけを新しい順に並べています。"
                    "「方向」は予測の符号が実績と一致したかどうかです。"
                )
                display_rows(list(reversed(buys)), height=420)
            else:
                st.info("この期間にBUYはありません。0件も正常な結果です。")

            everything = outcome_table_rows(rows)
            with st.expander(f"全銘柄の予測と結果 {len(everything)}件"):
                st.caption(
                    "BUY以外も含む全公開予測です。実績が未確定の日は空欄になります。"
                )
                display_rows(list(reversed(everything)), height=520)

            render_report(report, f"history_{label}")


if __name__ == "__main__":
    main()
