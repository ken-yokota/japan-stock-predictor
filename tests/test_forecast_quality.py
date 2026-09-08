"""The point prediction against the curve's median, and the refusal to judge.

The arithmetic here is easy; the discipline is not. Two things are pinned:
that a session is the unit of evidence rather than a row, and that no verdict
is offered until there are enough sessions. Twenty-two tickers on one morning
ride the same market, so counting them as twenty-two independent observations
would let three days look like a finding.
"""

from __future__ import annotations

from typing import Any

import pytest

from dashboard.forecast_quality import (
    MINIMUM_SESSIONS_FOR_EVIDENCE,
    comparison_rows,
    coverage_rows,
    evaluate,
)


def _curve(median: float, *, half_width: float = 0.02) -> dict[str, Any]:
    grid = {
        0.05: median - half_width * 2,
        0.10: median - half_width * 1.5,
        0.25: median - half_width,
        0.50: median,
        0.75: median + half_width,
        0.90: median + half_width * 1.5,
        0.95: median + half_width * 2,
    }
    return {"levels": [{"quantile": q, "return": v} for q, v in grid.items()]}


def _row(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "prediction_date": "2026-09-07",
        "ticker": "7203",
        "predicted_intraday_return": 0.02,
        "actual_intraday_return": 0.01,
        "return_distribution": _curve(0.005),
    }
    base.update(overrides)
    return base


def _days(count: int) -> list[dict[str, Any]]:
    return [_row(prediction_date=f"2026-09-{index + 1:02d}") for index in range(count)]


# --- what counts ------------------------------------------------------------


def test_a_row_without_an_outcome_is_not_counted() -> None:
    quality = evaluate([_row(), _row(ticker="7267", actual_intraday_return=None)])
    assert quality.observations == 1


def test_a_row_without_a_curve_is_not_counted() -> None:
    # There is nothing to compare the point against, so including it would
    # change the denominator without adding evidence.
    quality = evaluate([_row(), _row(ticker="7267", return_distribution=None)])
    assert quality.observations == 1


def test_sessions_count_days_not_rows() -> None:
    same_day = [_row(ticker=str(7200 + index)) for index in range(22)]
    quality = evaluate(same_day)
    assert quality.observations == 22
    assert quality.sessions == 1


def test_nothing_usable_is_reported_as_nothing() -> None:
    quality = evaluate([])
    assert quality.observations == 0
    assert quality.point_mae is None
    assert comparison_rows(quality) == []
    assert "まだありません" in quality.verdict


# --- the arithmetic ---------------------------------------------------------


def test_errors_are_mean_absolute_and_zero_is_the_floor() -> None:
    rows = [
        _row(
            predicted_intraday_return=0.03,
            actual_intraday_return=0.01,
            return_distribution=_curve(0.0),
        ),
    ]
    quality = evaluate(rows)
    assert quality.point_mae == pytest.approx(0.02)
    assert quality.median_mae == pytest.approx(0.01)
    assert quality.zero_mae == pytest.approx(0.01)


def test_direction_ignores_a_flat_close() -> None:
    # A zero-return day is neither a hit nor a miss for either contender, so it
    # must not enter the denominator on one side only.
    rows = [_row(actual_intraday_return=0.0), _row(ticker="7267")]
    quality = evaluate(rows)
    assert quality.scored == 1


def test_direction_is_counted_for_both_contenders() -> None:
    rows = [
        _row(
            predicted_intraday_return=0.02,
            actual_intraday_return=-0.01,
            return_distribution=_curve(-0.005),
        ),
    ]
    quality = evaluate(rows)
    assert quality.point_hits == 0
    assert quality.median_hits == 1


# --- coverage ---------------------------------------------------------------


def test_an_outcome_inside_the_band_is_covered() -> None:
    quality = evaluate(
        [_row(actual_intraday_return=0.005, return_distribution=_curve(0.005))]
    )
    assert all(band.inside == 1 for band in quality.bands)


def test_an_outcome_outside_every_band_is_not() -> None:
    quality = evaluate(
        [_row(actual_intraday_return=0.50, return_distribution=_curve(0.0))]
    )
    assert all(band.inside == 0 for band in quality.bands)


def test_coverage_rows_carry_the_prior_study_where_there_is_one() -> None:
    quality = evaluate([_row()])
    rows = {row["区間"]: row for row in coverage_rows(quality, ((0.80, 0.755),))}
    assert rows["80%"]["既存OOS調査"] == "75.5%"
    assert rows["50%"]["既存OOS調査"] == "—"


# --- the refusal to judge ---------------------------------------------------


def test_a_thin_window_refuses_to_pick_a_winner() -> None:
    quality = evaluate(_days(MINIMUM_SESSIONS_FOR_EVIDENCE - 1))
    assert quality.has_enough_evidence is False
    assert "判定不能" in quality.verdict
    assert str(MINIMUM_SESSIONS_FOR_EVIDENCE) in quality.verdict


def test_the_verdict_names_the_nearer_one_once_there_is_enough() -> None:
    quality = evaluate(_days(MINIMUM_SESSIONS_FOR_EVIDENCE))
    assert quality.has_enough_evidence is True
    assert "判定不能" not in quality.verdict
    # The fixture's median (0.005) is nearer the outcome (0.01) than the point
    # prediction (0.02), so the curve should win.
    assert "分布P50" in quality.verdict


def test_many_rows_on_few_days_still_does_not_qualify() -> None:
    # The trap this floor exists for: 22 tickers x 3 sessions is 66 rows and
    # three observations.
    rows = [
        _row(prediction_date=day, ticker=str(7200 + index))
        for day in ("2026-09-03", "2026-09-04", "2026-09-07")
        for index in range(22)
    ]
    quality = evaluate(rows)
    assert quality.observations == 66
    assert quality.sessions == 3
    assert quality.has_enough_evidence is False
