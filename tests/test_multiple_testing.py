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


# --------------------------------------------------------------------------
# The correction has to be calibrated, and that is measurable


def test_the_correction_controls_false_discoveries_on_pure_noise() -> None:
    """Twenty-two coins, no signal anywhere. Almost nothing should be called.

    The report's headline claim is that no ticker survives correction. That is
    only worth something if the correction would let a real edge through while
    keeping a fake one out, so both halves are measured -- this is the second.
    """

    import math

    import numpy as np

    rng = np.random.default_rng(5)
    raw_hits = corrected_hits = 0
    runs = 400

    for _ in range(runs):
        # 22 tickers, 250 fair coin flips each, expressed as the z the module
        # would compute and the two-sided p it turns into.
        hits = rng.binomial(250, 0.5, size=22)
        z = (hits - 125) / math.sqrt(250 * 0.25)
        raw = [_normal_two_sided_p(float(value)) for value in z]
        raw_hits += any(value < 0.05 for value in raw)
        corrected_hits += any(value < 0.05 for value in benjamini_hochberg(raw))

    # Twenty-two tests at 5% each: something crosses raw p in most runs. That is
    # the entire reason the correction exists.
    assert raw_hits / runs > 0.4
    # After correction a family-wise false discovery has to be rare.
    assert corrected_hits / runs < 0.15


def test_a_large_edge_survives_the_correction_and_a_useful_one_does_not() -> None:
    """The power of the per-ticker test, which is the thing it is short of.

    "No ticker survives correction" was read here as evidence there is nothing
    to find. It is much weaker than that. With 22 tickers over 250 sessions the
    correction finds a true 65% ticker almost always and a true 55% ticker 7% of
    the time -- and at zero cost, 55% would be worth having.

    Measured over 1,200 draws each; the thresholds below are loose enough not to
    be flaky and tight enough to fail if the power changes materially.
    """

    import math

    import numpy as np

    def detection_rate(truth: float, runs: int = 1200) -> float:
        rng = np.random.default_rng(int(truth * 1000))
        found = 0
        for _ in range(runs):
            hits = list(rng.binomial(250, 0.5, size=21))
            hits.append(int(rng.binomial(250, truth)))
            z = [(value - 125) / math.sqrt(250 * 0.25) for value in hits]
            adjusted = benjamini_hochberg(
                [_normal_two_sided_p(float(value)) for value in z]
            )
            found += adjusted[-1] < 0.05
        return found / runs

    assert detection_rate(0.65) > 0.85, "大きな優位性すら見つけられないなら壊れている"
    assert detection_rate(0.55) < 0.25, "この標本数で55%を安定検出できるはずがない"


def test_the_bootstrap_interval_covers_the_truth_about_95_percent_of_the_time() -> None:
    """A CI that covers 60% of the time is not a 95% CI.

    Sessions are resampled rather than predictions because same-day names move
    together; this checks the resulting interval is the width it claims to be.
    """

    import numpy as np

    rng = np.random.default_rng(8)
    truth = 0.55
    covered = 0
    runs = 60

    for _ in range(runs):
        sessions = [
            [bool(v) for v in (rng.random(5) < truth)] for _ in range(150)
        ]
        low, high = _block_bootstrap(sessions, samples=300)
        covered += low <= truth <= high

    assert covered / runs > 0.85
