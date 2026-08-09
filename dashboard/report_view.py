"""Rendering for one walk-forward report, shared by every page that shows one.

The research artifacts and the production history describe the same thing: a
set of predictions over a window, what they were worth, and what drove them. So
they are rendered by the same code from the same dictionary shape. Two pages
with their own copies of this drifted apart once already, and the reader had no
way to tell which one was stale.

Nothing here queries or computes; callers supply an already-built report.
"""

from __future__ import annotations

from typing import Any

import pandas as pd
import streamlit as st

from dashboard.catalog import stock_label
from dashboard.presenters import format_number, format_percent, format_yen
from dashboard.ui import display_rows


def _render_headline(report: dict[str, Any]) -> None:
    totals = report.get("totals", {})
    rule = report.get("rule", {})
    window = report.get("generated_for", {})

    st.caption(
        f"学習: 各予測日の直前 {window.get('training_window_sessions', '—')} 営業日 / "
        f"BUY条件: 予測リターン > "
        f"{float(rule.get('return_threshold', 0)) * 100:.2f}% かつ 上昇確率 >= "
        f"{float(rule.get('probability_threshold', 0)) * 100:.0f}%"
    )
    chosen = report.get("feature_set")
    if chosen:
        st.caption(
            f"予測要素: {chosen.get('name', '—')} "
            f"({chosen.get('feature_count', '—')}個) — {chosen.get('label', '')}"
        )

    first = st.columns(4)
    first[0].metric("予測件数", str(totals.get("predictions", 0)))
    first[1].metric("BUYシグナル", str(totals.get("buy_signals", 0)))
    first[2].metric(
        "勝率",
        format_percent(totals.get("win_rate"))
        if totals.get("win_rate") is not None
        else "—",
    )
    first[3].metric("純損益", format_yen(totals.get("net_profit_jpy")))

    second = st.columns(4)
    second[0].metric("勝ち金額", format_yen(totals.get("gross_win_jpy")))
    second[1].metric("負け金額", format_yen(totals.get("gross_loss_jpy")))
    second[2].metric(
        "金額ベース勝率",
        format_number(totals.get("money_win_ratio"), digits=3)
        if totals.get("money_win_ratio") is not None
        else "負けなし",
    )
    second[3].metric(
        "方向的中率",
        format_percent(totals.get("direction_accuracy"))
        if totals.get("direction_accuracy") is not None
        else "—",
    )
    st.caption(
        "「金額ベース勝率」は 勝ち金額 ÷ 負け金額 です。1.0を超えると、"
        "勝ったときの合計が負けたときの合計を上回っていた、という意味になります。"
        "回数の勝率とは別物で、両方を見ないと判断できません。"
    )

    buy_signals = int(totals.get("buy_signals") or 0)
    if buy_signals < 20:
        st.error(
            f"LOW_SAMPLE: BUYシグナルが {buy_signals} 件しかありません。"
            "この勝率は偶然の範囲で大きく動きます。有効性の証拠として扱わないでください。"
        )


def _render_buy_list(report: dict[str, Any]) -> None:
    """List every stock the rule actually bought, and why it qualified."""

    rule = report.get("rule", {})
    return_threshold = float(rule.get("return_threshold", 0.0))
    probability_threshold = float(rule.get("probability_threshold", 0.0))

    st.subheader("実際に買った銘柄")
    st.info(
        f"**買いの判断基準: 予測リターン > {return_threshold * 100:.2f}%　"
        f"かつ　上昇確率 >= {probability_threshold * 100:.0f}%**\n\n"
        "この2つを同時に満たした銘柄だけを、寄り付きで買って同日の大引けで売っています。"
        "片方だけでは買いません。持ち越しもしません。"
    )
    st.caption(
        f"資金は1銘柄あたり {float(rule.get('capital_per_stock_jpy', 0)):,.0f}円、"
        f"{int(rule.get('lot_size', 100))}株単位。"
        f"手数料 {rule.get('commission_bps_per_side')} bps と"
        f"スリッページ {rule.get('slippage_bps_per_side')} bps を"
        "片側ずつ差し引いています。"
    )

    bought = [
        row for row in report.get("predictions", []) if row.get("signal") == "BUY"
    ]
    if not bought:
        st.warning("この期間に条件を満たした銘柄はありませんでした。")
        return

    display_rows(
        [
            {
                "日付": row["date"],
                "社名": stock_label(str(row["ticker"])),
                "結果": "勝ち" if float(row["net_profit_jpy"]) > 0 else "負け",
                "予測リターン": format_percent(row["predicted_return"]),
                "上昇確率": format_percent(row["probability_up"]),
                "実績リターン": format_percent(row["actual_return"]),
                "予測 終値-寄付(1株)": format_yen(
                    row.get("predicted_price_difference")
                ),
                "実績 終値-寄付(1株)": format_yen(row.get("actual_price_difference")),
                "買値(寄付)": format_number(row.get("actual_open"), digits=1),
                "売値(大引)": format_number(row.get("actual_close"), digits=1),
                "株数": int(row["shares"]),
                "損益(合計)": format_yen(row["net_profit_jpy"]),
            }
            for row in sorted(bought, key=lambda item: (item["date"], item["ticker"]))
        ]
    )

    with st.expander("条件を満たさなかった銘柄は、どこで外れたか", expanded=False):
        skipped = [
            row for row in report.get("predictions", []) if row.get("signal") != "BUY"
        ]
        near_miss = sorted(skipped, key=lambda item: -float(item["predicted_return"]))[
            :15
        ]
        display_rows(
            [
                {
                    "日付": row["date"],
                    "銘柄": stock_label(str(row["ticker"])),
                    "予測リターン": format_percent(row["predicted_return"]),
                    "上昇確率": format_percent(row["probability_up"]),
                    "外れた条件": " / ".join(
                        filter(
                            None,
                            [
                                "予測リターンが閾値以下"
                                if float(row["predicted_return"]) <= return_threshold
                                else "",
                                "上昇確率が下限未満"
                                if float(row["probability_up"]) < probability_threshold
                                else "",
                            ],
                        )
                    )
                    or "—",
                }
                for row in near_miss
            ]
        )
        st.caption(
            "予測リターンが最も高かった順に15件です。"
            "多くは上昇確率が60%に届かずに見送られています。"
        )


def _render_daily(report: dict[str, Any]) -> None:
    daily = report.get("daily", [])
    if not daily:
        st.info("PENDING: 日別の結果がありません。")
        return

    st.subheader("毎日の勝率と損益")
    display_rows(
        [
            {
                "日付": row.get("date"),
                "予測数": row.get("predictions", 0),
                "BUY": row.get("buy_signals", 0),
                "勝ち": row.get("wins", 0),
                "負け": row.get("losses", 0),
                "勝率": (
                    format_percent(row.get("win_rate"))
                    if row.get("win_rate") is not None
                    else "—"
                ),
                "勝ち金額": format_yen(row.get("gross_win_jpy")),
                "負け金額": format_yen(row.get("gross_loss_jpy")),
                "金額ベース勝率": (
                    format_number(row.get("money_win_ratio"), digits=3)
                    if row.get("money_win_ratio") is not None
                    else "—"
                ),
                "純損益": format_yen(row.get("net_profit_jpy")),
                "方向的中率": format_percent(row.get("direction_accuracy")),
            }
            for row in daily
        ]
    )

    frame = pd.DataFrame(daily)
    if "net_profit_jpy" in frame.columns:
        cumulative = frame.loc[:, ["date", "net_profit_jpy"]].copy()
        cumulative["累積損益 (円)"] = cumulative["net_profit_jpy"].cumsum()
        st.line_chart(
            cumulative.set_index("date").loc[:, ["累積損益 (円)"]],
            use_container_width=True,
        )
    if "direction_accuracy" in frame.columns:
        st.caption("日ごとの方向的中率 (BUY以外の予測も含む全銘柄)")
        st.bar_chart(
            frame.set_index("date").loc[:, ["direction_accuracy"]],
            use_container_width=True,
        )


def _render_price_predictions(report: dict[str, Any], key_prefix: str) -> None:
    predictions = report.get("predictions", [])
    if not predictions:
        return

    st.subheader("寄り付き・大引けの予測と実績")
    st.caption(
        "朝の時点では当日の寄り付きが未確定なので、前日終値を基準に予測終値を出します。"
        "寄り付きが判明した後は、実際の寄り付き価格を基準に引き直します。"
        "この2つは別の数字なので、並べて表示しています。"
    )

    frame = pd.DataFrame(predictions)
    dates = sorted({str(value) for value in frame["date"]})
    selectors = st.columns(2)
    chosen_date = selectors[0].selectbox(
        "日付", dates, index=len(dates) - 1, key=f"{key_prefix}_prediction_date"
    )
    only_buy = selectors[1].checkbox(
        "BUYシグナルだけ表示", value=False, key=f"{key_prefix}_only_buy"
    )

    view = frame.loc[frame["date"].astype(str) == chosen_date]
    if only_buy:
        view = view.loc[view["signal"] == "BUY"]
    if view.empty:
        st.info("該当する行がありません。")
        return

    view = view.sort_values("predicted_return", ascending=False)
    display_rows(
        [
            {
                "銘柄": stock_label(str(row["ticker"])),
                "判定": row["signal"],
                "予測リターン": format_percent(row["predicted_return"]),
                "予測 終値-寄付(1株)": format_yen(
                    row.get("predicted_price_difference")
                ),
                "実績リターン": format_percent(row["actual_return"]),
                "実績 終値-寄付(1株)": format_yen(row.get("actual_price_difference")),
                "上昇確率": format_percent(row["probability_up"]),
                "前日終値": format_number(row.get("reference_close"), digits=1),
                "予測終値(朝/前日終値基準)": format_number(
                    row.get("morning_predicted_close"), digits=1
                ),
                "実際の寄り付き": format_number(row.get("actual_open"), digits=1),
                "予測終値(寄り付き基準)": format_number(
                    row.get("post_open_predicted_close"), digits=1
                ),
                "実際の終値": format_number(row.get("actual_close"), digits=1),
                "方向": "OK" if row["direction_correct"] else "NG",
                "株数": int(row["shares"]),
                "損益(合計)": format_yen(row["net_profit_jpy"]),
            }
            for row in view.to_dict("records")
        ],
        height=560,
    )

    st.caption("銘柄別の予測終値(寄り付き基準)と実際の終値の推移")
    tickers = sorted({str(value) for value in frame["ticker"]})
    chosen_ticker = st.selectbox(
        "銘柄を選ぶ",
        tickers,
        format_func=stock_label,
        key=f"{key_prefix}_chart_ticker",
    )
    series = frame.loc[frame["ticker"].astype(str) == chosen_ticker].sort_values("date")
    if not series.empty:
        chart = pd.DataFrame(
            {
                "日付": series["date"],
                "予測終値": series["post_open_predicted_close"],
                "実際の終値": series["actual_close"],
                "実際の寄り付き": series["actual_open"],
            }
        ).set_index("日付")
        st.line_chart(chart, use_container_width=True)


def _render_company_coefficients(report: dict[str, Any], key_prefix: str) -> None:
    """Show one company's coefficient per indicator, per day, across the window."""

    records = report.get("company_coefficients", [])
    st.subheader("銘柄別: 各指標の係数の推移")
    if not records:
        st.warning(
            "PENDING: 銘柄別の係数がこの検証結果に含まれていません。"
            "`python -m cli week-test` を実行し直すと生成されます。"
        )
        return

    st.caption(
        "選んだ銘柄の予測を作るために、どの指標がどの係数で効いていたかを"
        "営業日ごとに並べたものです。標準化後の係数なので、"
        "同じ銘柄・同じモデル内でのみ大小を比較できます。"
    )

    frame = pd.DataFrame(records)
    tickers = sorted({str(value) for value in frame["ticker"]})
    selectors = st.columns(2)
    ticker = selectors[0].selectbox(
        "銘柄", tickers, format_func=stock_label, key=f"{key_prefix}_coef_ticker"
    )
    view = frame.loc[frame["ticker"].astype(str) == ticker].copy()

    influence = (
        view.groupby("feature")["coefficient"]
        .apply(lambda values: values.abs().mean())
        .sort_values(ascending=False)
    )
    features = [str(name) for name in influence.index]
    chosen = selectors[1].multiselect(
        "表示する指標",
        features,
        default=features[:6],
        key=f"{key_prefix}_coef_features",
    )

    if chosen:
        pivot = view.loc[view["feature"].isin(chosen)].pivot_table(
            index="date", columns="feature", values="coefficient", aggfunc="mean"
        )
        st.line_chart(pivot, use_container_width=True)

    wide = view.pivot_table(
        index="date", columns="feature", values="coefficient", aggfunc="mean"
    ).sort_index()
    ordered = [name for name in features if name in wide.columns]
    st.caption(
        f"{stock_label(ticker)} の全 {len(ordered)} 指標 x {len(wide)} 営業日。"
        "影響の大きい指標から左に並べています。"
    )
    display_rows(
        [
            {
                "日付": str(index),
                **{name: format_number(row[name], digits=5) for name in ordered},
            }
            for index, row in wide.iterrows()
        ],
        height=460,
    )

    appeared = view.loc[view["first_seen"]]
    appeared = appeared.loc[appeared["date"] > view["date"].min()]
    if not appeared.empty:
        st.caption("この期間に新しく使われ始めた指標")
        display_rows(
            [
                {
                    "初めて使われた日": row["date"],
                    "指標": row["feature"],
                    "その日の係数": format_number(row["coefficient"], digits=5),
                }
                for row in appeared.sort_values("date").to_dict("records")
            ]
        )


def _render_coefficients(report: dict[str, Any], key_prefix: str) -> None:
    changes = report.get("coefficient_changes", [])
    if not changes:
        st.info("PENDING: 係数の記録がありません。")
        return

    st.subheader("各指標の係数と、その日々の変化")
    st.caption(
        "全銘柄で学習された係数の平均です。標準化後の値なので、"
        "同じモデル内でのみ大小を比較できます。"
        "「前日差」が大きい指標は、日によって効き方が変わっていたことを意味します。"
    )

    frame = pd.DataFrame(changes)
    features = sorted({str(value) for value in frame["feature"]})
    magnitude = (
        frame.groupby("feature")["mean_coefficient"]
        .apply(lambda values: values.abs().mean())
        .sort_values(ascending=False)
    )
    default = [str(name) for name in magnitude.head(6).index]
    chosen = st.multiselect(
        "表示する指標",
        features,
        default=default,
        key=f"{key_prefix}_mean_coef_features",
    )
    if chosen:
        pivot = frame.loc[frame["feature"].isin(chosen)].pivot_table(
            index="date", columns="feature", values="mean_coefficient", aggfunc="mean"
        )
        st.line_chart(pivot, use_container_width=True)

    dates = sorted({str(value) for value in frame["date"]})
    chosen_date = st.selectbox(
        "係数を確認する日付",
        dates,
        index=len(dates) - 1,
        key=f"{key_prefix}_mean_coef_date",
    )
    view = frame.loc[frame["date"].astype(str) == chosen_date].copy()
    view["abs_change"] = view["change_from_previous_day"].abs()
    view = view.sort_values("abs_change", ascending=False, na_position="last")
    display_rows(
        [
            {
                "指標": row["feature"],
                "平均係数": format_number(row["mean_coefficient"], digits=5),
                "向き": (
                    "上げ要因"
                    if float(row["mean_coefficient"]) > 0
                    else "下げ要因"
                    if float(row["mean_coefficient"]) < 0
                    else "中立"
                ),
                "前日差": (
                    "—"
                    if row.get("change_from_previous_day") is None
                    else format_number(row["change_from_previous_day"], digits=5)
                ),
            }
            for row in view.to_dict("records")
        ],
        height=520,
    )


def render_report(report: dict[str, Any], key_prefix: str) -> None:
    """Render one window's report in full."""

    _render_headline(report)
    st.divider()
    _render_buy_list(report)
    st.divider()
    _render_daily(report)
    st.divider()
    _render_price_predictions(report, key_prefix)
    st.divider()
    _render_company_coefficients(report, key_prefix)
    st.divider()
    _render_coefficients(report, key_prefix)

    failures = report.get("failures") or {}
    if failures:
        with st.expander(f"除外された銘柄 {len(failures)}件", expanded=False):
            display_rows(
                [
                    {"銘柄": stock_label(str(key)), "理由": str(value)}
                    for key, value in failures.items()
                ]
            )
    for caveat in report.get("caveats", []):
        st.caption(f"注意: {caveat}")
