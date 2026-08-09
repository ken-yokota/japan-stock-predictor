"""Tomorrow's prediction, and which indicators actually reached it.

Two questions are answered on one page because they are the same question. A
prediction that lost its FX and futures inputs looks identical to one that used
them -- same table, same numbers, same confidence -- and the difference only
shows up in per-symbol warnings that no page displayed. So the inputs are shown
beside the output, not on a separate screen.

The distinction that matters is between an indicator the model never saw and an
indicator it saw and merely flagged. Collapsing those two into one "warnings"
list is what let twelve missing series go unnoticed.

Read from ``artifacts/preview/latest.json``, written by ``python -m cli
preview``. Nothing is recomputed here, and the preview is never published to
the database.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import streamlit as st

from dashboard.catalog import sector_label, stock_label
from dashboard.presenters import format_number, format_percent
from dashboard.research_artifacts import load_artifact
from dashboard.ui import configure_page, display_rows, render_header

PREVIEW_PATH = Path("artifacts/preview/latest.json")

# Indicator ids are stored as configured; readers think in Japanese names.
INDICATOR_LABELS: dict[str, str] = {
    "nikkei225_futures": "日経225先物",
    "sp500_futures": "S&P500先物",
    "nasdaq100_futures": "NASDAQ100先物",
    "usdjpy": "USD/JPY",
    "eurjpy": "EUR/JPY",
    "audjpy": "AUD/JPY",
    "dollar_index": "ドル指数",
    "wti": "WTI原油",
    "brent": "Brent原油",
    "gold": "金",
    "copper": "銅",
    "natural_gas": "天然ガス",
    "sp500": "S&P500",
    "nasdaq100": "NASDAQ100",
    "dow": "ダウ平均",
    "vix": "VIX",
    "fxi": "中国大型株ETF",
    "mchi": "MSCI中国ETF",
    "ewy": "韓国株ETF",
    "xle": "米エネルギーETF",
    "xlf": "米金融ETF",
    "xli": "米資本財ETF",
    "kre": "米地銀ETF",
    "oih": "米石油サービスETF",
    "baltic_dry_index": "バルチック海運指数(代替)",
    "toyota_adr": "トヨタADR",
    "honda_adr": "ホンダADR",
    "mufg_adr": "三菱UFJ ADR",
    "smfg_adr": "三井住友FG ADR",
}


def _label(indicator: str) -> str:
    return INDICATOR_LABELS.get(indicator, indicator)


def _render_predictions(report: dict[str, Any]) -> None:
    rows = report.get("predictions", [])
    rule = report.get("rule", {})
    buys = [row for row in rows if row.get("signal") == "BUY"]

    headline = st.columns(3)
    headline[0].metric("予測", f"{len(rows)} 銘柄")
    headline[1].metric("BUY", f"{len(buys)} 銘柄")
    headline[2].metric("対象日", str(report.get("prediction_date", "—")))
    st.caption(
        f"BUY条件: 予測リターン > {float(rule.get('return_threshold', 0)) * 100:.2f}% "
        f"かつ 上昇確率 >= {float(rule.get('probability_threshold', 0)) * 100:.0f}%。"
        "両方を同時に満たした銘柄だけがBUYです。"
    )

    def as_row(row: dict[str, Any]) -> dict[str, Any]:
        return {
            "銘柄": stock_label(str(row["ticker"])),
            "業種": sector_label(str(row["ticker"])),
            "判定": row.get("signal") or row.get("status"),
            "予測リターン": format_percent(row.get("predicted_return")),
            "上昇確率": format_percent(row.get("probability_up")),
            "予測終値": format_number(row.get("predicted_close"), digits=1),
        }

    if buys:
        st.subheader("BUY 判定")
        display_rows([as_row(row) for row in buys])
    else:
        st.info("この日はBUY条件を満たした銘柄がありません。")

    st.subheader("全銘柄 (予測リターン順)")
    display_rows([as_row(row) for row in rows], height=560)


def _render_indicators(report: dict[str, Any]) -> None:
    indicators = report.get("indicators", {})
    excluded = indicators.get("excluded", {})
    labelled = indicators.get("quality_labelled", {})

    st.subheader("この予測に使われた指標・使われなかった指標")

    if excluded:
        st.error(
            f"**{len(excluded)}系列がモデルに届いていません。** "
            "予測時点で値が利用可能と判定されず、除外されました。"
        )
        display_rows(
            [
                {
                    "指標": _label(name),
                    "id": name,
                    "状態": "除外 (モデルは未使用)",
                    "理由": " / ".join(reasons),
                }
                for name, reasons in excluded.items()
            ]
        )
        st.caption(
            "リアルタイム系の指標は、予測時刻の直前に取得したスナップショットが"
            "必要です。取得が早すぎると鮮度の判定で弾かれ、結果としてモデルは"
            "その指標を一度も見ないまま予測を出します。"
            "**予測が出ていることと、意図した指標が使われていることは別です。**"
        )
    else:
        st.success("除外された指標はありません。")

    if labelled:
        with st.expander(
            f"品質ラベルが付いた指標 {len(labelled)}系列 (これらは使われています)",
            expanded=False,
        ):
            st.caption(
                "こちらは除外ではありません。無料・非公式データであることを示す"
                "注記が付いているだけで、値はモデルに渡っています。"
            )
            display_rows(
                [
                    {
                        "指標": _label(name),
                        "id": name,
                        "状態": "使用 (注記あり)",
                        "注記": " / ".join(reasons),
                    }
                    for name, reasons in labelled.items()
                ]
            )


def main() -> None:
    configure_page("予測プレビュー", "🔭")
    render_header(
        "予測プレビュー",
        "次の営業日の予測と、その予測に実際に使われた指標を表示します。",
    )

    report = load_artifact(PREVIEW_PATH)
    if report is None:
        st.warning(
            f"PENDING: `{PREVIEW_PATH}` がありません。先に "
            "`python -m cli preview` を実行してください。"
        )
        return

    generated = str(report.get("generated_at", ""))[:16].replace("T", " ")
    st.caption(
        f"算出 {generated} JST / 本番と同じコード・DB・設定で計算 / "
        "**DBには保存していません**"
    )

    _render_predictions(report)
    st.divider()
    _render_indicators(report)

    failures = report.get("failures") or {}
    if failures:
        with st.expander(f"予測できなかった銘柄 {len(failures)}件", expanded=False):
            display_rows(
                [
                    {"銘柄": stock_label(str(key)), "理由": str(value)}
                    for key, value in failures.items()
                ]
            )

    st.divider()
    for caveat in report.get("caveats", []):
        st.caption(f"注意: {caveat}")


if __name__ == "__main__":
    main()
