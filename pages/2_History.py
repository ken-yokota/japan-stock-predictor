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
from dashboard.history_progress import (
    GROUPINGS,
    accuracy_series,
    cumulative_profit,
    grouped_accuracy,
    grouped_returns,
    pivot,
)
from dashboard.outcomes import outcome_table_rows
from dashboard.presenters import format_number, format_percent, format_yen
from dashboard.progress import (
    daily_points,
    version_changes,
    version_summary,
)
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


def _deviation_chart(points: list[Any], label: str) -> None:
    """Plot accuracy as points away from a coin flip, with zero marked."""

    frame = pd.DataFrame(
        {
            "日付": [point.date for point in points],
            label: [point.deviation * 100 for point in points],
            "五分五分": [0.0 for _ in points],
        }
    ).set_index("日付")
    st.line_chart(frame, use_container_width=True)


def _render_progress(report: dict[str, Any], window: str) -> None:
    """Is the model beating a coin flip, and is the gap moving?"""

    rows = report["predictions"]
    st.subheader("モデルは良くなっているか")

    buy_points = accuracy_series(rows, buy_only=True)
    all_points = accuracy_series(rows, buy_only=False)
    if not buy_points and not all_points:
        st.info("PENDING: 実績が確定した営業日がまだありません。")
        return

    st.caption(
        "縦軸は**五分五分（50%）からの差**です。方向当ては当たり外れの二択なので、"
        "50%は「何も知らない」場合の成績にあたります。"
        "その日の的中率が75%なら +25%、25%なら -25% と表示されます。"
        "0の線を上回っていた日が、当てずっぽうより当たっていた日です。"
    )

    st.caption("① 買い候補の方向的中率（その日にBUYを出した銘柄のみ）")
    if buy_points:
        _deviation_chart(buy_points, "買い候補の的中率 (50%からの差)")
        st.caption(
            f"対象 {len(buy_points)}営業日 / "
            f"BUY {sum(point.count for point in buy_points)}件。"
            "1日あたりの件数が少ないので、単日の上下は成績というより偶然です。"
        )
    else:
        st.info("この期間にBUYはありません。0件も正常な結果です。")

    st.caption("② 全銘柄の方向的中率（BUY以外も含む全公開予測）")
    if all_points:
        _deviation_chart(all_points, "全銘柄の的中率 (50%からの差)")
        st.caption(
            f"対象 {len(all_points)}営業日 / "
            f"{sum(point.count for point in all_points)}件。"
            "①はルールが選んだ銘柄の成績、②はモデルが方向を当てられるかそのものです。"
        )

    profit = cumulative_profit(rows)
    if profit and any(value != 0.0 for _, value in profit):
        st.caption("③ 累積損益（BUYシグナルの想定損益を積み上げたもの）")
        st.line_chart(
            pd.DataFrame(
                {"日付": [day for day, _ in profit], "累積損益": [v for _, v in profit]}
            ).set_index("日付"),
            use_container_width=True,
        )
        st.caption(
            "記録された建玉数から計算した想定値で、手数料・スリッページは含みません。"
            "件数が少ないうちは証拠になりません。"
        )

    _render_breakdown(rows, window)

    changes = version_changes(daily_points(rows))
    versions = version_summary(daily_points(rows))
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


def _render_breakdown(rows: list[dict[str, Any]], window: str) -> None:
    """Per-sector or per-ticker accuracy and returns, folded away until asked.

    Both charts obey one selector. Splitting the choice in two invites reading
    a sector accuracy line beside a per-ticker return line and treating them as
    the same cut of the data.
    """

    with st.expander("業界別・銘柄別の内訳", expanded=False):
        # Keyed per window: the three tabs all render this, and Streamlit
        # refuses two widgets with the same key on one page.
        grouping = st.radio(
            "集計単位",
            GROUPINGS,
            horizontal=True,
            key=f"history_breakdown_grouping_{window}",
        )
        st.caption(
            "BUY以外も含む全公開予測が対象です。"
            "1日あたりの件数が少ない区分ほど線は大きく振れます。"
        )

        accuracy = grouped_accuracy(rows, grouping=grouping)
        if not accuracy:
            st.info("実績が確定した営業日がまだありません。")
            return
        st.caption(f"{grouping} 方向的中率（50%からの差）")
        st.line_chart(
            pd.DataFrame(pivot(accuracy, value="deviation")).T.sort_index() * 100,
            use_container_width=True,
        )

        returns = grouped_returns(rows, grouping=grouping)
        if not returns:
            return
        st.caption(f"{grouping} 予測Rと実績R")
        predicted = pd.DataFrame(pivot(returns, value="predicted_mean")).T.sort_index()
        actual = pd.DataFrame(pivot(returns, value="actual_mean")).T.sort_index()
        st.caption("予測R (%)")
        st.line_chart(predicted * 100, use_container_width=True)
        st.caption("実績R (%)")
        st.line_chart(actual * 100, use_container_width=True)
        st.caption(
            "同じ行を平均しているので、2枚の差はその区分に対するモデルの偏りです。"
        )


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
        show_banner=False,
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

            _render_progress(report, label)
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
