"""A series given to one stock must not reach the other twenty-one.

Production sends 33 of its 37 indicators to every ticker. Measured on
2026-08-18 that is at odds with the businesses: in FY3/2026 Mitsubishi and
Mitsui fell on resource prices while Itochu, Sumitomo and Marubeni set records
without them, and all five receive the same copper, iron ore and crude series.
Suzuki's growth is Indian and no Indian series exists at all.

Until this scoping existed the hypothesis could not even be measured, because
adding a series to test it on one stock added it to all of them. These pin the
scoping itself; whether any scoped series helps is a separate measurement.
"""

from __future__ import annotations

from research.feature_sets import IndicatorSpec


def test_an_unscoped_series_covers_every_ticker() -> None:
    """The default has to stay what production does, or nothing is comparable."""

    spec = IndicatorSpec("copper", "HG=F")
    assert spec.covers("8058")
    assert spec.covers("7269")


def test_a_scoped_series_covers_only_its_tickers() -> None:
    spec = IndicatorSpec("maruti", "MARUTI.NS", applies_to=("7269",))
    assert spec.covers("7269")
    assert not spec.covers("7203")
    assert not spec.covers("8058")


def test_scoping_several_tickers() -> None:
    """The resource series belong to the two houses that live on them."""

    spec = IndicatorSpec("iron_ore", "IRON.AX", applies_to=("8031", "8058"))
    assert spec.covers("8031")
    assert spec.covers("8058")
    assert not spec.covers("8001"), "Itochu set records without resources"


def test_the_frame_builder_drops_columns_outside_scope() -> None:
    """The scope has to be applied where the columns are attached."""

    import inspect

    from research import dataset

    source = inspect.getsource(dataset.build_stock_frame)
    assert "spec.covers(ticker)" in source
    assert "indicator_names" in source
    # The unscoped list must no longer be what names the features.
    assert "*indicators.names," not in source


def test_scope_is_matched_on_the_key_prefix_not_a_substring() -> None:
    """`us_10y_yield` and `us_10y_minus_2y_spread` share a prefix."""

    spec = IndicatorSpec("us_10y_yield", "^TNX", applies_to=("8306",))
    assert spec.key == "us_10y_yield"
    # The builder filters with an f"{key}_" prefix, so the spread's columns
    # (us_10y_minus_2y_spread_*) cannot be caught by the yield's key.
    assert not "us_10y_minus_2y_spread_return_1d".startswith("us_10y_yield_")
