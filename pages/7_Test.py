"""Research test results for a fixed recent window.

This page reads an artifact produced by ``python -m cli week-test``. It does not
recompute anything: the numbers shown are exactly what that run wrote, so the
page and the artifact cannot disagree.

The window tested here is a research estimate, not a live track record. It runs
outside the database pipeline, so it skips the provider quality gates and
point-in-time lineage that the production path enforces.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pandas as pd  # type: ignore[import-untyped]
import streamlit as st

from dashboard.catalog import stock_label
from dashboard.presenters import format_number, format_percent, format_yen
from dashboard.ui import configure_page, display_rows, render_header

ARTIFACT_PATH = Path("artifacts/week_test/latest.json")


def _load_artifact(path: Path) -> dict[str, Any] | None:
    try:
        return dict(json.loads(path.read_text(encoding="utf-8")))
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        return None


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
                "銘柄": stock_label(str(row["ticker"])),
                "予測リターン": format_percent(row["predicted_return"]),
                "予測 終値-寄付(1株)": format_yen(
                    row.get("predicted_price_difference")
                ),
                "実績リターン": format_percent(row["actual_return"]),
                "実績 終値-寄付(1株)": format_yen(row.get("actual_price_difference")),
                "上昇確率": format_percent(row["probability_up"]),
                "買値(寄付)": format_number(row.get("actual_open"), digits=1),
                "売値(大引)": format_number(row.get("actual_close"), digits=1),
                "株数": int(row["shares"]),
                "損益(合計)": format_yen(row["net_profit_jpy"]),
                "結果": "勝ち" if float(row["net_profit_jpy"]) > 0 else "負け",
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


def _render_price_predictions(report: dict[str, Any]) -> None:
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
    chosen_date = selectors[0].selectbox("日付", dates, index=len(dates) - 1)
    only_buy = selectors[1].checkbox("BUYシグナルだけ表示", value=False)

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
    chosen_ticker = st.selectbox("銘柄を選ぶ", tickers, format_func=stock_label)
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


def _render_coefficients(report: dict[str, Any]) -> None:
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
    chosen = st.multiselect("表示する指標", features, default=default)
    if chosen:
        pivot = frame.loc[frame["feature"].isin(chosen)].pivot_table(
            index="date", columns="feature", values="mean_coefficient", aggfunc="mean"
        )
        st.line_chart(pivot, use_container_width=True)

    dates = sorted({str(value) for value in frame["date"]})
    chosen_date = st.selectbox(
        "係数を確認する日付", dates, index=len(dates) - 1, key="coefficient_date"
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


def _render_window_tab(report: dict[str, Any]) -> None:
    _render_headline(report)
    st.divider()
    _render_buy_list(report)
    st.divider()
    _render_daily(report)
    st.divider()
    _render_price_predictions(report)
    st.divider()
    _render_coefficients(report)

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


def main() -> None:
    configure_page("テスト", "🧪")
    render_header(
        "テスト",
        "過去の教師データで学習したモデルを、直近の期間で検証した結果です。",
    )

    report = _load_artifact(ARTIFACT_PATH)
    if report is None:
        st.warning(
            f"PENDING: 検証結果 `{ARTIFACT_PATH}` がありません。先に "
            "`python -m cli week-test` を実行してください。"
        )
        return

    window = report.get("generated_for", {})
    label = f"{window.get('from', '?')} 〜 {window.get('to', '?')}"
    (window_tab,) = st.tabs([label])
    with window_tab:
        _render_window_tab(report)


if __name__ == "__main__":
    main()
