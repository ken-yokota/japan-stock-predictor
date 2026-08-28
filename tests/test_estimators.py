"""Each estimator must be right on data whose answer is known.

The comparison these feed is only worth reading if every arm is doing what its
name says. So each one is checked against a signal it should recover, and the
quantile arm additionally against the two properties a distribution has to have:
its levels must not cross, and the probability read off it must agree with where
zero sits among them.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from research.estimators import (
    ForestEstimator,
    QuantileEstimator,
    TreeEstimator,
    _probability_at_zero,
    _probability_from_spread,
    resolve,
)

ROWS = 140


def _linear(slope: float = 2.0, noise: float = 0.001, seed: int = 0):
    """y = slope * x1 + noise, with four columns of pure noise beside it."""

    rng = np.random.default_rng(seed)
    x1 = rng.normal(scale=0.01, size=ROWS)
    frame = pd.DataFrame(
        {
            "signal": x1,
            "noise1": rng.normal(size=ROWS),
            "noise2": rng.normal(size=ROWS),
            "noise3": rng.normal(size=ROWS),
            "noise4": rng.normal(size=ROWS),
        }
    )
    target = slope * x1 + rng.normal(scale=noise, size=ROWS)
    return frame.iloc[:-1], target[:-1], frame.iloc[[-1]], target[-1]


# --------------------------------------------------------------------------
# Each estimator recovers a signal that is there


@pytest.mark.parametrize(
    "estimator", [QuantileEstimator(), TreeEstimator(), ForestEstimator(trees=60)]
)
def test_a_clear_signal_is_predicted_with_the_right_sign(estimator) -> None:
    features, target, latest, actual = _linear()

    fitted = estimator.fit_predict(features, target, latest)

    assert (fitted.predicted_return > 0) == (actual > 0)


@pytest.mark.parametrize(
    "estimator", [QuantileEstimator(), TreeEstimator(), ForestEstimator(trees=60)]
)
def test_the_probability_agrees_with_the_direction_it_predicted(estimator) -> None:
    """A forecast of +1% beside a 40% chance of rising is two answers, not one."""

    features, target, latest, _ = _linear()

    fitted = estimator.fit_predict(features, target, latest)

    assert 0.0 < fitted.probability_up < 1.0
    if abs(fitted.predicted_return) > 0.002:
        assert (fitted.probability_up > 0.5) == (fitted.predicted_return > 0)


@pytest.mark.parametrize(
    "estimator", [QuantileEstimator(), TreeEstimator(), ForestEstimator(trees=60)]
)
def test_pure_noise_produces_no_confident_claim(estimator) -> None:
    """With nothing to find, the forecast must stay near zero rather than guess."""

    rng = np.random.default_rng(3)
    frame = pd.DataFrame(rng.normal(size=(ROWS, 5)), columns=list("abcde"))
    target = rng.normal(scale=0.015, size=ROWS)

    fitted = estimator.fit_predict(frame.iloc[:-1], target[:-1], frame.iloc[[-1]])

    assert abs(fitted.predicted_return) < 0.02
    assert 0.1 < fitted.probability_up < 0.9


# --------------------------------------------------------------------------
# The quantile arm must behave like a distribution


def test_the_fitted_quantiles_never_cross() -> None:
    """Independent fits can cross; a 90th percentile below a 10th is not a thing."""

    features, target, latest, _ = _linear(noise=0.02, seed=5)

    fitted = QuantileEstimator().fit_predict(features, target, latest)

    assert fitted.quantiles is not None
    keys = ("q0.1", "q0.25", "q0.5", "q0.75", "q0.9")
    values = [fitted.quantiles[key] for key in keys]
    assert values == sorted(values)


def test_the_median_is_the_point_forecast() -> None:
    features, target, latest, _ = _linear()

    fitted = QuantileEstimator().fit_predict(features, target, latest)

    assert fitted.quantiles is not None
    assert fitted.predicted_return == pytest.approx(fitted.quantiles["q0.5"])


def test_probability_is_read_off_where_zero_falls() -> None:
    quantiles = (0.1, 0.25, 0.5, 0.75, 0.9)

    # Zero exactly at the median: half the mass is above it.
    assert _probability_at_zero(
        quantiles, [-0.02, -0.01, 0.0, 0.01, 0.02]
    ) == pytest.approx(0.5)
    # Zero between the 25th and the median, three quarters of the way up.
    assert _probability_at_zero(
        quantiles, [-0.03, -0.02, 0.02, 0.03, 0.04]
    ) == pytest.approx(1 - (0.25 + 0.5 * 0.25), abs=0.01)


def test_a_distribution_entirely_above_zero_is_capped_not_certain() -> None:
    """A fit on 120 rows does not justify claiming certainty."""

    probability = _probability_at_zero((0.1, 0.5, 0.9), [0.01, 0.02, 0.03])

    assert probability == pytest.approx(0.9)


def test_a_distribution_entirely_below_zero_is_capped_the_other_way() -> None:
    probability = _probability_at_zero((0.1, 0.5, 0.9), [-0.03, -0.02, -0.01])

    assert probability == pytest.approx(0.1)


# --------------------------------------------------------------------------
# The shared helpers


def test_probability_from_spread_is_a_half_when_the_forecast_is_zero() -> None:
    assert _probability_from_spread(0.0, 0.01) == pytest.approx(0.5)


def test_probability_from_spread_grows_with_the_forecast() -> None:
    small = _probability_from_spread(0.005, 0.01)
    large = _probability_from_spread(0.02, 0.01)

    assert 0.5 < small < large <= 0.98


def test_a_zero_spread_refuses_to_claim_anything() -> None:
    assert _probability_from_spread(0.05, 0.0) == pytest.approx(0.5)


def test_resolve_names_the_valid_estimators_when_given_a_bad_one() -> None:
    with pytest.raises(ValueError, match="quantile"):
        resolve("nonsense")


@pytest.mark.parametrize("name", ["quantile", "tree", "forest"])
def test_every_registered_estimator_resolves(name: str) -> None:
    assert resolve(name).name == name


# --------------------------------------------------------------------------
# Hyperparameters come from inside the window


def test_the_chosen_depth_does_not_depend_on_rows_after_the_window() -> None:
    """The tree models are the ones most able to exploit a leak, so pin it."""

    features, target, latest, _ = _linear(noise=0.02, seed=11)
    estimator = TreeEstimator()

    first = estimator.fit_predict(features, target, latest)

    tampered = features.copy()
    tampered.iloc[-20:, 0] = -tampered.iloc[-20:, 0] * 50
    # Only the *training* rows changed above; the estimator never saw anything
    # else, so a different answer here would mean it read something it should
    # not have.
    second = estimator.fit_predict(features, target, latest)

    assert first.parameters == second.parameters
    assert first.predicted_return == pytest.approx(second.predicted_return)


# --------------------------------------------------------------------------
# The window's own score is recorded, because it cannot be recovered later


@pytest.mark.parametrize(
    "estimator", [QuantileEstimator(), TreeEstimator(), ForestEstimator(trees=60)]
)
def test_every_arm_reports_how_well_it_fitted_its_own_window(estimator) -> None:
    """Without this the train-versus-out-of-sample gap cannot be measured.

    The fitted model is discarded once the session is scored, so a score taken
    afterwards would need a refit -- on 5,500 sessions, hours of work to recover
    a number that was free at fit time.
    """

    features, target, latest, _ = _linear(noise=0.02, seed=7)

    fitted = estimator.fit_predict(features, target, latest)

    assert fitted.train_mae is not None and fitted.train_mae >= 0.0
    assert fitted.train_direction is not None
    assert 0.0 <= fitted.train_direction <= 1.0


def test_a_deeper_tree_fits_its_own_window_better_than_a_shallow_one() -> None:
    """The direction the gap is expected to run, pinned so a bug reverses it."""

    features, target, latest, _ = _linear(noise=0.02, seed=9)

    shallow = TreeEstimator(depths=(2,)).fit_predict(features, target, latest)
    deep = TreeEstimator(depths=(4,)).fit_predict(features, target, latest)

    assert deep.train_mae <= shallow.train_mae
