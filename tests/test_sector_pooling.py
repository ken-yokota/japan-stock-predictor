"""Pooling a sector is a power change, not a data change.

A daily return is mostly beta times a market move nobody knows at 08:30, and
the predictable residual has to be found in 120 observations against more
predictors than that. Measured on this repository, the smallest detectable
difference in direction accuracy is about 3.1pp, and four indicator proposals
came in between +0.19 and +0.93pp - the test could not tell. Pooling three to
five tickers multiplies the training rows without adding a single series.

What must not change while doing it: the walk-forward boundary. Every row used
to fit must come from a session strictly before the one being predicted, for
every ticker in the pool.
"""

from __future__ import annotations

import inspect

from research import walk


def test_pooling_groups_by_sector() -> None:
    class Stock:
        sector = "shipping"

    assert walk._pool_key(Stock()) == "shipping"


def test_a_stock_without_a_sector_still_groups() -> None:
    class Stock:
        sector = None

    assert walk._pool_key(Stock()) == "unknown"


def test_the_pooled_path_keeps_the_walk_forward_boundary() -> None:
    """The one line that separates this from a look-ahead result."""

    source = inspect.getsource(walk.run_pooled_window)
    assert 'frame["market_date"] < target_date' in source
    # Never <=: including the session being predicted would hand over the answer.
    assert 'frame["market_date"] <= target_date' not in source


def test_the_pool_trains_on_more_than_one_ticker() -> None:
    source = inspect.getsource(walk.run_pooled_window)
    assert "pd.concat(training_rows" in source
    assert "for stock in present:" in source


def test_pooled_features_are_the_intersection() -> None:
    """An ADR belongs to one company and cannot be pooled across a sector."""

    source = inspect.getsource(walk.run_pooled_window)
    assert "shared &= set(" in source


def test_the_per_ticker_path_is_untouched() -> None:
    """Production still runs per ticker; this is a research comparison."""

    source = inspect.getsource(walk.run_window)
    assert "train_ticker_model(" in source
    assert "pooled" not in source


def test_pooled_predictions_carry_their_pool() -> None:
    """A comparison has to be able to say which model made which prediction."""

    source = inspect.getsource(walk.run_pooled_window)
    assert '"pool": pool_name' in source
