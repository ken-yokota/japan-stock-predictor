from __future__ import annotations

from pathlib import Path

import pytest

from dashboard.factors import (
    BuyRule,
    buy_rule_mismatches,
    coefficient_matrix,
    coefficient_summary_rows,
    coefficient_timeline,
    load_configured_buy_rule,
    newly_active_features,
    newly_influential_features,
    summarize_coefficients,
)

_CONFIG = """
version: 1
signal:
  predicted_intraday_return_threshold: 0.005
  probability_up_threshold: 0.65
  return_comparison: strict_greater_than
  probability_comparison: greater_than_or_equal
  insufficient_data_status: INSUFFICIENT_DATA
position:
  capital_per_stock_jpy: 2000000
  quantity_method: floor_capital_div_open
  lot_size: 100
  lot_size_status: confirmed
  carry_overnight: false
costs:
  commission_bps_per_side: 3.0
  slippage_bps_per_side: 4.0
  assumptions_status: confirmed
prediction_price:
  morning_reference: previous_close
  recompute_after_actual_open: true
"""


def _coefficient_rows() -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for index, day in enumerate(["2026-08-03", "2026-08-04", "2026-08-05"]):
        rows.append(
            {
                "model_run_id": f"run-{index}",
                "training_end": day,
                "feature_name": "usdjpy_return_1d",
                "coefficient": 0.10 + index * 0.01,
            }
        )
        rows.append(
            {
                "model_run_id": f"run-{index}",
                "training_end": day,
                "feature_name": "flip_flop",
                "coefficient": 0.05 if index % 2 == 0 else -0.05,
            }
        )
    return rows


def test_configured_rule_is_read_from_the_repository_config() -> None:
    rule = load_configured_buy_rule()

    assert rule is not None
    assert rule.return_threshold == pytest.approx(0.003)
    assert rule.probability_threshold == pytest.approx(0.60)
    assert rule.lot_size == 100


def test_configured_rule_reads_the_supplied_file(tmp_path: Path) -> None:
    path = tmp_path / "trading.yaml"
    path.write_text(_CONFIG, encoding="utf-8")

    rule = load_configured_buy_rule(path)

    assert rule is not None
    assert rule.return_threshold == pytest.approx(0.005)
    assert rule.capital_per_stock == pytest.approx(2_000_000.0)
    assert rule.commission_bps_per_side == pytest.approx(3.0)
    assert "0.50%" in rule.summary
    assert "65%" in rule.summary


@pytest.mark.parametrize(
    "content",
    ["", "not: a mapping we expect", "signal: {}\nposition: {}\ncosts: {}\n", "[]"],
)
def test_unusable_config_returns_none_rather_than_a_guess(
    tmp_path: Path, content: str
) -> None:
    path = tmp_path / "trading.yaml"
    path.write_text(content, encoding="utf-8")

    assert load_configured_buy_rule(path) is None


def test_missing_config_file_returns_none(tmp_path: Path) -> None:
    assert load_configured_buy_rule(tmp_path / "absent.yaml") is None


def test_matching_stored_thresholds_produce_no_mismatch() -> None:
    rule = BuyRule(0.003, 0.60, 1_000_000.0, 100, 5.0, 5.0, "config/trading.yaml")
    rows = [
        {
            "return_threshold": 0.003,
            "probability_threshold": 0.60,
            "prediction_count": 22,
            "first_date": "2026-08-03",
            "last_date": "2026-08-07",
        }
    ]

    assert buy_rule_mismatches(rule, rows) == []


def test_config_drift_from_stored_signals_is_reported() -> None:
    rule = BuyRule(0.005, 0.65, 1_000_000.0, 100, 5.0, 5.0, "config/trading.yaml")
    rows = [
        {
            "return_threshold": 0.003,
            "probability_threshold": 0.60,
            "prediction_count": 22,
            "first_date": "2026-08-03",
            "last_date": "2026-08-07",
        }
    ]

    messages = buy_rule_mismatches(rule, rows)

    assert len(messages) == 1
    assert "0.30%" in messages[0]
    assert "60%" in messages[0]


def test_mismatch_check_is_skipped_without_a_configured_rule() -> None:
    assert buy_rule_mismatches(None, [{"return_threshold": 0.003}]) == []


def test_coefficient_matrix_is_ordered_oldest_first() -> None:
    matrix = coefficient_matrix(_coefficient_rows())

    assert list(matrix.columns) == ["flip_flop", "usdjpy_return_1d"]
    assert len(matrix) == 3
    assert matrix["usdjpy_return_1d"].tolist() == pytest.approx([0.10, 0.11, 0.12])


def test_empty_rows_produce_an_empty_matrix() -> None:
    assert coefficient_matrix([]).empty


def test_summary_reports_direction_and_sign_consistency() -> None:
    report, fits = summarize_coefficients(_coefficient_rows(), lookback=120)

    assert fits == 3
    steady = report["usdjpy_return_1d"]
    assert steady.mean_coefficient > 0.0
    assert steady.sign_consistency == pytest.approx(1.0)

    alternating = report["flip_flop"]
    assert alternating.sign_consistency < 1.0
    assert alternating.stability_score < steady.stability_score


def test_lookback_limits_the_fits_used() -> None:
    _, fits = summarize_coefficients(_coefficient_rows(), lookback=2)

    assert fits == 2


def test_summary_rows_are_ordered_by_absolute_influence() -> None:
    report, _ = summarize_coefficients(_coefficient_rows())

    rows = coefficient_summary_rows(report)

    assert rows[0]["指標 (Feature)"] == "usdjpy_return_1d"
    assert rows[0]["向き"] == "上げ要因"
    assert str(rows[0]["平均係数"]).startswith("+")


def test_summary_rows_respect_the_top_limit() -> None:
    report, _ = summarize_coefficients(_coefficient_rows())

    assert len(coefficient_summary_rows(report, top=1)) == 1


def _timeline_rows() -> list[dict[str, object]]:
    """Three fits: one steady feature, one that only becomes active on day 2."""

    rows: list[dict[str, object]] = []
    late = {"2026-08-03": 0.0, "2026-08-04": 0.0, "2026-08-05": -0.04}
    for index, day in enumerate(["2026-08-03", "2026-08-04", "2026-08-05"]):
        rows.append(
            {
                "model_run_id": f"run-{index}",
                "training_end": day,
                "feature_name": "usdjpy_return_1d",
                "coefficient": 0.10 + index * 0.01,
            }
        )
        rows.append(
            {
                "model_run_id": f"run-{index}",
                "training_end": day,
                "feature_name": "late_starter",
                "coefficient": late[day],
            }
        )
    return rows


def test_timeline_is_one_row_per_fit_oldest_first() -> None:
    timeline = coefficient_timeline(_timeline_rows())

    assert list(timeline.index) == ["2026-08-03", "2026-08-04", "2026-08-05"]
    assert timeline["usdjpy_return_1d"].tolist() == pytest.approx([0.10, 0.11, 0.12])


def test_timeline_of_empty_rows_is_empty() -> None:
    assert coefficient_timeline([]).empty


def test_feature_that_leaves_zero_is_reported_as_newly_active() -> None:
    appeared = newly_active_features(_timeline_rows())

    assert [row["feature"] for row in appeared] == ["late_starter"]
    assert appeared[0]["first_active_on"] == "2026-08-05"
    assert appeared[0]["coefficient"] == pytest.approx(-0.04)


def test_feature_active_from_the_first_fit_is_not_called_new() -> None:
    appeared = newly_active_features(_timeline_rows())

    assert "usdjpy_return_1d" not in {row["feature"] for row in appeared}


def test_feature_that_never_activates_is_not_reported() -> None:
    rows = [
        {
            "model_run_id": f"run-{index}",
            "training_end": day,
            "feature_name": "never_used",
            "coefficient": 0.0,
        }
        for index, day in enumerate(["2026-08-03", "2026-08-04"])
    ]

    assert newly_active_features(rows) == []


def test_a_single_fit_cannot_show_an_appearance() -> None:
    rows = [
        {
            "model_run_id": "run-0",
            "training_end": "2026-08-03",
            "feature_name": "usdjpy_return_1d",
            "coefficient": 0.1,
        }
    ]

    assert newly_active_features(rows) == []


def _ranked_rows() -> list[dict[str, object]]:
    """Ridge-like fits: nothing is ever exactly zero, but ranks move."""

    series = {
        "steady_top": [0.90, 0.90, 0.90],
        "climber": [0.01, 0.01, 0.80],
        "filler_a": [0.50, 0.50, 0.50],
        "filler_b": [0.40, 0.40, 0.40],
        "filler_c": [0.30, 0.30, 0.30],
        "filler_d": [0.20, 0.20, 0.20],
    }
    rows: list[dict[str, object]] = []
    for index, day in enumerate(["2026-08-03", "2026-08-04", "2026-08-05"]):
        for feature, values in series.items():
            rows.append(
                {
                    "model_run_id": f"run-{index}",
                    "training_end": day,
                    "feature_name": feature,
                    "coefficient": values[index],
                }
            )
    return rows


def test_zero_crossing_never_fires_for_ridge_like_coefficients() -> None:
    # Ridge shrinks but never zeroes, so the sparse-fit check finds nothing.
    assert newly_active_features(_ranked_rows()) == []


def test_feature_entering_the_top_ranks_is_reported() -> None:
    appeared = newly_influential_features(_ranked_rows(), top=5)

    assert [row["feature"] for row in appeared] == ["climber"]
    assert appeared[0]["first_top_on"] == "2026-08-05"
    assert appeared[0]["rank"] <= 5


def test_feature_in_the_top_from_the_start_is_not_reported() -> None:
    appeared = newly_influential_features(_ranked_rows(), top=5)

    assert "steady_top" not in {row["feature"] for row in appeared}


def test_a_narrower_top_reports_fewer_features() -> None:
    wide = newly_influential_features(_ranked_rows(), top=5)
    narrow = newly_influential_features(_ranked_rows(), top=1)

    assert len(narrow) <= len(wide)


def test_ranking_uses_absolute_weight_so_sign_does_not_matter() -> None:
    rows = _ranked_rows()
    for row in rows:
        if row["feature_name"] == "climber":
            row["coefficient"] = -float(row["coefficient"])

    appeared = newly_influential_features(rows, top=5)

    assert [row["feature"] for row in appeared] == ["climber"]
    assert float(appeared[0]["coefficient"]) < 0.0


def test_single_fit_cannot_show_a_new_top_entry() -> None:
    rows = [
        {
            "model_run_id": "run-0",
            "training_end": "2026-08-03",
            "feature_name": "only",
            "coefficient": 0.5,
        }
    ]

    assert newly_influential_features(rows) == []
