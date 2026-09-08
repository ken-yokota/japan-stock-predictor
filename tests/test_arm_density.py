"""Per-family forecast curves, and the distinctions the tabs must not blur.

Three families answer with a genuinely conditional curve, one with ensemble
disagreement, and four with their own historical error laid over today's point.
Presenting those identically would tell the reader that eight models agree or
disagree when four of them share a shape by construction, so the grouping and
its wording are pinned here alongside the arithmetic.
"""

from __future__ import annotations

from typing import Any

import pytest

from dashboard.arm_density import (
    CONDITIONAL,
    ENSEMBLE,
    PRODUCTION_LABEL,
    RESIDUAL,
    SPREAD_NOTES,
    SPREAD_SHORT,
    arm_curves,
    axis_for,
    density_chart,
    density_frame,
    quantile_at,
)


def _levels(low: float, high: float) -> dict[str, Any]:
    steps = [0.05, 0.25, 0.5, 0.75, 0.95]
    span = high - low
    return {
        "method": "test",
        "levels": [{"quantile": q, "return": low + span * q} for q in steps],
    }


def _arm(name: str, spread: str, **overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "name": name,
        "label": name.title(),
        "status": "OK",
        "spread_kind": spread,
        "predicted_return": 0.01,
        "distribution": _levels(-0.03, 0.03),
    }
    base.update(overrides)
    return base


def _row(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "ticker": "7203",
        "predicted_intraday_return": 0.02,
        "actual_intraday_return": None,
        "return_distribution": _levels(-0.04, 0.04),
        "arm_predictions": [
            _arm("ridge", RESIDUAL),
            _arm("random_forest", ENSEMBLE),
            _arm("lightgbm", CONDITIONAL),
        ],
    }
    base.update(overrides)
    return base


# --- which families get a tab ----------------------------------------------


def test_production_comes_first_then_the_arms() -> None:
    curves = arm_curves(_row())
    assert curves[0].label == PRODUCTION_LABEL
    assert [curve.name for curve in curves[1:]] == [
        "ridge",
        "random_forest",
        "lightgbm",
    ]


def test_a_family_without_a_curve_gets_no_tab() -> None:
    # Logistic regression estimates a direction probability, not a return.
    # Inventing a distribution for it would be fabrication, so it must simply
    # be absent.
    row = _row(
        arm_predictions=[
            _arm("logistic", "", distribution=None),
            _arm("ridge", RESIDUAL),
        ]
    )
    assert [curve.name for curve in arm_curves(row)[1:]] == ["ridge"]


def test_an_unavailable_family_gets_no_tab() -> None:
    row = _row(
        arm_predictions=[
            _arm("lstm", CONDITIONAL, status="UNAVAILABLE"),
            _arm("ridge", RESIDUAL),
        ]
    )
    assert [curve.name for curve in arm_curves(row)[1:]] == ["ridge"]


def test_a_row_with_no_production_curve_still_shows_its_arms() -> None:
    curves = arm_curves(_row(return_distribution=None))
    assert curves
    assert all(curve.label != PRODUCTION_LABEL for curve in curves)


def test_a_row_with_nothing_at_all_yields_no_curves() -> None:
    assert arm_curves({"ticker": "7203"}) == []
    assert arm_curves(_row(return_distribution=None, arm_predictions=[])) == []


def test_malformed_arm_entries_are_skipped_not_fatal() -> None:
    row = _row(arm_predictions=["nonsense", None, _arm("ridge", RESIDUAL)])
    assert [curve.name for curve in arm_curves(row)[1:]] == ["ridge"]


# --- the distinction the tabs carry -----------------------------------------


def test_every_spread_kind_has_wording() -> None:
    for kind in (CONDITIONAL, ENSEMBLE, RESIDUAL):
        assert kind in SPREAD_NOTES
        assert kind in SPREAD_SHORT


def test_the_residual_wording_says_the_width_does_not_move() -> None:
    # This is the sentence that stops a fixed historical error band being read
    # as "the model is confident today".
    assert "変わりません" in SPREAD_NOTES[RESIDUAL]
    assert "変わります" in SPREAD_NOTES[CONDITIONAL]


def test_spread_kind_survives_onto_the_curve() -> None:
    curves = {curve.name: curve for curve in arm_curves(_row())}
    assert curves["ridge"].spread_kind == RESIDUAL
    assert curves["random_forest"].spread_kind == ENSEMBLE
    assert curves["lightgbm"].spread_kind == CONDITIONAL


# --- the shared axis --------------------------------------------------------


def test_the_axis_covers_every_family() -> None:
    # Each family on its own scale would draw a wide forecast and a narrow one
    # at the same width, which is the one comparison the tabs are for.
    curves = arm_curves(_row())
    low, high = axis_for(curves, actual=None)
    for curve in curves:
        assert low <= curve.points[0][1]
        assert high >= curve.points[-1][1]


def test_the_axis_stretches_to_reach_the_outcome() -> None:
    _, high = axis_for(arm_curves(_row()), actual=0.20)
    assert high > 0.20


def test_the_axis_has_a_default_when_there_is_nothing() -> None:
    low, high = axis_for([], actual=None)
    assert low < high


# --- the curves themselves --------------------------------------------------


def test_width_is_the_span_between_the_outermost_fitted_levels() -> None:
    (curve,) = arm_curves(
        _row(return_distribution=_levels(-0.02, 0.06), arm_predictions=[])
    )
    lowest, highest = curve.points[0][1], curve.points[-1][1]
    assert curve.width == pytest.approx(highest - lowest)
    # Not the nominal range handed to the helper: the curve only knows the
    # levels it actually fitted, and its width is the distance between the
    # outermost of those.
    assert curve.points[0][0] == pytest.approx(0.05)
    assert curve.points[-1][0] == pytest.approx(0.95)


def test_the_profile_has_one_column_per_step() -> None:
    curve = arm_curves(_row())[0]
    frame = density_frame(curve, low=-0.05, high=0.05, columns=40)
    assert frame is not None
    assert len(frame) == 40


def test_an_impossible_axis_draws_nothing() -> None:
    curve = arm_curves(_row())[0]
    assert density_frame(curve, low=0.05, high=-0.05) is None
    assert density_chart(curve, low=0.05, high=-0.05) is None


def test_the_outcome_adds_a_layer() -> None:
    curve = arm_curves(_row())[0]
    without = density_chart(curve, low=-0.05, high=0.05)
    with_actual = density_chart(curve, low=-0.05, high=0.05, actual=0.01)
    assert without is not None and with_actual is not None
    assert len(with_actual.layer) == len(without.layer) + 1


def test_levels_outside_the_fit_are_pinned() -> None:
    points = [(0.1, -0.02), (0.5, 0.0), (0.9, 0.02)]
    assert quantile_at(points, 0.5) == pytest.approx(0.0)
    assert quantile_at(points, 0.3) == pytest.approx(-0.01)
    assert quantile_at(points, 0.001) == pytest.approx(-0.02)
    assert quantile_at([], 0.5) is None
