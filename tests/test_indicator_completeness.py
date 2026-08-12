"""What the day was actually missing, as opposed to what it managed to build.

``feature_coverage`` answers a narrower question than its name suggests: its
denominator is assembled from the features that materialised, so an indicator
absent from every session in the window never enters it and cannot lower it.
Three days of production reported 1.000 while the morning prefetch was failing
five required series - the number was true and the reassurance it gave was not.

Indicator completeness is measured against the configuration instead, which
states what a ticker needs before any data is fetched, so an indicator that
produced nothing at all is still counted as owed.
"""

from __future__ import annotations

from dataclasses import replace

import pytest

from services.dataset import ModelDataset


def _dataset(**overrides: object) -> ModelDataset:
    """A dataset carrying only the fields these assertions read."""

    import pandas as pd

    from services.dataset import ModelSample

    empty = pd.DataFrame()
    sample = ModelSample(
        ticker="9101",
        sample_date=__import__("datetime").date(2026, 8, 12),
        cutoff_at=__import__("datetime").datetime.now().astimezone(),
        values={},
        lineage={},
    )
    base = ModelDataset(
        ticker="9101",
        feature_names=(),
        training_frame=empty,
        training_target=pd.Series(dtype=float),
        current_frame=empty,
        training_samples=(sample,),
        current_sample=sample,
        candidate_feature_count=0,
        feature_coverage=1.0,
        expected_indicators=("usdjpy", "sp500", "wti"),
        observed_indicators=("usdjpy", "sp500", "wti"),
        missing_required_indicators=(),
        missing_optional_indicators=(),
        indicator_coverage=1.0,
    )
    return replace(base, **overrides)  # type: ignore[arg-type]


def test_a_complete_day_reports_nothing_missing() -> None:
    dataset = _dataset()
    assert dataset.missing_required_indicators == ()
    assert dataset.missing_optional_indicators == ()
    assert dataset.indicator_coverage == pytest.approx(1.0)


def test_a_missing_indicator_lowers_indicator_coverage() -> None:
    """The regression this whole file exists for.

    Under the old accounting an indicator that produced nothing simply
    vanished from the denominator, so coverage stayed at 1.000 while a
    required series was absent from every single session.
    """

    dataset = _dataset(
        observed_indicators=("sp500", "wti"),
        missing_required_indicators=("usdjpy",),
        indicator_coverage=2 / 3,
    )
    assert "usdjpy" in dataset.missing_required_indicators
    assert dataset.indicator_coverage < 1.0


def test_required_and_optional_misses_are_told_apart() -> None:
    dataset = _dataset(
        observed_indicators=("sp500",),
        missing_required_indicators=("usdjpy",),
        missing_optional_indicators=("wti",),
        indicator_coverage=1 / 3,
    )
    assert dataset.missing_required_indicators == ("usdjpy",)
    assert dataset.missing_optional_indicators == ("wti",)


def test_feature_coverage_and_indicator_coverage_are_separate_questions() -> None:
    """A dense matrix built from an incomplete set of inputs is still incomplete."""

    dataset = _dataset(
        feature_coverage=1.0,
        observed_indicators=("sp500", "wti"),
        missing_required_indicators=("usdjpy",),
        indicator_coverage=2 / 3,
    )
    assert dataset.feature_coverage == pytest.approx(1.0)
    assert dataset.indicator_coverage < 1.0
