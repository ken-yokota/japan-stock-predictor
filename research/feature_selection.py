"""Choose features from inside the training window, and nowhere else.

The hypothesis this exists to test is that 120 training sessions cannot support
the number of columns being fed to them. Testing it requires selecting features,
and selecting features is the easiest place in this repository to leak the
future: pick the columns that did well across the whole out-of-sample period and
the out-of-sample score stops meaning anything.

So every selector here is a pure function of one training slice. It sees the
rows strictly before the session being predicted and nothing else. Called once
per ticker per session, it re-selects as the window rolls, which is also the
honest thing: a column that stopped working should stop being used.

Two steps, in this order:

1. Drop redundancy. Twenty-eight series times two lookbacks produces columns
   that are near-copies of each other, and ridge splits a coefficient across
   them rather than choosing. Correlated survivors are removed before anything
   is ranked, keeping the one with the stronger relationship to the target.
2. Rank what is left by how it relates to the target *in the training window*,
   by Spearman, and keep the top k.

Spearman rather than Pearson because the thing being predicted is a
cross-sectional ranking, and because a single outlier session should not decide
whether a column survives.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Sequence

import numpy as np
import pandas as pd

# Above this pairwise correlation two columns are treated as one piece of
# information. 0.95 is deliberately permissive: the intent is to remove
# near-duplicates such as a return and its log, not to thin the space.
DEFAULT_REDUNDANCY_THRESHOLD = 0.95

# A column with fewer than this many usable training rows cannot be ranked
# honestly, so it is dropped rather than scored on a handful of points.
MINIMUM_USABLE_ROWS = 30

FeatureSelector = Callable[[pd.DataFrame, np.ndarray], tuple[str, ...]]


def _usable(column: pd.Series) -> np.ndarray:
    return np.asarray(pd.to_numeric(column, errors="coerce"), dtype=float)


def _spearman_against_target(
    values: np.ndarray, target: np.ndarray
) -> float:
    """Rank correlation on the rows where both are present."""

    mask = np.isfinite(values) & np.isfinite(target)
    if mask.sum() < MINIMUM_USABLE_ROWS:
        return 0.0
    a, b = values[mask], target[mask]
    if a.std() == 0 or b.std() == 0:
        return 0.0
    ranked_a = pd.Series(a).rank().to_numpy()
    ranked_b = pd.Series(b).rank().to_numpy()
    correlation = np.corrcoef(ranked_a, ranked_b)[0, 1]
    return 0.0 if math.isnan(correlation) else float(correlation)


def univariate_strength(
    features: pd.DataFrame, target: np.ndarray, names: Sequence[str]
) -> dict[str, float]:
    """|Spearman| of each column against the target, inside this window only."""

    return {
        name: abs(_spearman_against_target(_usable(features[name]), target))
        for name in names
        if name in features
    }


def drop_redundant(
    features: pd.DataFrame,
    names: Sequence[str],
    strength: dict[str, float],
    *,
    threshold: float = DEFAULT_REDUNDANCY_THRESHOLD,
) -> list[str]:
    """Keep the stronger of any two near-identical columns.

    Ridge does not choose between collinear columns; it splits the weight
    across them, which inflates the column count without adding information and
    is exactly what a 120-row window cannot afford.
    """

    ordered = sorted(names, key=lambda n: -strength.get(n, 0.0))
    numeric = features[list(ordered)].apply(pd.to_numeric, errors="coerce")
    kept: list[str] = []
    kept_values: list[np.ndarray] = []
    for name in ordered:
        values = np.asarray(numeric[name], dtype=float)
        if not np.isfinite(values).any() or np.nanstd(values) == 0:
            continue
        duplicate = False
        for existing in kept_values:
            mask = np.isfinite(values) & np.isfinite(existing)
            if mask.sum() < MINIMUM_USABLE_ROWS:
                continue
            a, b = values[mask], existing[mask]
            if a.std() == 0 or b.std() == 0:
                continue
            if abs(np.corrcoef(a, b)[0, 1]) >= threshold:
                duplicate = True
                break
        if not duplicate:
            kept.append(name)
            kept_values.append(values)
    return kept


def select_top_k(
    k: int, *, redundancy_threshold: float = DEFAULT_REDUNDANCY_THRESHOLD
) -> FeatureSelector:
    """A selector keeping ``k`` columns, chosen only from the training window.

    Returns every column when ``k`` is at least the number available, so the
    full-feature arm runs through the same code path as the reduced ones and a
    difference between arms cannot be a difference between code paths.
    """

    if k <= 0:
        raise ValueError("k must be positive")

    def selector(features: pd.DataFrame, target: np.ndarray) -> tuple[str, ...]:
        names = [str(column) for column in features.columns]
        if not names:
            return ()
        strength = univariate_strength(features, target, names)
        survivors = drop_redundant(
            features, names, strength, threshold=redundancy_threshold
        )
        if not survivors:
            return tuple(names[:k])
        ranked = sorted(survivors, key=lambda n: -strength.get(n, 0.0))
        return tuple(ranked[:k])

    return selector


def select_all() -> FeatureSelector:
    """The baseline: change nothing, so the comparison has a fixed reference."""

    def selector(features: pd.DataFrame, target: np.ndarray) -> tuple[str, ...]:
        return tuple(str(column) for column in features.columns)

    return selector
