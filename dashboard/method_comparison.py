"""Render the all-family replay: what each model would have bought, and how it did.

Reading only. Every number is exactly what ``scripts.report_all_method_backtest``
wrote, so this page and the artifact cannot disagree.

Two blocks, in this order on purpose. Coverage first, because it decides how
much the second block is worth: a family whose 80% band caught far less than
80% of outcomes is overconfident, and every probability and threshold read off
that same curve inherits the error. Then the buy rules, each with the number of
positions it took, because a win rate over four trades is not a win rate.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import streamlit as st

from dashboard.presenters import format_percent
from dashboard.ui import display_rows

# Repeated rather than imported: the dashboard is barred from importing the
# notification layer, and that ban is worth more than this one sentence of
# duplication -- it is what stops a page from fetching, training or sending.
# ``test_the_two_surfaces_state_the_same_convention`` pins this against
# notifications.risk_levels so the two cannot drift into describing P90
# differently, which is the failure the shared constant was protecting against.
CONVENTION_NOTE = (
    "Pxx の読み方: その確率で「これ以上になる」水準です。"
    "P90 は90%の確率で上回る水準、つまり下振れ側のリスクです（上振れではありません）。"
    "P95 はさらに悪い側、P50 は中央値です。"
)

ALL_METHODS_DIRECTORY = Path("docs/all_methods")

# Under this many positions a rule's win rate is noise. The same floor the OOS
# page uses, for the same reason.
MINIMUM_POSITIONS_FOR_EVIDENCE = 20


def load_reports(
    directory: Path = ALL_METHODS_DIRECTORY,
) -> list[tuple[str, dict[str, Any]]]:
    """Every saved comparison, newest window last, labelled for a tab."""

    found: list[tuple[str, dict[str, Any]]] = []
    for path in sorted(directory.glob("*.json")):
        try:
            payload = dict(json.loads(path.read_text(encoding="utf-8")))
        except (OSError, ValueError):
            continue
        if "rules" not in payload:
            continue
        label = f"{payload.get('from', '?')} 〜 {payload.get('to', '?')}"
        found.append((label, payload))
    return found


def _coverage_rows(payload: dict[str, Any]) -> list[dict[str, object]]:
    rows = []
    for entry in payload.get("coverage", []):
        covered = entry.get("covered")
        rows.append(
            {
                "手法": entry.get("label", entry.get("arm", "—")),
                "標本": entry.get("samples", 0),
                "実測被覆": format_percent(covered) if covered is not None else "—",
                "名目80%との差": (
                    format_percent(covered - 0.80) if covered is not None else "—"
                ),
                "判定": (
                    "—"
                    if covered is None
                    else "自信過剰"
                    if covered < 0.70
                    else "おおむね妥当"
                ),
            }
        )
    return rows


def _rule_rows(payload: dict[str, Any], rule: str) -> list[dict[str, object]]:
    rows = []
    for entry in payload.get("rules", []):
        if entry.get("rule") != rule:
            continue
        positions = int(entry.get("positions") or 0)
        rows.append(
            {
                "手法": entry.get("label", entry.get("arm", "—")),
                "建玉": positions,
                "取引日": entry.get("sessions", 0),
                "勝率": (
                    format_percent(entry["win_rate"])
                    if entry.get("win_rate") is not None
                    else "—"
                ),
                "平均リターン": format_percent(entry.get("mean_return")),
                "累積リターン": format_percent(entry.get("total_return")),
                "方向的中": (
                    format_percent(entry["direction_accuracy"])
                    if entry.get("direction_accuracy") is not None
                    else "—"
                ),
                "標本": (
                    "十分" if positions >= MINIMUM_POSITIONS_FOR_EVIDENCE else "不足"
                ),
            }
        )

    def _positions(row: dict[str, object]) -> int:
        value = row["建玉"]
        return value if isinstance(value, int) else 0

    return sorted(rows, key=lambda row: -_positions(row))


def _calibration_rows(entry: dict[str, Any]) -> list[dict[str, object]]:
    """One family's levels: what each Pxx claimed, and what actually happened."""

    rows = []
    for level in entry.get("levels", []):
        observed = level.get("observed_exceeded")
        nominal = level.get("nominal_exceeded")
        error = level.get("error")
        rows.append(
            {
                "水準": level.get("label", "—"),
                "予測の平均": format_percent(level.get("mean_predicted")),
                "本来これ以上になる割合": format_percent(nominal),
                "実測": format_percent(observed),
                "差": format_percent(error),
                "標本": level.get("samples", 0),
                "読み": (
                    "—"
                    if error is None
                    else "低めに出ている（慎重）"
                    if error > 0.05
                    else "高めに出ている（外れる）"
                    if error < -0.05
                    else "おおむね一致"
                ),
            }
        )
    return rows


def _threshold_rows(payload: dict[str, Any]) -> list[dict[str, object]]:
    """Each family's hurdle, with the selection and evaluation halves apart.

    Both halves are shown because the gap between them is the only honest
    measure of how much the hurdle owes to having been chosen after the fact.
    """

    rows = []
    for entry in payload.get("thresholds", []):
        threshold = entry.get("threshold")
        rows.append(
            {
                "手法": entry.get("label", entry.get("arm", "—")),
                "閾値": format_percent(threshold) if threshold is not None else "—",
                "選定期 建玉": entry.get("selection_positions", 0),
                "選定期 平均": format_percent(entry.get("selection_mean_return")),
                "評価期 建玉": entry.get("evaluation_positions", 0),
                "評価期 平均": format_percent(entry.get("evaluation_mean_return")),
                "評価期 勝率": (
                    format_percent(entry["evaluation_win_rate"])
                    if entry.get("evaluation_win_rate") is not None
                    else "—"
                ),
                "根拠": (
                    "十分"
                    if int(entry.get("evaluation_positions") or 0)
                    >= MINIMUM_POSITIONS_FOR_EVIDENCE
                    else "不足"
                ),
            }
        )
    return rows


def render_report(payload: dict[str, Any]) -> None:
    """One window's comparison across every model family."""

    sessions = payload.get("sessions", 0)
    st.caption(
        f"{payload.get('from')} 〜 {payload.get('to')}　"
        f"{sessions}営業日 x {payload.get('tickers', 0)}銘柄　"
        f"閾値 {format_percent(payload.get('threshold'))}"
    )
    st.warning(
        "これは当時走っていなかったモデルの再現です。この系の実績ではありません。"
        f"{sessions}営業日では、手法どうしの勝率の差は偶然と区別できません。"
        "どの手法が優れているかを、ここから結論づけないでください。"
    )

    st.subheader("80%区間の被覆")
    st.caption(
        "名目80%の区間に、実際の当日リターンが何%入ったか。"
        "大きく下回る手法は区間が狭すぎ、そこから読んだ確率も同じだけ外れます。"
        "下の買いルールの成績は、この表を見たうえで読んでください。"
    )
    display_rows(_coverage_rows(payload), height=380)

    st.subheader("Pxx別の精度")
    st.caption(CONVENTION_NOTE)
    calibration = payload.get("calibration", [])
    if not calibration:
        st.info("PENDING: 水準ごとの精度がまだ計算されていません。")
    else:
        labels = [str(e.get("label", e.get("arm", "—"))) for e in calibration]
        for entry, tab in zip(calibration, st.tabs(labels), strict=True):
            with tab:
                st.caption(
                    f"平均誤差 {format_percent(entry.get('mean_absolute_error'))}"
                    f"　最悪 {entry.get('worst_level', '—')} "
                    f"{format_percent(entry.get('worst_error'))}"
                )
                display_rows(_calibration_rows(entry), height=300)

    st.subheader("手法ごとの閾値")
    st.caption(
        "閾値は前半の営業日だけで選び、後半では触っていません。"
        "選定期と評価期の差が、そのまま『選んだことによる下駄』の大きさです。"
        "評価期の建玉が少ない行の勝率は読まないでください。"
    )
    thresholds = _threshold_rows(payload)
    if thresholds:
        display_rows(thresholds, height=380)
    else:
        st.info("PENDING: 閾値がまだ導出されていません。")

    st.subheader("買いルール別の成績")
    rules = list(dict.fromkeys(entry.get("rule") for entry in payload.get("rules", [])))
    if not rules:
        st.info("PENDING: ルールの結果がまだありません。")
        return
    for rule, tab in zip(rules, st.tabs([str(r) for r in rules]), strict=True):
        with tab:
            rows = _rule_rows(payload, str(rule))
            display_rows(rows, height=380)
            thin = [r for r in rows if r["標本"] == "不足"]
            if thin:
                st.caption(
                    f"{len(thin)}手法は建玉が{MINIMUM_POSITIONS_FOR_EVIDENCE}件未満です。"
                    "その勝率は読まないでください。0/2 も 2/2 も、この件数では"
                    "何も意味しません。"
                )
