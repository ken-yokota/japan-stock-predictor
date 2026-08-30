"""The all-family comparison page.

The page's job is not to show numbers -- it is to stop the numbers being read
as more than they are. Fourteen sessions of twenty-two names cannot separate
ten families, and a rule that took three positions has no win rate. So most of
what is tested here is that the caveats survive rendering, and that a thin
sample is labelled thin rather than sorted to the top of a leaderboard.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from dashboard.method_comparison import (
    MINIMUM_POSITIONS_FOR_EVIDENCE,
    _coverage_rows,
    _rule_rows,
    load_reports,
)


def _payload(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "from": "2026-08-07",
        "to": "2026-08-28",
        "sessions": 14,
        "tickers": 22,
        "threshold": 0.003,
        "coverage": [
            {"arm": "ridge", "label": "Ridge回帰", "samples": 300, "covered": 0.78},
            {"arm": "lstm", "label": "LSTM", "samples": 300, "covered": 0.31},
            {"arm": "broken", "label": "壊れた", "samples": 0, "covered": None},
        ],
        "rules": [
            {
                "arm": "ridge",
                "label": "Ridge回帰",
                "rule": "中央値(p50)>0.3%",
                "positions": 40,
                "sessions": 12,
                "wins": 22,
                "win_rate": 0.55,
                "mean_return": 0.001,
                "total_return": 0.012,
                "direction_accuracy": 0.54,
            },
            {
                "arm": "lstm",
                "label": "LSTM",
                "rule": "中央値(p50)>0.3%",
                "positions": 3,
                "sessions": 2,
                "wins": 3,
                "win_rate": 1.0,
                "mean_return": 0.02,
                "total_return": 0.04,
                "direction_accuracy": 1.0,
            },
        ],
    }
    base.update(overrides)
    return base


def test_a_thin_sample_is_labelled_thin_however_good_it_looks() -> None:
    """A 100% win rate over three trades must not read as the best method."""

    rows = _rule_rows(_payload(), "中央値(p50)>0.3%")
    thin = next(row for row in rows if row["手法"] == "LSTM")
    assert thin["勝率"] == "100.00%"
    assert thin["標本"] == "不足"
    solid = next(row for row in rows if row["手法"] == "Ridge回帰")
    assert solid["標本"] == "十分"


def test_rows_are_ordered_by_positions_not_by_win_rate() -> None:
    """Sorting by win rate would put the three-trade row on top."""

    rows = _rule_rows(_payload(), "中央値(p50)>0.3%")
    assert [row["手法"] for row in rows] == ["Ridge回帰", "LSTM"]


def test_a_badly_covered_band_is_called_overconfident() -> None:
    rows = {row["手法"]: row for row in _coverage_rows(_payload())}
    assert rows["LSTM"]["判定"] == "自信過剰"
    assert rows["Ridge回帰"]["判定"] == "おおむね妥当"


def test_a_family_with_no_measurable_coverage_is_not_judged() -> None:
    rows = {row["手法"]: row for row in _coverage_rows(_payload())}
    assert rows["壊れた"]["実測被覆"] == "—"
    assert rows["壊れた"]["判定"] == "—"


def test_a_rule_nobody_traded_produces_no_rows_rather_than_zeroes() -> None:
    assert _rule_rows(_payload(), "存在しないルール") == []


def test_the_evidence_floor_is_the_same_one_the_rest_of_the_system_uses() -> None:
    assert MINIMUM_POSITIONS_FOR_EVIDENCE == 20


def test_only_scored_artifacts_are_offered_as_tabs(tmp_path: Path) -> None:
    """A raw fitting artifact has no rules yet and must not appear as a report."""

    (tmp_path / "scored.json").write_text(json.dumps(_payload()), encoding="utf-8")
    (tmp_path / "raw.json").write_text(
        json.dumps({"from": "x", "to": "y", "rows": []}), encoding="utf-8"
    )
    (tmp_path / "broken.json").write_text("{ not json", encoding="utf-8")
    loaded = load_reports(tmp_path)
    assert [label for label, _ in loaded] == ["2026-08-07 〜 2026-08-28"]


# --- the two sections the operator's request centres on ------------------


def _calibrated() -> dict[str, Any]:
    return {
        "label": "Ridge回帰",
        "arm": "ridge",
        "mean_absolute_error": 0.06,
        "worst_level": "P90",
        "worst_error": -0.18,
        "levels": [
            {
                "label": "P90",
                "level": 0.10,
                "nominal_exceeded": 0.90,
                "observed_exceeded": 0.72,
                "error": -0.18,
                "samples": 330,
                "mean_predicted": -0.021,
            },
            {
                "label": "P50",
                "level": 0.50,
                "nominal_exceeded": 0.50,
                "observed_exceeded": 0.51,
                "error": 0.01,
                "samples": 330,
                "mean_predicted": 0.001,
            },
            {
                "label": "P75",
                "level": 0.25,
                "nominal_exceeded": 0.75,
                "observed_exceeded": 0.83,
                "error": 0.08,
                "samples": 330,
                "mean_predicted": -0.008,
            },
        ],
    }


def test_a_level_that_overstates_itself_is_called_out_as_such() -> None:
    """P90 claiming nine sessions in ten but delivering seven is the finding."""

    from dashboard.method_comparison import _calibration_rows

    rows = {row["水準"]: row for row in _calibration_rows(_calibrated())}
    assert rows["P90"]["読み"] == "高めに出ている（外れる）"
    assert rows["P50"]["読み"] == "おおむね一致"
    assert rows["P75"]["読み"] == "低めに出ている（慎重）"


def test_the_threshold_table_keeps_both_halves_apart() -> None:
    """One combined number would hide the advantage of having chosen."""

    from dashboard.method_comparison import _threshold_rows

    payload = {
        "thresholds": [
            {
                "arm": "xgboost",
                "label": "XGBoost",
                "threshold": 0.003,
                "selection_positions": 31,
                "selection_mean_return": 0.0076,
                "evaluation_positions": 42,
                "evaluation_mean_return": -0.0014,
                "evaluation_win_rate": 0.43,
            },
            {
                "arm": "lstm",
                "label": "LSTM",
                "threshold": 0.008,
                "selection_positions": 9,
                "selection_mean_return": 0.0095,
                "evaluation_positions": 4,
                "evaluation_mean_return": 0.0154,
                "evaluation_win_rate": 1.0,
            },
        ]
    }
    rows = {row["手法"]: row for row in _threshold_rows(payload)}
    # The selection half looked good and the evaluation half did not; both
    # must be visible side by side.
    assert rows["XGBoost"]["選定期 平均"] == "0.76%"
    assert rows["XGBoost"]["評価期 平均"] == "-0.14%"
    assert rows["XGBoost"]["根拠"] == "十分"
    # A 100% win rate over four trades must be labelled, not celebrated.
    assert rows["LSTM"]["評価期 勝率"] == "100.00%"
    assert rows["LSTM"]["根拠"] == "不足"


def test_a_family_with_no_hurdle_shows_a_dash_not_a_zero() -> None:
    from dashboard.method_comparison import _threshold_rows

    rows = _threshold_rows(
        {"thresholds": [{"arm": "logistic", "label": "ロジ", "threshold": None}]}
    )
    assert rows[0]["閾値"] == "—"


def test_the_two_surfaces_state_the_same_convention() -> None:
    """The dashboard may not import notifications, so the sentence is repeated.

    Repeating it is the lesser evil: the import ban is what stops a page from
    fetching, training or sending. What the ban cannot prevent is the two
    copies drifting until one of them describes P90 as the upside again, which
    is the exact confusion the convention was introduced to end. So they are
    pinned equal here, where importing both is allowed.
    """

    from dashboard.method_comparison import CONVENTION_NOTE as dashboard_note
    from notifications.risk_levels import CONVENTION_NOTE as mail_note

    assert dashboard_note == mail_note
