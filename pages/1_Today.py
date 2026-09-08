"""Today's persisted prediction publication."""

from __future__ import annotations

import streamlit as st

from dashboard.arm_density import render_arm_tabs
from dashboard.catalog import stock_label
from dashboard.completeness import (
    NORMAL,
    UNKNOWN,
    WARNING,
    stock_from_details,
    summarise,
)
from dashboard.outcomes import outcome_table_rows
from dashboard.presenters import (
    derive_operational_alerts,
    format_percent,
    today_table_rows,
)
from dashboard.today_view import (
    buy_cards,
    day_is_settled,
    density_chart,
    morning_email_sent,
    result_column_config,
    result_rows,
    shared_axis,
)
from dashboard.ui import (
    cached_feature_completeness,
    cached_latest_run,
    cached_metrics,
    cached_prediction_set,
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
    configure_page("Today", "🌅")
    render_header(
        "Today",
        "最新のREADY prediction setと、その08:30 JST cutoff・品質根拠を表示します。",
    )
    service = require_service()
    if service is None:
        return

    latest_run = cached_latest_run(service)
    prediction_set = cached_prediction_set(service)
    predictions = cached_today_predictions(service)
    selections = cached_selections(service)
    metrics = cached_metrics(service)

    run = latest_run.first
    publication = prediction_set.first
    prediction_rows = predictions.rows if predictions.ready else ()
    selection_rows = selections.rows if selections.ready else ()
    # Nothing above the BUY list. The page is opened at 08:30 to see which
    # stocks to buy, and a banner between the title and that answer costs a
    # scroll every single morning to say something that is usually routine.
    # Operational alerts, the cutoff summary and the quality panel all sit at
    # the bottom now, and the folded header names their state so a bad morning
    # is still visible without opening anything.
    alerts = derive_operational_alerts(
        run=run,
        prediction_set=publication,
        predictions=prediction_rows,
        selections=selection_rows,
    )

    if not render_query_state(
        predictions,
        empty_message="個別予測が未作成です。pipeline完了後に表示されます。",
    ):
        return

    metric_rows = metrics.rows if metrics.ready else ()
    table = today_table_rows(prediction_rows, metric_rows)

    # Completeness used to open the page with seven metrics, two banners and an
    # auto-expanded table, which pushed the BUY candidates - the reason anyone
    # opens this page at 08:30 - below the fold. It now sits at the bottom, and
    # only one case still interrupts the top: a BUY built on missing required
    # indicators, because that changes how the recommendation itself should be
    # read. Everything else is available lower down, unchanged.
    completeness = cached_feature_completeness(service)
    signals = {
        str(row.get("ticker")): str(row.get("signal") or "") for row in prediction_rows
    }
    coverages = {
        str(row.get("ticker")): row.get("feature_coverage") for row in prediction_rows
    }
    quality = summarise(
        [
            stock_from_details(
                str(row.get("ticker")),
                row.get("details"),
                feature_coverage=coverages.get(str(row.get("ticker"))),
                signal=signals.get(str(row.get("ticker")), ""),
            )
            for row in (completeness.rows if completeness.ready else ())
        ]
    )
    cards = buy_cards(prediction_rows)
    st.subheader(f"BUY候補 {len(cards)}件")
    if not cards:
        st.info("BUY条件を満たす公開済み銘柄はありません。0件も正常な結果です。")
    else:
        # Text lines rather than metric widgets. st.metric is tall, and four of
        # them per card pushed the candidates off a laptop screen -- on the one
        # page whose whole job is to answer "what do I buy" without scrolling.
        # The forecast is always the first line; the outcome adds a second only
        # once the session settles, so the morning card and the evening card
        # are the same object with one line more.
        for start in range(0, len(cards), 2):
            columns = st.columns(2)
            for column, card in zip(columns, cards[start : start + 2], strict=False):
                with column.container(border=True):
                    st.markdown(
                        f"**{card.label}**　<small>Rank {card.rank}</small>",
                        unsafe_allow_html=True,
                    )
                    st.markdown(
                        f"予測R **{card.predicted_return}**"
                        f" ／ 上昇確率 **{card.probability_up}**"
                    )
                    if card.settled:
                        colour = "green" if card.direction == "的中" else "red"
                        st.markdown(
                            f"実績R **{card.actual_return}**"
                            f" ／ :{colour}[**{card.direction}**]"
                        )
                    else:
                        st.caption("実績は大引け後")

    # The day's own record. Present before the close too -- the forecast
    # columns are filled and the outcome columns read "—" -- because the same
    # eleven columns before and after the close mean the operator learns one
    # table rather than two.
    settled = [
        row for row in prediction_rows if row.get("actual_intraday_return") is not None
    ]
    settled_day = day_is_settled(prediction_rows)
    prediction_day = str((publication or {}).get("prediction_date", ""))
    email_sent = morning_email_sent(service, prediction_day)
    # Every ticker missing a required indicator, not only the BUY ones: the
    # table covers all 22, so a NO BUY row with missing data must say so too.
    degraded = frozenset(
        item.ticker for item in quality.stocks if item.missing_required
    )

    heading = f"本日の結果（確定 {len(settled)}銘柄）" if settled_day else "本日の予測"
    st.subheader(heading)
    if settled_day:
        buy_outcomes = outcome_table_rows(prediction_rows, buy_only=True)
        hits = sum(1 for row in buy_outcomes if row["方向"] == "的中")
        scored = sum(1 for row in buy_outcomes if row["方向"] in {"的中", "外れ"})
        if scored:
            st.caption(f"BUY候補の方向的中 {hits}/{scored}")
    else:
        st.caption(
            "実績列は大引け後の更新で埋まります。いまは予測値だけが確定しています。"
        )
    st.dataframe(
        result_rows(
            prediction_rows,
            email_sent=email_sent,
            degraded_tickers=degraded,
        ),
        hide_index=True,
        column_config=result_column_config(),
        height=420,
        use_container_width=True,
    )

    _render_densities(prediction_rows, settled_day)
    _render_arm_densities(prediction_rows)

    st.subheader("全銘柄")
    st.caption(
        "判定は丸め前の保存値で確定済みです。INSUFFICIENT_DATA・FAILEDはBUY対象外です。"
    )
    display_rows(table, height=620)

    degraded_names = "、".join(
        f"{stock_label(item.ticker)}（{'・'.join(item.missing_required)}）"
        for item in quality.degraded_buys
    )
    if alerts or degraded_names or publication is not None:
        worst = max((alert.level.value for alert in alerts), default="")
        label = "システムの状態"
        if degraded_names:
            label = "⚠ システムの状態 — 必須指標が欠けたBUYがあります"
        elif alerts:
            label = f"システムの状態 — {len(alerts)}件の通知（{worst}）"
        with st.expander(label, expanded=False):
            if degraded_names:
                st.warning(f"⚠ 必須指標が欠けた状態のBUY: {degraded_names}")
            render_alerts(alerts)
            if publication is not None:
                render_cutoff_summary(
                    cutoff_at=publication.get("cutoff_at"),
                    generated_at=publication.get("generated_at"),
                    status=publication.get("status"),
                    run_id=publication.get("run_id"),
                )

    if quality.stock_count:
        status_text = {
            NORMAL: "NORMAL — 必須指標はすべて揃っています",
            WARNING: "WARNING — 必須指標が欠けた銘柄があります",
            UNKNOWN: "UNKNOWN — 欠損記録の導入前で、完全性を確認できません",
        }[quality.data_status]
        label = f"本日のデータ品質 — {status_text}"
        # Collapsed even when degraded: the BUY-level warning at the top already
        # said so, and opening a table nobody asked for is what buried the page.
        with st.expander(label, expanded=False):
            top = st.columns(4)
            top[0].metric("対象銘柄", quality.stock_count)
            top[1].metric("CLEAN", quality.clean_count)
            top[2].metric("⚠ DEGRADED", quality.degraded_count)
            top[3].metric("UNKNOWN", quality.unknown_count)
            bottom = st.columns(3)
            bottom[0].metric("BUY候補", quality.buy_count)
            bottom[1].metric("CLEAN BUY", quality.clean_buy_count)
            bottom[2].metric("⚠ DEGRADED BUY", quality.degraded_buy_count)

            display_rows(
                [
                    {
                        "銘柄": stock_label(item.ticker),
                        "状態": item.label,
                        "Indicator Coverage": format_percent(
                            item.indicator_coverage, digits=1
                        ),
                        "Feature Coverage": format_percent(
                            item.feature_coverage, digits=1
                        ),
                        "欠損(必須)": "、".join(item.missing_required) or "—",
                        "欠損(任意)": "、".join(item.missing_optional) or "—",
                        "シグナル": item.signal or "—",
                    }
                    for item in quality.stocks
                ],
                height=420,
            )
            if quality.missing_required_ranking:
                st.caption("欠損した必須指標（影響銘柄数の多い順）")
                display_rows(
                    [
                        {"指標": name, "区分": "REQUIRED", "影響銘柄数": count}
                        for name, count in quality.missing_required_ranking
                    ],
                    height=200,
                )
            if quality.hidden_by_feature_coverage:
                st.caption(
                    "Feature Coverage が100%でも Indicator Coverage が100%未満の銘柄: "
                    + "、".join(quality.hidden_by_feature_coverage)
                )

    with st.expander("品質表示の読み方"):
        st.markdown(
            """
            - **Data Cutoff**: 特徴量へ入れてよい情報の上限時刻です。
            - **FREE_UNVERIFIED / DELAYED**:
              無料・遅延データで、公式リアルタイム保証ではありません。
            - **FALLBACK**:
              Primaryが品質・coverage条件を満たさず、系列全体を代替Providerへ切替済みです。
            - **STALE / MISSING**: BUY判断を抑止すべき状態です。
            - **LOW_SAMPLE / PENDING**: 指標の母数または前提が未確定です。
            - **Indicator Coverage**: その銘柄に本来必要な海外指標のうち、実際に
              揃っていた割合です。設定から決まるので、まったく取得できなかった
              指標も「欠けている」と数えます。
            - **Feature Coverage**: 生成できた特徴量のうち値があった割合です。
              **必要指標全体の完全性とは別の指標**で、取得できなかった指標は
              分母に入りません。
            - **CLEAN / ⚠ DEGRADED / UNKNOWN**: 必須指標が揃っていたか、欠けて
              いたか、記録自体が無いか。UNKNOWNは「欠損なし」ではありません。
            """
        )


def _render_arm_densities(
    prediction_rows: tuple[dict[str, object], ...] | list[dict[str, object]],
) -> None:
    """Every model family's curve for one chosen ticker, one tab each."""

    usable = [row for row in prediction_rows if row.get("arm_predictions")]
    if not usable:
        return
    with st.expander("手法別の確率密度分布", expanded=False):
        ordered = sorted(
            usable,
            key=lambda row: (
                0 if str(row.get("signal", "")).upper() == "BUY" else 1,
                row.get("rank") or 999,
            ),
        )
        choice = st.selectbox(
            "銘柄",
            [str(row.get("ticker", "")) for row in ordered],
            format_func=stock_label,
            key="today_arm_density_ticker",
        )
        row = next(r for r in ordered if str(r.get("ticker", "")) == choice)
        actual = row.get("actual_intraday_return")
        render_arm_tabs(row, actual=float(actual) if actual is not None else None)


def _render_densities(
    prediction_rows: tuple[dict[str, object], ...] | list[dict[str, object]],
    settled_day: bool,
) -> None:
    """Per-ticker forecast densities, folded away until asked for.

    Twenty-two charts is a lot of page, and most mornings nobody wants them, so
    the whole section stays closed until it is opened. Every ticker is drawn on
    one shared return axis: densities on private axes cannot be compared with
    each other by eye, which is the main thing a wall of them is for.
    """

    drawable = [row for row in prediction_rows if row.get("return_distribution")]
    if not drawable:
        return
    label = (
        f"確率密度分布 {len(drawable)}銘柄（実績を重ねて表示）"
        if settled_day
        else f"確率密度分布 {len(drawable)}銘柄（予測のみ）"
    )
    with st.expander(label, expanded=False):
        st.caption(
            "縦の破線が予測リターン、赤い実線が実績リターン、"
            "灰色の点線が下振れ側の P50 / P75 / P90 です。"
            if settled_day
            else "縦の破線が予測リターン、灰色の点線が下振れ側の P50 / P75 / P90 です。"
            "実績が確定すると赤い実線が重なります。"
        )
        st.caption(
            "Pxx はその確率で「これ以上になる」水準です。"
            "P90 は90%の確率で上回る水準、つまり下振れ側のリスクで、"
            "図では P50 より左に出ます（上振れではありません）。"
        )
        buy_only = st.checkbox("BUY候補のみ", value=True)
        buy_first = sorted(
            drawable,
            key=lambda row: (
                0 if str(row.get("signal", "")).upper() == "BUY" else 1,
                row.get("rank") or 999,
            ),
        )
        chosen = (
            [row for row in buy_first if str(row.get("signal", "")).upper() == "BUY"]
            if buy_only
            else buy_first
        )
        if not chosen:
            st.caption(
                "BUY候補がありません。チェックを外すと全銘柄の分布を表示します。"
            )
            return
        # The axis spans whichever set is on screen. Rescaling to the drawn
        # rows keeps the shapes readable instead of squeezing four candidates
        # into the corner of an axis sized for twenty-two.
        low, high = shared_axis(chosen)
        for row in chosen:
            chart = density_chart(row, low=low, high=high)
            if chart is None:
                continue
            st.markdown(f"**{stock_label(str(row.get('ticker', '')))}**")
            st.altair_chart(chart, use_container_width=True)


if __name__ == "__main__":
    main()
