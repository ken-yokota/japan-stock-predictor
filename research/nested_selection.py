"""Training-only sparse selection with inner chronological validation and freeze."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd
from sklearn.model_selection import TimeSeriesSplit  # type: ignore[import-untyped]

from models.ridge import build_ridge_pipeline


@dataclass(frozen=True)
class Selection:
    names: tuple[str, ...]
    chosen_budget: int
    inner_mae: float
    training_rows: int
    stability: dict[str, float]


def stable_ranking(
    x: pd.DataFrame, y: np.ndarray, budget: int
) -> tuple[tuple[str, ...], dict[str, float]]:
    """Filter coverage/duplicates, then rank IC and temporal sign agreement."""
    if len(x) != len(y) or len(x) < 20:
        raise ValueError("selection requires aligned training rows")
    finite = x.replace([np.inf, -np.inf], np.nan)
    eligible = [
        str(n)
        for n in finite
        if finite[n].notna().mean() >= 0.95 and finite[n].std() > 0
    ]
    strength: dict[str, float] = {}
    stability: dict[str, float] = {}
    target = pd.Series(y, index=x.index)
    for n in eligible:
        ic = finite[n].corr(target, method="spearman")
        signs = []
        for part in np.array_split(np.arange(len(x)), 3):
            v = finite[n].iloc[part].corr(target.iloc[part], method="spearman")
            if np.isfinite(v):
                signs.append(np.sign(v))
        stability[n] = (
            max(signs.count(1), signs.count(-1)) / len(signs) if signs else 0.0
        )
        strength[n] = abs(float(ic)) * stability[n] if np.isfinite(ic) else 0.0
    ordered = sorted(eligible, key=lambda n: (-strength[n], n))
    names: list[str] = []
    for n in ordered:
        if any(abs(finite[n].corr(finite[old])) >= 0.90 for old in names):
            continue
        names.append(n)
        if len(names) >= min(budget, len(x) // 10):
            break
    return tuple(names), {n: stability[n] for n in names}


def nested_select(x: pd.DataFrame, y: np.ndarray) -> Selection:
    """Every inner fold repeats feature selection using its training portion."""
    if len(x) != len(y) or len(x) < 80:
        raise ValueError("nested selection requires at least 80 training rows")
    losses: dict[int, float] = {}
    for budget in (3, 5, 8):
        errors: list[float] = []
        for train, validation in TimeSeriesSplit(n_splits=3).split(x):
            names, _ = stable_ranking(x.iloc[train], y[train], budget)
            if not names:
                continue
            model = build_ridge_pipeline(10.0)
            model.fit(x.iloc[train].loc[:, names], y[train])
            prediction = model.predict(x.iloc[validation].loc[:, names])
            errors.extend(np.abs(prediction - y[validation]).tolist())
        losses[budget] = float(np.mean(errors)) if errors else float("inf")
    chosen = min(losses, key=lambda k: (losses[k], k))
    names, stability = stable_ranking(x, y, chosen)
    if not names:
        raise ValueError("no covered nonconstant candidate features")
    return Selection(names, chosen, losses[chosen], len(x), stability)


@dataclass
class FrozenSelection:
    """A 20-session block is the shortest legal research review interval."""

    selected_at_position: int
    selection: Selection
    minimum_sessions: int = 20

    def due(self, position: int) -> bool:
        if self.minimum_sessions < 20:
            raise ValueError("daily selection is forbidden")
        return position - self.selected_at_position >= self.minimum_sessions
