"""Render one three-layer out-of-sample evaluation.

The page this feeds exists because a single headline number hid a real
disagreement: a candidate could improve prediction error and worsen the trading
result, and there was nowhere to see both. So the three layers are drawn as
three blocks, each with the sample it rests on, and the trading block carries
the sentence that says it cannot decide anything on its own.

Reading only. The numbers are exactly what ``scripts.evaluate_predictions``
wrote; nothing here recomputes, fetches, or trains, so the page and the artifact
cannot disagree.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import streamlit as st

from dashboard.presenters import format_percent, format_yen
from dashboard.ui import display_rows

OOS_DIRECTORY = Path("docs/oos")

# Below this many trades the trading block is shown and then disclaimed. The
# same floor the comparison runner and the evening mail use.
MINIMUM_TRADES_FOR_EVIDENCE = 20


def load_evaluations(
    directory: Path = OOS_DIRECTORY,
) -> list[tuple[str, dict[str, Any]]]:
    """Every saved evaluation, newest window last, labelled for a tab."""

    found: list[tuple[str, dict[str, Any]]] = []
    for path in sorted(directory.glob("*.json")):
        try:
            payload = dict(json.loads(path.read_text(encoding="utf-8")))
        except (OSError, json.JSONDecodeError, TypeError, ValueError):
            continue
        label = str(payload.get("label") or path.stem)
        found.append((label, payload))
    return found


def _number(value: Any, digits: int = 4) -> str:
    if value is None:
        return "—"
    return f"{float(value):+.{digits}f}"


def _percent(value: Any, digits: int = 3) -> str:
    if value is None:
        return "—"
    return f"{float(value):+.{digits}%}"


def _render_model(model: dict[str, Any]) -> None:
    st.subheader("Model Layer")
    st.caption(
        f"標本 {model.get('count', 0):,} 予測。"
        "モデルの変更はこの層で判定します。標本が最も大きいためです。"
    )
    slope = model.get("calibration_slope")
    display_rows(
        [
            {
                "指標": "MAE",
                "値": _percent(model.get("mae"), 4),
                "理想": "小さいほど良い",
            },
            {
                "指標": "RMSE",
                "値": _percent(model.get("rmse"), 4),
                "理想": "小さいほど良い",
            },
            {
                "指標": "Pearson",
                "値": _number(model.get("pearson")),
                "理想": "大きいほど良い",
            },
            {
                "指標": "Spearman",
                "値": _number(model.get("spearman")),
                "理想": "大きいほど良い",
            },
            {
                "指標": "Bias(予測 - 実績)",
                "値": _percent(model.get("bias"), 4),
                "理想": "0",
            },
            {"指標": "Calibration slope", "値": _number(slope), "理想": "1.0"},
            {
                "指標": "Calibration intercept",
                "値": _number(model.get("calibration_intercept"), 5),
                "理想": "0",
            },
            {
                "指標": "Direction accuracy",
                "値": f"{float(model.get('direction_accuracy', 0)):.2%}",
                "理想": "50%より上",
            },
        ]
    )
    if slope is not None and float(slope) < 0.5:
        st.warning(
            f"Calibration slope が {float(slope):.3f} です（理想 1.0）。"
            "予測の順位はともかく、水準は実績の"
            f"{1 / max(float(slope), 1e-9):.0f}倍に振れています。"
            "過去のOOS予測と実績の関係を使った補正層が、現在ありません。"
        )


def _render_quantiles(rows: list[dict[str, Any]]) -> None:
    if not rows:
        return
    st.subheader("予測値の分位")
    st.caption(
        "予測が高い銘柄ほど実績も高いか。単調でなければ、"
        "予測値に閾値を置くこと自体に意味がありません。"
    )
    display_rows(
        [
            {
                "分位": f"Q{row.get('quantile')}",
                "件数": f"{row.get('count', 0):,}",
                "予測平均": _percent(row.get("predicted_mean")),
                "実績平均": _percent(row.get("actual_mean")),
                "上昇率": f"{float(row.get('win_rate', 0)):.1%}",
            }
            for row in rows
        ]
    )


def _render_selection(selection: dict[str, Any]) -> None:
    st.subheader("Selection Layer")
    sessions = selection.get("sessions", 0)
    st.caption(
        f"標本 {sessions:,} 営業日。同じ日の銘柄は一緒に動くので、"
        "情報を運ぶ単位は取引ではなく営業日です。"
    )
    display_rows(
        [
            {
                "指標": "Daily Rank IC 平均",
                "値": _number(selection.get("rank_ic_mean")),
                "t値": _number(selection.get("rank_ic_t"), 2),
            },
            {
                "指標": "Universe 平均実績",
                "値": _percent(selection.get("universe_mean")),
                "t値": "—",
            },
            {
                "指標": "Top1 - Universe",
                "値": _percent(selection.get("top1_alpha")),
                "t値": "—",
            },
            {
                "指標": "Top3 - Universe",
                "値": _percent(selection.get("top3_alpha")),
                "t値": "—",
            },
            {
                "指標": "Top5 - Universe",
                "値": _percent(selection.get("top5_alpha")),
                "t値": _number(selection.get("top5_alpha_t"), 2),
            },
            {
                "指標": "Top5 - Bottom5",
                "値": _percent(selection.get("top_bottom_spread")),
                "t値": "—",
            },
        ]
    )


def _render_probability(probability: dict[str, Any]) -> None:
    st.subheader("Probability Layer")
    brier = probability.get("brier")
    base = float(probability.get("base_rate", 0.5))
    constant_brier = base * (1 - base)
    st.caption(
        f"標本 {probability.get('count', 0):,} 予測。"
        f"常に50%と答えるだけの Brier は {constant_brier:.4f} です。"
    )
    if brier is not None and float(brier) >= constant_brier:
        st.error(
            f"Brier {float(brier):.4f} は、常に50%と答える {constant_brier:.4f} "
            "以上です。この確率は無情報より良くないので、閾値0.60は根拠を持ちません。"
        )
    display_rows(
        [
            {
                "確率帯": (
                    f"{float(row.get('low', 0)):.0%}"
                    f"-{float(row.get('high', 0)):.0%}"
                ),
                "件数": f"{row.get('count', 0):,}",
                "平均予測確率": f"{float(row.get('mean_predicted', 0)):.1%}",
                "実際の上昇率": f"{float(row.get('actual_up_rate', 0)):.1%}",
            }
            for row in probability.get("bins", [])
        ]
    )


def _render_trading(trading: dict[str, Any]) -> None:
    st.subheader("Trading Layer")
    trades = int(trading.get("trades", 0))
    st.caption(
        f"取引 {trades:,}件 / {trading.get('sessions', 0):,}営業日。"
        "この層はモデルの採否には使いません。標本が最も小さいためです。"
    )
    display_rows(
        [
            {"項目": "Gross", "値": format_yen(trading.get("gross_jpy"))},
            {"項目": "Cost", "値": format_yen(-abs(float(trading.get("cost_jpy", 0))))},
            {"項目": "Net", "値": format_yen(trading.get("net_jpy"))},
            {"項目": "Profit factor", "値": _number(trading.get("profit_factor"), 2)},
            {"項目": "Expectancy", "値": format_yen(trading.get("expectancy_jpy"))},
            {
                "項目": "Win rate",
                "値": format_percent(trading.get("win_rate")),
            },
            {"項目": "Payoff", "値": _number(trading.get("payoff_ratio"), 2)},
            {
                "項目": "勝ち日 / 負け日",
                "値": (
                    f"{trading.get('winning_days', 0)}"
                    f" / {trading.get('losing_days', 0)}"
                ),
            },
            {"項目": "日次 Sharpe", "値": _number(trading.get("daily_sharpe"), 2)},
            {"項目": "日次 Sortino", "値": _number(trading.get("daily_sortino"), 2)},
            {
                "項目": "最大ドローダウン",
                "値": format_yen(trading.get("max_drawdown_jpy")),
            },
        ]
    )
    if trades < MINIMUM_TRADES_FOR_EVIDENCE:
        st.warning(
            f"{trades}取引では勝率も損益も優位性の証拠になりません。参考値です。"
        )
    net = float(trading.get("net_jpy", 0))
    drawdown = abs(float(trading.get("max_drawdown_jpy", 0)))
    if net > 0 and drawdown > net:
        st.warning(
            f"累積 {format_yen(net)} に対し、途中の最大ドローダウンは "
            f"{format_yen(-drawdown)} です。利益より下振れのほうが大きい形です。"
        )


def render_evaluation(evaluation: dict[str, Any]) -> None:
    """Draw one evaluation, layer by layer, with each layer's sample size."""

    st.info(
        "これは研究のOOS評価であり、実際の売買記録ではありません。"
        "BUY と表示されていても、実資金での購入推奨ではありません。"
    )
    _render_model(dict(evaluation.get("model", {})))
    _render_quantiles(list(evaluation.get("quantiles", [])))
    st.divider()
    _render_selection(dict(evaluation.get("selection", {})))
    st.divider()
    _render_probability(dict(evaluation.get("probability", {})))
    st.divider()
    _render_trading(dict(evaluation.get("trading", {})))
