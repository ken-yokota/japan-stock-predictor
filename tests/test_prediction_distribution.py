"""What the forecast distribution must do, and what it must refuse to do.

The failures exercised here are the ones that turn a distribution into a lie:
a curve whose quantiles cross, a probability extrapolated past the outermost
level that was actually fitted, a constant-width fallback presented as if it
had varied with the inputs, and a missing curve rendered as a blank cell rather
than as an absence. Each has a test because reading the code is not the same
check -- the crossing one in particular looks correct in the source.
"""

from __future__ import annotations

from datetime import UTC, date, datetime

import numpy as np
import pandas as pd
import pytest

from models.base import (
    DEFAULT_QUANTILE_LEVELS,
    REPORTED_QUANTILE_LEVELS,
    ModelTrainingConfig,
)
from models.distribution import (
    ReturnDistribution,
    distribution_from_pairs,
    empirical_distribution,
    fit_quantile_ensemble,
)
from models.training import train_ticker_model
from notifications.contracts import EmailCandidate, MorningEmailPayload
from notifications.templates import DENSITY_COLUMNS, render_morning_email

LEVELS = (0.05, 0.10, 0.25, 0.50, 0.75, 0.90, 0.95)


def _window(rows: int = 140, seed: int = 0) -> tuple[pd.DataFrame, pd.Series]:
    rng = np.random.default_rng(seed)
    frame = pd.DataFrame(
        rng.normal(size=(rows, 6)), columns=[f"f{index}" for index in range(6)]
    )
    target = pd.Series(
        0.004 * frame["f0"].to_numpy() + rng.normal(scale=0.015, size=rows)
    )
    return frame, target


def _curve(centre: float = 0.004, spread: float = 0.009) -> ReturnDistribution:
    values = tuple(
        centre + spread * offset for offset in (-2.1, -1.6, -0.7, 0.0, 0.7, 1.6, 2.1)
    )
    return ReturnDistribution(LEVELS, values, alpha=0.01, training_sessions=120)


# --- the curve itself ----------------------------------------------------


def test_a_fitted_curve_never_comes_back_crossed() -> None:
    """Independently fitted quantiles can cross; a distribution cannot."""

    frame, target = _window()
    ensemble = fit_quantile_ensemble(
        frame, target.to_numpy(dtype=float), n_splits=5, levels=LEVELS
    )
    values = ensemble.predict_distribution(frame.iloc[[-1]]).values
    assert list(values) == sorted(values)


def test_a_crossed_curve_is_refused_rather_than_stored() -> None:
    with pytest.raises(ValueError, match="non-decreasing"):
        ReturnDistribution((0.1, 0.9), (0.02, -0.02), alpha=0.01, training_sessions=120)


def test_a_curve_needs_more_than_one_level_to_be_a_distribution() -> None:
    with pytest.raises(ValueError, match="at least two"):
        ReturnDistribution((0.5,), (0.01,), alpha=0.01, training_sessions=120)


def test_a_non_finite_level_is_refused() -> None:
    with pytest.raises(ValueError, match="finite"):
        ReturnDistribution(
            (0.1, 0.9), (0.0, float("nan")), alpha=0.01, training_sessions=120
        )


# --- reading claims off the curve ---------------------------------------


def test_the_probability_is_pinned_at_the_outermost_fitted_level() -> None:
    """A 120-session fit cannot support a claim past its own 5th percentile.

    Extrapolating one would manufacture a 99% that nothing in the window
    measured, so both ends are answered with the outer level instead.
    """

    curve = _curve()
    assert curve.probability_above(curve.values[-1] + 1.0) == pytest.approx(0.05)
    assert curve.probability_above(curve.values[0] - 1.0) == pytest.approx(0.95)


def test_the_probability_at_zero_interpolates_between_the_bracketing_levels() -> None:
    curve = ReturnDistribution(
        (0.25, 0.75), (-0.01, 0.01), alpha=0.01, training_sessions=120
    )
    assert curve.probability_above(0.0) == pytest.approx(0.5)


def test_an_interval_is_only_returned_when_both_of_its_levels_were_fitted() -> None:
    curve = _curve()
    assert curve.interval(0.90) == (curve.values[0], curve.values[-1])
    assert curve.interval(0.80) == (curve.values[1], curve.values[-2])
    # 0.30 would need the 35th and 65th percentiles, which are not fitted.
    assert curve.interval(0.30) is None


def test_the_curve_survives_a_round_trip_through_its_stored_form() -> None:
    curve = _curve()
    restored = ReturnDistribution.from_payload(curve.to_payload())
    assert restored.values == curve.values
    assert restored.levels == curve.levels
    assert restored.method == curve.method


def test_the_stored_form_carries_the_coverage_that_was_measured() -> None:
    """A band with no record of how often it held is a band read too warmly."""

    payload = _curve().to_payload()
    nominal = [row["nominal"] for row in payload["coverage_evidence"]["intervals"]]
    assert 0.80 in nominal
    assert payload["coverage_evidence"]["samples"] == 5500


def test_prices_are_the_same_curve_in_yen() -> None:
    curve = _curve()
    priced = curve.prices(1000.0)
    assert priced[3][1] == pytest.approx(1000.0 * (1.0 + curve.median))
    with pytest.raises(ValueError, match="positive"):
        curve.prices(0.0)


# --- the trained bundle --------------------------------------------------


def test_a_trained_ticker_answers_with_a_distribution() -> None:
    frame, target = _window()
    model = train_ticker_model("9101", frame, target)
    prediction = model.predict_one(frame.iloc[[-1]])
    assert prediction.distribution is not None
    assert prediction.distribution.levels == DEFAULT_QUANTILE_LEVELS
    assert prediction.distribution.method == "quantile_regression_l1"


def test_a_failed_quantile_fit_falls_back_and_says_which_method_it_used() -> None:
    """The fallback is constant-width, so it must never be labelled as fitted."""

    frame, target = _window()
    model = train_ticker_model("9101", frame, target)
    model.quantiles = None
    prediction = model.predict_one(frame.iloc[[-1]])
    assert prediction.distribution is not None
    assert prediction.distribution.method == "residual_quantiles"


def test_a_ticker_with_the_distribution_disabled_still_predicts() -> None:
    frame, target = _window()
    model = train_ticker_model(
        "9101",
        frame,
        target,
        config=ModelTrainingConfig(distribution_quantiles=()),
    )
    assert model.quantiles is None
    assert model.predict_one(frame.iloc[[-1]]).predicted_return is not None


def test_the_residual_fallback_needs_residuals_to_stand_on() -> None:
    assert (
        empirical_distribution(0.01, np.array([0.001]), training_sessions=120) is None
    )
    assert (
        empirical_distribution(
            float("nan"), np.array([0.001, 0.002]), training_sessions=120
        )
        is None
    )


def test_rebuilding_from_pairs_refuses_a_single_point() -> None:
    assert distribution_from_pairs([(0.5, 0.01)]) is None
    assert distribution_from_pairs([(0.9, 0.02), (0.1, -0.02)]) is not None


# --- the morning mail ----------------------------------------------------


def _candidate(
    ticker: str,
    company: str,
    *,
    signal: str = "BUY",
    rank: int | None = 1,
    curve: ReturnDistribution | None = None,
    scale: float = 0.05,
) -> EmailCandidate:
    density = (
        curve.density_profile(-scale, scale, DENSITY_COLUMNS)
        if curve is not None
        else ()
    )
    return EmailCandidate(
        ticker=ticker,
        company=company,
        predicted_return=0.0062,
        probability_up=0.64,
        signal=signal,
        reference_price=5000.0,
        predicted_close=5031.0,
        rank=rank,
        data_quality="CLEAN",
        distribution=curve.pairs() if curve is not None else (),
        distribution_method=curve.method if curve is not None else None,
        distribution_probability_up=(
            curve.probability_above(0.0) if curve is not None else None
        ),
        distribution_median=curve.median if curve is not None else None,
        density=density,
        density_scale=scale if density else None,
    )


def _render(candidates: tuple[EmailCandidate, ...]):
    payload = MorningEmailPayload(
        prediction_date=date(2026, 8, 28),
        generated_at=datetime(2026, 8, 28, 8, 20, tzinfo=UTC),
        cutoff_at=datetime(2026, 8, 28, 8, 20, tzinfo=UTC),
        candidates=candidates,
        dashboard_url="https://example.invalid/app",
        model_version="ridge-logistic-quantile-v2",
    )
    return render_morning_email(payload, sender="a@b.invalid", recipient="c@d.invalid")


def test_the_mail_leads_with_the_band_not_with_one_number() -> None:
    """Named by the operator's convention: Pxx is the level exceeded xx% of the
    time, so P90 is the downside. Labelling the 10th percentile "上位10%" read
    as an upside target for a risk figure."""

    message = _render((_candidate("9101", "日本郵船", curve=_curve()),))
    assert "P50" in message.text
    assert "P90" in message.text
    assert "上振れ10%" in message.text
    assert "80%区間" in message.text
    assert "下振れ側のリスク" in message.text


def test_the_mail_states_how_often_the_band_actually_held() -> None:
    message = _render((_candidate("9101", "日本郵船", curve=_curve()),))
    assert "75.5%" in message.text
    assert "46.3%" in message.text


def test_the_mail_still_names_the_two_numbers_the_buy_rule_used() -> None:
    """The decision must stay explainable in terms of what decided it."""

    message = _render((_candidate("9101", "日本郵船", curve=_curve()),))
    assert "判定に使った数値" in message.text
    assert "+0.62%" in message.text


def test_a_constant_width_fallback_is_named_in_the_mail() -> None:
    fallback = ReturnDistribution(
        LEVELS, _curve().values, 0.0, 120, method="residual_quantiles"
    )
    message = _render((_candidate("6758", "ソニーグループ", curve=fallback),))
    assert "分位点回帰が解けず" in message.text
    assert "6758" in message.text


def test_a_candidate_with_no_distribution_is_reported_as_missing() -> None:
    message = _render((_candidate("7203", "トヨタ自動車", curve=None),))
    assert "分布がありません" in message.text
    assert "分布なし" in message.html


def test_a_day_with_no_distributions_at_all_still_renders() -> None:
    """Zero scale must not divide by zero on the way to a blank bar."""

    message = _render(
        (
            _candidate("7203", "トヨタ自動車", signal="NO_BUY", rank=None, curve=None),
            _candidate("6758", "ソニー", signal="NO_BUY", rank=None, curve=None),
        )
    )
    assert "本日は条件を満たすBUY候補なし" in message.text
    assert message.html


def test_every_row_of_one_table_is_drawn_against_the_same_ruler() -> None:
    """Two distributions of different width must not look identical."""

    narrow = _candidate("9101", "日本郵船", curve=_curve(spread=0.002))
    wide = _candidate("9104", "商船三井", rank=2, curve=_curve(spread=0.020))
    html = _render((narrow, wide)).html
    assert html.count("border-right:1px solid") >= 2


# --- reading the curve back out of the database --------------------------


class _Row:
    """The two fields ``services.email._distribution`` reads off a Prediction."""

    def __init__(self, payload: object) -> None:
        self.return_distribution = payload


def test_a_persisted_curve_is_rebuilt_for_the_mail() -> None:
    from services.email import _distribution

    restored = _distribution(_Row(_curve().to_payload()))
    assert restored is not None
    assert restored.levels == LEVELS


@pytest.mark.parametrize(
    "payload",
    [
        None,
        {},
        {"levels": []},
        {"levels": [{"quantile": 0.5}]},
        {"levels": "not-a-list"},
        {"levels": [{"quantile": 0.9, "return": 0.0}]},
    ],
)
def test_an_unusable_stored_curve_costs_the_mail_its_band_not_the_mail(
    payload: object,
) -> None:
    """A malformed document must not be the reason no prediction mail goes out."""

    from services.email import _distribution

    assert _distribution(_Row(payload)) is None


# --- the density ---------------------------------------------------------


GRID = DEFAULT_QUANTILE_LEVELS


def _normal(mu: float, sigma: float) -> ReturnDistribution:
    from statistics import NormalDist

    shape = NormalDist(mu, sigma)
    return ReturnDistribution(
        GRID, tuple(shape.inv_cdf(level) for level in GRID), 0.01, 120
    )


def test_the_grid_is_evenly_spaced_so_every_gap_holds_equal_mass() -> None:
    """This is what makes the curve a density rather than a set of landmarks."""

    steps = {round(GRID[i + 1] - GRID[i], 6) for i in range(len(GRID) - 1)}
    assert steps == {0.05}
    assert set(REPORTED_QUANTILE_LEVELS) <= set(GRID)


def test_every_bin_is_its_own_mass_over_its_own_width() -> None:
    curve = _normal(0.004, 0.012)
    bins = curve.density_bins()
    assert bins
    for item in bins:
        assert item.high > item.low
        assert item.density == pytest.approx(item.mass / (item.high - item.low))
    assert sum(item.mass for item in bins) == pytest.approx(GRID[-1] - GRID[0])


def test_a_tied_pair_of_quantiles_is_merged_rather_than_divided_by() -> None:
    """A zero-width bin is arithmetic, not a claim of infinite certainty."""

    curve = ReturnDistribution(
        (0.25, 0.50, 0.75), (0.0, 0.0, 0.02), alpha=0.01, training_sessions=120
    )
    bins = curve.density_bins()
    assert len(bins) == 1
    assert all(item.high > item.low for item in bins)
    # The tied bin's mass is carried forward, not dropped.
    assert bins[0].mass == pytest.approx(0.50)


def test_a_completely_flat_curve_yields_no_bins_rather_than_infinities() -> None:
    curve = ReturnDistribution(
        (0.25, 0.50, 0.75), (0.01, 0.01, 0.01), alpha=0.01, training_sessions=120
    )
    assert curve.density_bins() == ()


def test_the_drawn_density_accounts_for_exactly_the_fitted_mass() -> None:
    """The outer 5% either side is undrawn because the window cannot place it."""

    curve = _normal(0.004, 0.012)
    profile = curve.density_profile(-0.10, 0.10, 41)
    assert sum(profile) == pytest.approx(GRID[-1] - GRID[0])


def test_nothing_is_drawn_beyond_the_outermost_fitted_quantile() -> None:
    curve = _normal(0.0, 0.010)
    profile = curve.density_profile(-0.40, 0.40, 41)
    # The extreme columns sit far outside q05..q95, where the curve is pinned.
    assert profile[0] == pytest.approx(0.0)
    assert profile[-1] == pytest.approx(0.0)


def test_a_tighter_forecast_peaks_higher_on_the_same_axis() -> None:
    """The whole point of one shared axis: certainty must be visible."""

    tight = _normal(0.0, 0.006).density_profile(-0.06, 0.06, 41)
    wide = _normal(0.0, 0.024).density_profile(-0.06, 0.06, 41)
    assert max(tight) > max(wide)


@pytest.mark.parametrize("columns,low,high", [(0, -0.05, 0.05), (10, 0.05, 0.05)])
def test_a_nonsense_axis_is_refused(columns: int, low: float, high: float) -> None:
    with pytest.raises(ValueError):
        _normal(0.0, 0.01).density_profile(low, high, columns)


def test_the_mail_draws_the_density_and_says_what_it_leaves_out() -> None:
    message = _render((_candidate("9101", "日本郵船", curve=_normal(0.006, 0.010)),))
    assert "確率密度分布" in message.text
    assert "█" in message.text
    assert "5%〜95%" in message.text
    assert "意図的に描いていません" in message.text


def test_the_mail_prints_every_reported_quantile_as_a_column() -> None:
    message = _render((_candidate("9101", "日本郵船", curve=_normal(0.006, 0.010)),))
    assert "買い候補の分位点" in message.text
    for level in REPORTED_QUANTILE_LEVELS:
        assert f"{level:.0%}" in message.text


def test_a_day_with_no_density_says_so_instead_of_drawing_nothing() -> None:
    message = _render((_candidate("7203", "トヨタ", curve=None),))
    assert "分布がありません" in message.text


def test_the_dashboard_sparklines_share_one_scale() -> None:
    """Per-row scaling made a confident and an unreadable forecast identical."""

    from dashboard.presenters import density_sparklines

    def payload(sigma: float) -> dict[str, object]:
        curve = _normal(0.0, sigma)
        return {
            "levels": [
                {"quantile": level, "return": value} for level, value in curve.pairs()
            ]
        }

    blocks = " ▁▂▃▄▅▆▇█"
    tight, wide, legacy = density_sparklines([payload(0.006), payload(0.024), None])
    assert legacy == "—"
    # The tight forecast must peak higher, and the wide one must span further.
    assert max(blocks.index(ch) for ch in tight) > max(blocks.index(ch) for ch in wide)
    assert sum(ch != " " for ch in wide) > sum(ch != " " for ch in tight)


def test_the_dashboard_handles_an_empty_page() -> None:
    from dashboard.presenters import density_sparklines

    assert density_sparklines([]) == []
    assert density_sparklines([None, None]) == ["—", "—"]


def test_the_mail_warns_that_the_bumps_are_noise_not_modes() -> None:
    """18 bins from 120 sessions is ~6 observations each; the wiggles are sampling
    error, and a reader who takes them for real structure is reading too much."""

    message = _render((_candidate("9101", "日本郵船", curve=_normal(0.006, 0.010)),))
    assert "120営業日" in message.text
    assert "推定のばらつき" in message.text
