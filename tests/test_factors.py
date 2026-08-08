from __future__ import annotations

from pathlib import Path

import pytest

from dashboard.factors import (
    BuyRule,
    buy_rule_mismatches,
    coefficient_matrix,
    coefficient_summary_rows,
    load_configured_buy_rule,
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
