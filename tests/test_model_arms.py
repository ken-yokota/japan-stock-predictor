"""What running every model family each morning must and must not do.

The risk with eight arms is not that one is wrong; it is that a wrong one is
presented as though it were as informative as the others. So most of these
tests are about labelling: that a family which estimates no return is not given
a distribution, that a width taken from past errors is never reported as one
that reacts to today's inputs, and that a feed-forward network is not printed
under a name belonging to a model this project cannot currently run.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from models.arms import (
    CONDITIONAL,
    ENSEMBLE,
    FAILED,
    OK,
    RESIDUAL,
    UNAVAILABLE,
    ArmForecast,
    GradientBoostingArm,
    LogisticArm,
    PointArm,
    RandomForestArm,
    default_arms,
    out_of_fold_residuals,
    run_arms,
)
from models.base import DEFAULT_QUANTILE_LEVELS
from models.ridge import build_ridge_pipeline


def _window(rows: int = 120, columns: int = 12, seed: int = 0):
    rng = np.random.default_rng(seed)
    frame = pd.DataFrame(
        rng.normal(size=(rows, columns)),
        columns=[f"f{index}" for index in range(columns)],
    )
    target = 0.004 * frame["f0"].to_numpy() + rng.normal(scale=0.015, size=rows)
    latest = pd.DataFrame(rng.normal(size=(1, columns)), columns=frame.columns)
    return frame, target, latest


def _run(arm, seed: int = 0) -> ArmForecast:
    frame, target, latest = _window(seed=seed)
    return arm.forecast(
        frame, target, latest, levels=DEFAULT_QUANTILE_LEVELS, n_splits=5
    )


# --- the families are all present ---------------------------------------


def test_every_family_the_operator_asked_for_is_present() -> None:
    names = {arm.name for arm in default_arms()}
    assert {
        "ridge",
        "logistic",
        "random_forest",
        "lightgbm",
        "xgboost",
        "lasso",
        "elastic_net",
        "mlp",
    } == names


def test_the_neural_arm_is_never_labelled_lstm_or_transformer() -> None:
    """It is an MLP. Printing another model's name would outlive the excuse."""

    neural = next(arm for arm in default_arms() if arm.name == "mlp")
    assert "MLP" in neural.label
    assert "LSTM" not in neural.label
    assert "Transformer" not in neural.label


# --- each family answers in its own way ---------------------------------


def test_the_classifier_gets_a_probability_and_no_distribution() -> None:
    """Giving it a width would show magnitude opinions it does not hold."""

    forecast = _run(LogisticArm())
    assert forecast.status == OK
    assert forecast.probability_up is not None
    assert forecast.predicted_return is None
    assert forecast.distribution is None
    assert forecast.spread_kind is None


def test_a_mean_estimator_reports_its_width_as_residual() -> None:
    arm = PointArm(
        "ridge",
        "Ridge回帰",
        lambda alpha=1.0: build_ridge_pipeline(alpha),
        {"alpha": (0.1, 1.0)},
    )
    forecast = _run(arm)
    assert forecast.status == OK
    assert forecast.spread_kind == RESIDUAL
    assert forecast.distribution is not None


def test_the_forest_width_is_the_disagreement_between_its_trees() -> None:
    forecast = _run(RandomForestArm(depths=(3,), trees=40))
    assert forecast.status == OK
    assert forecast.spread_kind == ENSEMBLE
    assert forecast.distribution is not None
    assert forecast.distribution.method == "forest_tree_spread"


@pytest.mark.parametrize("backend", ["lightgbm", "xgboost"])
def test_a_boosting_arm_fits_the_quantiles_directly(backend: str) -> None:
    forecast = _run(GradientBoostingArm(backend, trees=40))
    if forecast.status == UNAVAILABLE:
        pytest.skip(f"{backend} is not installed: {forecast.detail}")
    assert forecast.status == OK
    assert forecast.spread_kind == CONDITIONAL
    assert forecast.distribution is not None
    assert forecast.distribution.method == f"{backend}_quantile"


def test_a_boosted_curve_is_sorted_even_though_its_quantiles_cross() -> None:
    """Boosted quantiles cross more readily than linear ones."""

    forecast = _run(GradientBoostingArm("lightgbm", trees=40))
    if forecast.status != OK:
        pytest.skip(f"lightgbm unavailable: {forecast.detail}")
    values = forecast.distribution.values  # type: ignore[union-attr]
    assert list(values) == sorted(values)


# --- the spread must not flatter the model ------------------------------


def test_the_residual_width_is_taken_out_of_fold_not_in_sample() -> None:
    """In-sample residuals are the errors the fit already minimised.

    A band built from them is narrower than the sessions it is meant to
    cover, which is the specific failure the distribution work removed from
    the production interval; it must not come back in through an arm.
    """

    frame, target, _ = _window()
    matrix = np.asarray(frame, dtype=float)
    out_of_fold = out_of_fold_residuals(
        lambda: build_ridge_pipeline(1.0), matrix, target, 5
    )
    pipeline = build_ridge_pipeline(1.0)
    pipeline.fit(matrix, target)
    in_sample = target - np.asarray(pipeline.predict(matrix), dtype=float)
    assert len(out_of_fold) > 0
    assert float(np.std(out_of_fold)) > float(np.std(in_sample))


def test_out_of_fold_residuals_are_empty_when_the_window_cannot_be_split() -> None:
    matrix = np.zeros((3, 2), dtype=float)
    target = np.array([0.1, 0.2, 0.3])
    assert (
        len(out_of_fold_residuals(lambda: build_ridge_pipeline(1.0), matrix, target, 5))
        == 0
    )


# --- one failure must not cost the morning ------------------------------


class _Exploding:
    name = "exploding"
    label = "壊れる系統"

    def forecast(self, features, target, latest, *, levels, n_splits):
        raise RuntimeError("solver did not converge")


def test_one_family_raising_does_not_stop_the_others() -> None:
    frame, target, latest = _window()
    with pytest.raises(RuntimeError):
        _Exploding().forecast(frame, target, latest, levels=(0.5,), n_splits=5)


def test_an_arm_reports_its_own_failure_rather_than_raising() -> None:
    """A family that cannot fit is a fact to report, not a lost morning."""

    arm = PointArm("broken", "壊れる", lambda: build_ridge_pipeline(-1.0), {})
    forecast = _run(arm)
    assert forecast.status == FAILED
    assert forecast.detail
    assert forecast.predicted_return is None


def test_a_missing_library_is_reported_as_unavailable_not_failed() -> None:
    """The two are different: one needs installing, the other needs fixing."""

    arm = GradientBoostingArm("lightgbm")
    arm.backend = "not_a_real_backend"

    def explode(*args: object, **kwargs: object) -> float:
        raise ImportError(name="not_a_real_backend")

    arm._fit_quantile = explode  # type: ignore[method-assign]
    forecast = _run(arm)
    assert forecast.status == UNAVAILABLE
    assert "not_a_real_backend" in forecast.detail


def test_every_arm_answers_and_none_of_them_raise() -> None:
    frame, target, latest = _window()
    forecasts = run_arms(frame, target, latest, n_splits=5)
    assert len(forecasts) == len(default_arms())
    assert all(f.status in {OK, FAILED, UNAVAILABLE} for f in forecasts)
    # Whatever else happened, the production families must have answered.
    by_name = {f.name: f for f in forecasts}
    assert by_name["ridge"].status == OK
    assert by_name["logistic"].status == OK


# --- what gets written down ---------------------------------------------


def test_the_stored_record_keeps_how_the_width_was_arrived_at() -> None:
    """Without it, two very different bands are one column of numbers."""

    payload = _run(RandomForestArm(depths=(3,), trees=40)).to_payload()
    assert payload["spread_kind"] == ENSEMBLE
    assert payload["distribution"]["method"] == "forest_tree_spread"
    assert payload["name"] == "random_forest"


def test_a_failed_arm_still_serialises_without_a_distribution() -> None:
    payload = ArmForecast("x", "X", status=FAILED, detail="Boom").to_payload()
    assert payload["distribution"] is None
    assert payload["predicted_return"] is None
    assert payload["status"] == FAILED


def test_the_mail_warns_that_some_families_report_bands_that_are_too_tight() -> None:
    """Measured 2026-08-30: the tree and boosting bands run 0.53-0.65x the width
    a zero-skill forecast needs. A reader comparing two 80% columns cannot see
    that from the numbers alone, so the mail says it."""

    from notifications.templates import ARM_WIDTH_NOTE

    assert "狭すぎ" in ARM_WIDTH_NOTE
    assert "0.53" in ARM_WIDTH_NOTE


def test_the_sequence_arms_stay_off_until_something_measures_them() -> None:
    """They cost ~19 minutes a morning and measured 0.26-0.31x on band width."""

    from models.arms import default_arms

    assert not any(a.name in {"lstm", "transformer"} for a in default_arms())
    with_sequence = default_arms(include_sequence=True)
    assert {"lstm", "transformer"} <= {a.name for a in with_sequence}
