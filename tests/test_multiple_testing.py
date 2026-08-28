"""The correction has to actually correct, and the bootstrap has to keep the day.

Twenty-two tickers were scored and three came back above z = 2. Whether that is
three findings or the shape of twenty-two draws is the whole question, so the
arithmetic answering it is pinned against cases with known answers.
"""

from __future__ import annotations

import pytest

from research.multiple_testing import (
    _block_bootstrap,
    _normal_two_sided_p,
    benjamini_hochberg,
    evaluate_tickers,
)

# --------------------------------------------------------------------------
# The p-value


def test_two_sigma_is_about_five_percent() -> None:
    assert _normal_two_sided_p(1.96) == pytest.approx(0.05, abs=0.001)


def test_zero_z_is_certainty_of_nothing() -> None:
    assert _normal_two_sided_p(0.0) == pytest.approx(1.0)


def test_the_sign_of_z_does_not_change_a_two_sided_p() -> None:
    assert _normal_two_sided_p(2.5) == pytest.approx(_normal_two_sided_p(-2.5))


# --------------------------------------------------------------------------
# The correction


def test_a_single_test_is_not_penalised() -> None:
    assert benjamini_hochberg([0.01]) == pytest.approx([0.01])


def test_the_smallest_p_of_many_is_scaled_by_how_many() -> None:
    """0.01 out of twenty-two tries is not 0.01 worth of evidence."""

    values = [0.01] + [0.5] * 21

    adjusted = benjamini_hochberg(values)

    assert adjusted[0] == pytest.approx(0.22, abs=0.001)


def test_adjusted_values_never_decrease_as_p_grows() -> None:
    values = [0.001, 0.01, 0.04, 0.2, 0.9]

    adjusted = benjamini_hochberg(values)
    pairs = sorted(zip(values, adjusted, strict=True))

    assert [q for _, q in pairs] == sorted(q for _, q in pairs)


def test_adjusted_values_are_capped_at_one() -> None:
    assert all(q <= 1.0 for q in benjamini_hochberg([0.9, 0.95, 0.99]))


def test_an_empty_family_returns_nothing() -> None:
    assert benjamini_hochberg([]) == []


# --------------------------------------------------------------------------
# The bootstrap keeps the session together


def test_the_interval_is_wider_when_a_day_moves_as_one() -> None:
    """Resampling predictions instead of sessions would hide the correlation.

    Ten sessions of five names that all agree carry ten observations, not
    fifty. An interval built as if they were fifty is far too narrow, which is
    the same error as reading 46 trades from 8 days as 46 samples.
    """

    correlated = [[True] * 5 if index % 2 else [False] * 5 for index in range(10)]
    independent = [[True, False, True, False, True] for _ in range(10)]

    low_c, high_c = _block_bootstrap(correlated, samples=2000)
    low_i, high_i = _block_bootstrap(independent, samples=2000)

    assert (high_c - low_c) > (high_i - low_i)


def test_an_all_correct_record_gives_an_interval_at_one() -> None:
    low, high = _block_bootstrap([[True, True]] * 20, samples=500)

    assert low == pytest.approx(1.0)
    assert high == pytest.approx(1.0)


def test_no_sessions_gives_no_interval_rather_than_a_number() -> None:
    low, high = _block_bootstrap([])

    assert low != low  # nan
    assert high != high


# --------------------------------------------------------------------------
# Put together


def test_one_strong_ticker_among_many_null_ones_loses_its_significance() -> None:
    """The case the module exists for."""

    nested: dict[str, list[list[bool]]] = {}
    # One ticker at 60% over 250 predictions.
    nested["strong"] = [
        [index % 5 != 0] for index in range(250)
    ]
    # Twenty-one tickers at exactly chance.
    for number in range(21):
        nested[f"null{number}"] = [[index % 2 == 0] for index in range(250)]

    results = {item.ticker: item for item in evaluate_tickers(nested)}
    strong = results["strong"]

    assert strong.raw_p < 0.05
    assert strong.adjusted_q > strong.raw_p


def test_a_ticker_with_no_predictions_is_skipped_not_scored() -> None:
    results = evaluate_tickers({"empty": [], "real": [[True], [False], [True]]})

    assert {item.ticker for item in results} == {"real"}


def test_every_ticker_carries_its_own_sample_size() -> None:
    results = evaluate_tickers({"a": [[True, False], [True]], "b": [[False]]})

    by_ticker = {item.ticker: item for item in results}
    assert by_ticker["a"].sessions == 2
    assert by_ticker["a"].predictions == 3
    assert by_ticker["b"].predictions == 1
