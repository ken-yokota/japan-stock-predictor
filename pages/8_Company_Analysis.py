"""Per-company view of which indicators drove that company's predictions.

Factor Analysis answers "what does the model weigh right now". This page answers
a narrower question for one company: which indicators carried weight on each
day, how those weights moved, and which indicators the model only started using
partway through the window.

"Newly appeared" is defined against the regularized fit rather than the feature
list. Ridge and its relatives push irrelevant features toward zero, so a
coefficient crossing from exactly zero to non-zero is the model beginning to use
that indicator for that company. A feature present in every fit but always zero
was never actually used.

Everything shown is read from the artifacts written by ``python -m cli
week-test``; nothing is recomputed here. Which run is on screen is chosen by
the reader, because a coefficient only means something next to the window and
the weighting that produced it: the same company fitted on 120 sessions and on
250 sessions is two different models, not two views of one.
"""

from __future__ import annotations

from typing import Any

import pandas as pd  # type: ignore[import-untyped]
import streamlit as st

from dashboard.catalog import sector_label, stock_label
from dashboard.factors import newly_influential_features
from dashboard.presenters import format_number, format_percent, format_yen
from dashboard.research_artifacts import WEEK_TEST_DIRECTORY, labelled_runs
from dashboard.ui import configure_page, display_rows, render_header


def _prediction_history(report: dict[str, Any], ticker: str) -> pd.DataFrame:
    rows = [
        row for row in report.get("predictions", []) if str(row.get("ticker")) == ticker
    ]
    if not rows:
        return pd.DataFrame()
    return pd.DataFrame(rows).sort_values("date")


def _render_prediction_series(frame: pd.DataFrame) -> None:
    """Show the company's predicted vs realized path over the window."""

    st.subheader("予測値の推移")
    st.caption(
        "この銘柄について、各営業日の予測と実績を時系列で並べたものです。"
        "BUY条件を満たさなかった日も含め、モデルが出した予測をすべて載せています。"
    )

    display_rows(
        [
            {
                "日付": row["date"],
                "社名": stock_label(str(row["ticker"])),
                "結果": (
                    "—"
                    if row["signal"] != "BUY"
                    else ("勝ち" if float(row["net_profit_jpy"]) > 0 else "負け")
                ),
                "予測リターン": format_percent(row["predicted_return"]),
                "上昇確率": format_percent(row["probability_up"]),
                "実績リターン": format_percent(row["actual_return"]),
                "予測 終値-寄付(1株)": format_yen(
                    row.get("predicted_price_difference")
                ),
                "実績 終値-寄付(1株)": format_yen(row.get("actual_price_difference")),
                "買値(寄付)": format_number(row.get("actual_open"), digits=1),
                "売値(大引)": format_number(row.get("actual_close"), digits=1),
                "判定": row["signal"],
                "方向": "OK" if row["direction_correct"] else "NG",
            }
            for row in frame.to_dict("records")
        ],
        height=460,
    )

    chart = pd.DataFrame(
        {
            "日付": frame["date"],
            "予測リターン": frame["predicted_return"],
            "実績リターン": frame["actual_return"],
        }
    ).set_index("日付")
    st.line_chart(chart, use_container_width=True)
    st.caption(
        "2本の線が近いほど、その銘柄をうまく読めていたことになります。"
        "符号 (プラスかマイナスか)が一致しているかを先に見てください。"
    )


def _render_coefficients(report: dict[str, Any], ticker: str) -> None:
    """Show which indicators carried weight for this company, and how they moved."""

    records = [
        row
        for row in report.get("company_coefficients", [])
        if str(row.get("ticker")) == ticker
    ]
    if not records:
        st.warning(
            "PENDING: この銘柄の係数履歴がありません。"
            "`python -m cli week-test` を実行し直すと生成されます。"
        )
        return

    frame = pd.DataFrame(records).sort_values(["date", "feature"])
    dates = sorted({str(value) for value in frame["date"]})

    st.subheader("どの指標が、どの係数で効いていたか")
    st.caption(
        "標準化後の係数です。プラスなら上げ要因、マイナスなら下げ要因として"
        "働いていました。同じモデル内でのみ大小を比較できます。"
        "係数が大きいことは「その指標を見れば儲かる」という意味ではありません。"
    )

    latest = frame.loc[frame["date"] == dates[-1]].copy()
    latest["abs"] = latest["coefficient"].abs()
    latest = latest.sort_values("abs", ascending=False)
    active = latest.loc[latest["active"]]

    columns = st.columns(3)
    columns[0].metric("集計した営業日数", str(len(dates)))
    columns[1].metric("使われた指標数", f"{len(active)} / {len(latest)}")
    columns[2].metric("新たに使われ始めた指標", str(int(frame["first_seen"].sum())))

    display_rows(
        [
            {
                "指標": row["feature"],
                "係数": format_number(row["coefficient"], digits=5),
                "向き": (
                    "上げ要因"
                    if float(row["coefficient"]) > 0
                    else "下げ要因"
                    if float(row["coefficient"]) < 0
                    else "未使用"
                ),
                "前日差": (
                    "—"
                    if row.get("change_from_previous_day") is None
                    else format_number(row["change_from_previous_day"], digits=5)
                ),
            }
            for row in latest.to_dict("records")
        ],
        height=420,
    )
    st.caption(f"最終日 ({dates[-1]})の係数を、影響の大きい順に並べています。")

    st.subheader("係数の時系列推移")
    features = sorted({str(value) for value in frame["feature"]})
    influence = (
        frame.groupby("feature")["coefficient"]
        .apply(lambda values: values.abs().mean())
        .sort_values(ascending=False)
    )
    default = [str(name) for name in influence.head(6).index]
    chosen = st.multiselect("表示する指標", features, default=default)
    if chosen:
        pivot = frame.loc[frame["feature"].isin(chosen)].pivot_table(
            index="date", columns="feature", values="coefficient", aggfunc="mean"
        )
        st.line_chart(pivot, use_container_width=True)
        st.caption(
            "線が上下に振れている指標は、日によって効き方が変わっていたことを"
            "意味します。符号がまたいで反転する指標は信頼性が低いと考えてください。"
        )

    _render_new_indicators(frame)


def _render_newly_influential(frame: pd.DataFrame, *, top: int = 5) -> None:
    """Show features that first broke into the strongest weights.

    Ridge never drives a coefficient to exactly zero, so "started being used"
    cannot be detected by a zero crossing. What can be detected is when an
    indicator first became one of the strongest influences.
    """

    rows = frame.rename(
        columns={"feature": "feature_name", "date": "training_end"}
    ).copy()
    rows["model_run_id"] = rows["training_end"]
    appeared = newly_influential_features(rows.to_dict("records"), top=top)

    st.caption(f"影響上位{top}に新しく入った指標")
    if not appeared:
        st.info("上位の顔ぶれは期間を通じて変わりませんでした。")
        return
    display_rows(
        [
            {
                "初めて上位に入った日": str(row["first_top_on"]),
                "指標": row["feature"],
                "その日の係数": format_number(row["coefficient"], digits=5),
                "順位": row["rank"],
            }
            for row in appeared
        ]
    )
    st.caption(
        "順位は係数の絶対値の大きさです。"
        "Ridgeは効かない指標も0にはしないため、「使われ始めた」ではなく"
        "「強く効き始めた」で判定しています。"
    )


def _render_new_indicators(frame: pd.DataFrame) -> None:
    """List indicators the model only began using partway through the window."""

    st.subheader("新しく現れた指標")
    appeared = frame.loc[frame["first_seen"]].copy()
    first_date = frame["date"].min()
    # A feature active on the very first fit was not "new"; it was there from
    # the start of the observed window.
    appeared = appeared.loc[appeared["date"] > first_date]

    if appeared.empty:
        st.info(
            "係数がちょうど0から動いた指標はありません。"
            "本番モデルのRidgeは係数を0に潰さないため、この条件は通常成立しません。"
            "代わりに、下の「影響上位に新しく入った指標」を見てください。"
        )
        _render_newly_influential(frame)
        return

    display_rows(
        [
            {
                "初めて使われた日": row["date"],
                "指標": row["feature"],
                "その日の係数": format_number(row["coefficient"], digits=5),
                "向き": "上げ要因" if float(row["coefficient"]) > 0 else "下げ要因",
            }
            for row in appeared.sort_values(["date", "feature"]).to_dict("records")
        ]
    )
    _render_newly_influential(frame)
    st.caption(
        "係数がちょうど0から0以外に変わった日を「初めて使われた日」としています。"
        "Ridgeのような正則化モデルは効かない指標の係数を0に潰すため、"
        "0を抜けた時点がモデルがその指標を使い始めた時点になります。"
    )


def main() -> None:
    configure_page("Company Analysis", "🏢")
    render_header(
        "Company Analysis",
        "企業ごとに、どの指標がどの係数で予測を動かしたかと、予測値の推移を表示します。",
    )

    runs = labelled_runs(WEEK_TEST_DIRECTORY)
    if not runs:
        st.warning(
            f"PENDING: 検証結果が `{WEEK_TEST_DIRECTORY}` にありません。先に "
            "`python -m cli week-test` を実行してください。"
        )
        return

    selectors = st.columns(2)
    # Longest window last in the list, so default to it: it holds the most
    # predictions and therefore the most trustworthy per-company picture.
    chosen_run = selectors[0].selectbox(
        "検証期間と学習設定",
        [label for label, _ in runs],
        index=0,
        key="company_run",
    )
    report = dict(runs)[chosen_run]

    window = report.get("generated_for", {})
    training = report.get("training", {})
    tickers = sorted({str(row["ticker"]) for row in report.get("predictions", [])})
    if not tickers:
        st.info("PENDING: この検証結果には予測が入っていません。")
        return

    ticker = selectors[1].selectbox(
        "企業を選ぶ", tickers, format_func=stock_label, key="company_ticker"
    )
    half_life = training.get("recency_half_life_sessions")
    feature_set = report.get("feature_set", {})
    st.caption(
        f"{stock_label(ticker)} ({sector_label(ticker)}) /  "
        f"対象期間 {window.get('from', '?')} 〜 {window.get('to', '?')} /  "
        f"学習 直前{window.get('training_window_sessions', '?')}営業日 /  "
        + (
            "履歴の重み: 全期間を等しく"
            if half_life is None
            else f"履歴の重み: 直近重視 (半減期{half_life}営業日)"
        )
        + f" /  予測要素 {feature_set.get('feature_count', '—')}個"
        + (f" ({feature_set.get('name')})" if feature_set.get("name") else "")
    )
    st.caption(
        f"この期間に予測された銘柄は {len(tickers)} 社です。"
        "係数は標準化後の値なので、同じ銘柄・同じ検証結果の中でだけ大小を比較できます。"
        "学習設定が違う結果どうしの係数を並べても意味がありません。"
    )

    history = _prediction_history(report, ticker)
    if history.empty:
        st.info("この銘柄の予測がありません。")
        return

    _render_prediction_series(history)
    st.divider()
    _render_coefficients(report, ticker)

    for caveat in report.get("caveats", []):
        st.caption(f"注意: {caveat}")


if __name__ == "__main__":
    main()
