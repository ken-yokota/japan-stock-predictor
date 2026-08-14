"""Pin what Rank IC measures, and pin that it admits when it cannot measure.

The reason this metric exists is that the sign test kept returning "not
adopted" for effects it had no power to see. A replacement that quietly does
the same thing would be worse than the original, so the honesty properties are
tested as hard as the arithmetic: a day that cannot be ranked is dropped rather
than scored zero, and a result below the detection floor is reported as
unmeasured rather than as absence of effect.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from research.metrics import (
    MINIMUM_NAMES,
    paired_rank_ic,
    rank_ic_series,
    summarise_rank_ic,
)


def _day(date: str, predicted: list[float], actual: list[float]) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "date": date,
            "ticker": [f"T{index}" for index in range(len(predicted))],
            "predicted_return": predicted,
            "actual_return": actual,
        }
    )


def test_a_perfectly_ordered_morning_scores_one() -> None:
    frame = _day("2026-08-03", [3.0, 2.0, 1.0], [0.03, 0.02, 0.01])
    assert rank_ic_series(frame).iloc[0] == pytest.approx(1.0)


def test_the_reversed_ordering_scores_minus_one() -> None:
    frame = _day("2026-08-03", [3.0, 2.0, 1.0], [0.01, 0.02, 0.03])
    assert rank_ic_series(frame).iloc[0] == pytest.approx(-1.0)


def test_a_shared_market_move_does_not_create_skill() -> None:
    """Every stock rising is the case direction accuracy scores as 100%.

    A ranking is indifferent to the level, so the same day scores as the
    ordering deserves. This is the whole reason for preferring it: the market
    term that dominates the variance is not knowable at the cutoff, and it
    should not be able to masquerade as skill.
    """

    predicted = [0.001, 0.002, 0.003, 0.004]
    # All four rose, and the ordering is exactly backwards.
    actual = [0.040, 0.030, 0.020, 0.010]
    frame = _day("2026-08-03", predicted, actual)

    direction_accuracy = float(
        ((frame["predicted_return"] > 0) == (frame["actual_return"] > 0)).mean()
    )
    assert direction_accuracy == 1.0
    assert rank_ic_series(frame).iloc[0] == pytest.approx(-1.0)


def test_a_day_with_too_few_names_is_dropped_not_scored() -> None:
    frame = pd.concat(
        [
            _day("2026-08-03", [1.0] * MINIMUM_NAMES, [0.01, 0.02, 0.03]),
            _day("2026-08-04", [1.0, 2.0], [0.01, 0.02]),
        ]
    )
    daily = rank_ic_series(frame)
    assert list(daily.index) == ["2026-08-03"]


def test_a_day_with_no_spread_to_rank_is_dropped() -> None:
    """Identical predictions cannot agree or disagree; that is not an IC of 0."""

    frame = _day("2026-08-03", [1.0, 1.0, 1.0], [0.01, 0.02, 0.03])
    assert rank_ic_series(frame).empty


def test_missing_columns_raise_rather_than_score() -> None:
    frame = pd.DataFrame({"date": ["2026-08-03"], "predicted_return": [1.0]})
    with pytest.raises(KeyError, match="actual_return"):
        rank_ic_series(frame)


def test_the_summary_reports_the_floor_it_could_detect() -> None:
    generator = np.random.default_rng(20260815)
    daily = pd.Series(generator.normal(0.0, 0.30, size=40))
    summary = summarise_rank_ic(daily)

    assert summary.days == 40
    # 2.80 * sd / sqrt(n), the 80%-power two-sided floor.
    expected = 2.801585219 * daily.std(ddof=1) / np.sqrt(40)
    assert summary.detectable_ic == pytest.approx(expected, rel=1e-6)


def test_a_tiny_effect_is_called_unmeasured_not_absent() -> None:
    """The exact failure this module was written to stop repeating."""

    generator = np.random.default_rng(7)
    daily = pd.Series(generator.normal(0.004, 0.30, size=30))
    summary = summarise_rank_ic(daily)

    assert not summary.is_detectable
    assert "判定不能" in summary.verdict()
    assert "効果がない" not in summary.verdict().split("効果がないのではなく")[0]


def test_a_real_effect_is_reported_as_significant() -> None:
    generator = np.random.default_rng(11)
    daily = pd.Series(generator.normal(0.18, 0.20, size=60))
    summary = summarise_rank_ic(daily)

    assert summary.p_value is not None and summary.p_value < 0.05
    assert "有意" in summary.verdict()
    assert summary.information_ratio > 0


def test_serial_correlation_is_surfaced_because_it_invalidates_the_p_value() -> None:
    values = [0.2, 0.2, 0.2, -0.2, -0.2, -0.2, 0.2, 0.2, 0.2, -0.2, -0.2, -0.2]
    summary = summarise_rank_ic(pd.Series(values))
    assert summary.lag1_autocorrelation is not None
    assert summary.lag1_autocorrelation > 0.4


def test_an_empty_window_reports_an_infinite_floor() -> None:
    summary = summarise_rank_ic(pd.Series(dtype=float))
    assert summary.days == 0
    assert summary.p_value is None
    assert summary.detectable_ic == float("inf")
    assert not summary.is_detectable


def test_pairing_compares_only_the_names_both_arms_predicted() -> None:
    """An arm covering more tickers must not win on coverage alone."""

    baseline = pd.concat(
        [
            _day("2026-08-03", [3.0, 2.0, 1.0], [0.01, 0.02, 0.03]),
            _day("2026-08-04", [3.0, 2.0, 1.0], [0.01, 0.02, 0.03]),
        ]
    )
    candidate = baseline.copy()
    candidate["predicted_return"] = [1.0, 2.0, 3.0, 1.0, 2.0, 3.0]
    extra = _day("2026-08-03", [9.0, 8.0, 7.0], [0.09, 0.08, 0.07])
    extra["ticker"] = ["X0", "X1", "X2"]
    candidate = pd.concat([candidate, extra])

    summary = paired_rank_ic(candidate, baseline)
    # Baseline is reversed (-1) on both days, candidate is aligned (+1).
    assert summary.days == 2
    assert summary.mean == pytest.approx(2.0)
