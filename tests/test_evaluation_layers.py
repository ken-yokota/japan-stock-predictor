"""The three layers must be measurable apart, and each must be right on its own.

These are checked against hand-computable cases rather than against the live
numbers, because the point of the module is to be trusted when it disagrees
with an intuition. A metric that only ever gets run on real data is a metric
nobody has verified.
"""

from __future__ import annotations

import math

import pytest

from research.evaluation import (
    Prediction,
    evaluate,
    model_quality,
    probability_quality,
    quantile_is_monotonic,
    quantile_table,
    selection_quality,
    session_selection,
    spearman,
    trading_quality,
)


def _p(
    date: str,
    ticker: str,
    predicted: float,
    actual: float,
    *,
    probability: float | None = None,
    signal: str = "NO_BUY",
    net: float | None = None,
    gross: float | None = None,
    cost: float | None = None,
) -> Prediction:
    return Prediction(
        date=date,
        ticker=ticker,
        predicted_return=predicted,
        actual_return=actual,
        probability_up=probability,
        signal=signal,
        net_profit_jpy=net,
        gross_profit_jpy=gross,
        cost_jpy=cost,
    )


# --------------------------------------------------------------------------
# Model layer


def test_a_perfect_forecast_scores_as_perfect() -> None:
    rows = [
        _p("2026-08-03", f"T{i}", v, v)
        for i, v in enumerate([-0.02, 0.0, 0.01, 0.03])
    ]

    quality = model_quality(rows)

    assert quality.mae == pytest.approx(0.0)
    assert quality.rmse == pytest.approx(0.0)
    assert quality.calibration_slope == pytest.approx(1.0)
    assert quality.calibration_intercept == pytest.approx(0.0, abs=1e-12)
    assert quality.bias == pytest.approx(0.0)


def test_a_forecast_ten_times_too_large_shows_a_slope_of_one_tenth() -> None:
    """This is the shape the live model is in: right order, wrong magnitude."""

    actual = [-0.01, 0.0, 0.005, 0.02]
    rows = [_p("2026-08-03", f"T{i}", v * 10, v) for i, v in enumerate(actual)]

    quality = model_quality(rows)

    assert quality.calibration_slope == pytest.approx(0.1)
    assert quality.bias > 0  # overshooting on average
    assert quality.direction_accuracy == pytest.approx(1.0)


def test_direction_accuracy_counts_signs_not_sizes() -> None:
    rows = [
        _p("2026-08-03", "A", 0.05, 0.001),
        _p("2026-08-03", "B", -0.05, -0.001),
        _p("2026-08-03", "C", 0.05, -0.001),
    ]

    assert model_quality(rows).direction_accuracy == pytest.approx(2 / 3)


def test_bias_is_the_gap_between_what_was_claimed_and_what_happened() -> None:
    rows = [_p("2026-08-03", "A", 0.01, 0.0), _p("2026-08-03", "B", 0.01, 0.0)]

    assert model_quality(rows).bias == pytest.approx(0.01)


# --------------------------------------------------------------------------
# Quantiles: does a bigger forecast mean a bigger outcome?


def test_a_monotonic_relationship_is_reported_as_monotonic() -> None:
    rows = [
        _p("2026-08-03", f"T{i}", i / 1000, i / 2000) for i in range(-10, 10)
    ]

    table = quantile_table(rows)

    assert len(table) == 5
    assert quantile_is_monotonic(table)


def test_an_inverted_relationship_is_not_reported_as_monotonic() -> None:
    """If the strongest forecasts do worst, no threshold on them can help."""

    rows = [_p("2026-08-03", f"T{i}", i / 1000, -i / 2000) for i in range(-10, 10)]

    assert not quantile_is_monotonic(quantile_table(rows))


# --------------------------------------------------------------------------
# Selection layer


def test_rank_ic_is_one_when_the_ordering_is_right() -> None:
    predicted = [0.03, 0.02, 0.01, -0.01]
    actual = [0.04, 0.03, 0.001, -0.02]

    assert spearman(predicted, actual) == pytest.approx(1.0)


def test_rank_ic_is_minus_one_when_the_ordering_is_exactly_backwards() -> None:
    assert spearman([0.03, 0.02, 0.01], [0.01, 0.02, 0.03]) == pytest.approx(-1.0)


def test_selection_alpha_is_the_top_names_minus_the_whole_universe() -> None:
    rows = [
        _p("2026-08-03", "A", 0.03, 0.04),
        _p("2026-08-03", "B", 0.02, 0.02),
        _p("2026-08-03", "C", 0.01, 0.00),
        _p("2026-08-03", "D", 0.00, -0.02),
    ]

    session = session_selection(rows)[0]

    assert session.universe_mean == pytest.approx(0.01)
    assert session.top1 == pytest.approx(0.04)
    assert session.alpha(session.top1) == pytest.approx(0.03)
    assert session.top3 == pytest.approx((0.04 + 0.02 + 0.00) / 3)


def test_a_selection_that_adds_nothing_reports_zero_alpha() -> None:
    """Picking names whose realised return equals the universe adds nothing."""

    rows = [
        _p("2026-08-03", t, p, 0.01)
        for t, p in (("A", 0.03), ("B", 0.02), ("C", 0.01))
    ]

    session = session_selection(rows)[0]

    assert session.alpha(session.top1) == pytest.approx(0.0)


def test_selection_quality_reports_a_t_statistic_beside_the_mean() -> None:
    """A mean with no dispersion beside it invites reading noise as a result."""

    rows = []
    for index, day in enumerate(["2026-08-03", "2026-08-04", "2026-08-05"]):
        rows += [
            _p(day, "A", 0.03, 0.02 + index / 1000),
            _p(day, "B", 0.02, 0.01),
            _p(day, "C", 0.01, 0.00),
        ]

    quality = selection_quality(session_selection(rows))

    assert quality.sessions == 3
    assert quality.rank_ic_mean == pytest.approx(1.0)
    assert quality.top1_alpha is not None
    assert quality.top1_alpha > 0


def test_a_session_with_too_few_names_is_skipped_not_guessed() -> None:
    rows = [_p("2026-08-03", "A", 0.01, 0.01), _p("2026-08-03", "B", 0.02, 0.02)]

    assert session_selection(rows) == []


# --------------------------------------------------------------------------
# Probability layer


def test_a_perfectly_calibrated_score_has_the_bin_rate_it_claims() -> None:
    rows = [
        _p("2026-08-03", f"U{i}", 0.01, 0.01, probability=0.62) for i in range(6)
    ] + [_p("2026-08-03", f"D{i}", 0.01, -0.01, probability=0.62) for i in range(4)]

    quality = probability_quality(rows)

    bin_60_65 = next(b for b in quality.bins if b.low == 0.60)
    assert bin_60_65.count == 10
    assert bin_60_65.actual_up_rate == pytest.approx(0.6)


def test_brier_is_zero_for_a_score_that_is_always_right() -> None:
    rows = [
        _p("2026-08-03", "A", 0.01, 0.01, probability=1.0),
        _p("2026-08-03", "B", -0.01, -0.01, probability=0.0),
    ]

    assert probability_quality(rows).brier == pytest.approx(0.0)


def test_brier_is_one_for_a_score_that_is_always_wrong() -> None:
    rows = [
        _p("2026-08-03", "A", 0.01, 0.01, probability=0.0),
        _p("2026-08-03", "B", -0.01, -0.01, probability=1.0),
    ]

    assert probability_quality(rows).brier == pytest.approx(1.0)


def test_log_loss_does_not_blow_up_on_a_certain_and_wrong_score() -> None:
    """An unclipped log(0) makes the metric useless exactly when it matters."""

    rows = [_p("2026-08-03", "A", 0.01, 0.01, probability=0.0)]

    loss = probability_quality(rows).log_loss

    assert loss is not None and math.isfinite(loss)


def test_predictions_without_a_probability_are_excluded_not_defaulted() -> None:
    rows = [_p("2026-08-03", "A", 0.01, 0.01)]

    assert probability_quality(rows).count == 0


# --------------------------------------------------------------------------
# Trading layer, kept separate from the two above


def test_trade_level_and_day_level_are_counted_separately() -> None:
    """Same-day names move together; N trades are not N independent results."""

    rows = [
        _p("2026-08-03", "A", 0.01, 0.01, signal="BUY", net=1000.0),
        _p("2026-08-03", "B", 0.01, 0.01, signal="BUY", net=2000.0),
        _p("2026-08-04", "A", 0.01, -0.01, signal="BUY", net=-5000.0),
    ]

    quality = trading_quality(rows)

    assert quality.trades == 3
    assert quality.sessions == 2
    assert quality.winning_days == 1
    assert quality.losing_days == 1
    assert quality.net_jpy == pytest.approx(-2000.0)


def test_a_win_rate_above_half_can_still_lose_money() -> None:
    """The live system's shape: wins more often, loses more per loss."""

    rows = [
        _p("2026-08-03", "A", 0.01, 0.01, signal="BUY", net=1000.0),
        _p("2026-08-04", "B", 0.01, 0.01, signal="BUY", net=1000.0),
        _p("2026-08-05", "C", 0.01, -0.01, signal="BUY", net=-5000.0),
    ]

    quality = trading_quality(rows)

    assert quality.win_rate == pytest.approx(2 / 3)
    assert quality.net_jpy < 0
    assert quality.payoff_ratio == pytest.approx(0.2)


def test_max_drawdown_is_measured_on_the_daily_equity_curve() -> None:
    rows = [
        _p("2026-08-03", "A", 0.01, 0.01, signal="BUY", net=10000.0),
        _p("2026-08-04", "A", 0.01, -0.01, signal="BUY", net=-30000.0),
        _p("2026-08-05", "A", 0.01, 0.01, signal="BUY", net=5000.0),
    ]

    quality = trading_quality(rows)

    assert quality.max_drawdown_jpy == pytest.approx(-30000.0)
    assert quality.worst_day_jpy == pytest.approx(-30000.0)


def test_a_small_sample_is_flagged_as_underpowered() -> None:
    rows = [_p("2026-08-03", "A", 0.01, 0.01, signal="BUY", net=1.0)]

    assert trading_quality(rows).underpowered


def test_only_the_signalled_rows_become_trades() -> None:
    rows = [
        _p("2026-08-03", "A", 0.01, 0.01, signal="BUY", net=100.0),
        _p("2026-08-03", "B", 0.01, 0.05, signal="NO_BUY", net=None),
    ]

    assert trading_quality(rows).trades == 1


# --------------------------------------------------------------------------
# The layers must be able to disagree


def test_a_set_can_predict_better_and_trade_worse() -> None:
    """This disagreement is the whole reason the layers are reported apart.

    On 2026-08-17 a candidate improved prediction error at p=1.1e-11 and moved
    the profit factor from 1.15 to 1.03. A single headline number hides that;
    two layers show it.
    """

    rows = [
        # Accurate on the many, wrong on the one that was traded.
        _p("2026-08-03", "A", 0.001, 0.001),
        _p("2026-08-03", "B", 0.001, 0.001),
        _p("2026-08-03", "C", 0.02, -0.02, signal="BUY", net=-20000.0),
    ]

    result = evaluate(rows, label="candidate")

    assert result.model.mae < 0.02  # the model layer looks fine
    assert result.trading.net_jpy < 0  # the trading layer does not
    assert result.trading.underpowered  # and it cannot decide anything anyway
