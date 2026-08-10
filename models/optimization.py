"""Chronological hyperparameter selection with ``TimeSeriesSplit``."""

from __future__ import annotations

from collections.abc import Sequence

import numpy as np
import pandas as pd
from sklearn.model_selection import TimeSeriesSplit  # type: ignore[import-untyped]
from sklearn.pipeline import Pipeline  # type: ignore[import-untyped]

from models.classifier import build_logistic_pipeline
from models.ridge import build_ridge_pipeline


def as_model_matrix(features: pd.DataFrame | np.ndarray) -> np.ndarray:
    """Return the float matrix scikit-learn would build from ``features``.

    Selection refits the same frame 45 times per prediction, and every fit made
    scikit-learn re-derive this conversion from a freshly sliced DataFrame.
    Measured on one research window, validation and imputation input-checking
    accounted for 317 of 559 seconds. Doing the conversion once is arithmetic
    scikit-learn performs anyway, so no fitted value changes; only the number
    of times the frame is inspected does.
    """

    return np.asarray(features, dtype=float)


def fit_with_weights(
    pipeline: Pipeline,
    features: pd.DataFrame | np.ndarray,
    targets: np.ndarray,
    weights: np.ndarray | None,
) -> None:
    """Fit a pipeline, weighting only the final estimator.

    The imputer's median and the scaler's mean/scale stay unweighted on
    purpose: they describe what the feature *is*, and reweighting them would
    make the standardization depend on how recent the rows are.

    Weights are rescaled here, at the point of fitting, to sum to the number of
    rows being fitted. Normalizing once when the weights are built is not
    enough: cross-validation hands each fold a *slice* of that array, and an
    early fold holds only the oldest, lightest rows. Measured on a 250-session
    window with a 60-session half-life, the first fold's weights summed to 10
    against 45 rows, making a given alpha bite 4.5x harder there than in the
    last fold -- so hyperparameter selection was comparing folds that were not
    regularized alike. Rescaling per fit is idempotent for an already-balanced
    array, so the final full-window fit is unchanged.
    """

    if weights is None:
        pipeline.fit(features, targets)
        return
    total = float(np.sum(weights))
    if total <= 0.0:
        raise ValueError("sample weights must sum to a positive value")
    balanced = np.asarray(weights, dtype=float) * (len(targets) / total)
    pipeline.fit(features, targets, model__sample_weight=balanced)


def chronological_splitter(
    sample_count: int, requested_splits: int
) -> TimeSeriesSplit | None:
    """Return a ``TimeSeriesSplit`` sized for the sample, or ``None`` if too small.

    At least two samples are required in the earliest training fold.  This also
    avoids fragile one-row median/scaler fits for tiny unit-test inputs.
    """

    maximum = sample_count - 2
    if maximum < 2:
        return None
    return TimeSeriesSplit(n_splits=min(requested_splits, maximum))


# Retained internal alias for existing call sites.
_splitter = chronological_splitter


def select_ridge_alpha(
    features: pd.DataFrame,
    targets: np.ndarray,
    *,
    candidates: Sequence[float],
    n_splits: int,
    sample_weight: np.ndarray | None = None,
) -> float:
    """Choose alpha by mean chronological validation squared error.

    ``sample_weight`` is applied to the training half of each fold only. The
    validation error stays unweighted, because the question being asked is
    "which alpha predicts unseen sessions best", not "which alpha fits the
    weighted history best".
    """

    if not candidates:
        raise ValueError("candidates must not be empty")
    splitter = _splitter(len(features), n_splits)
    if splitter is None:
        return float(candidates[0])

    matrix = as_model_matrix(features)
    best_value = float(candidates[0])
    best_loss = float("inf")
    for candidate in candidates:
        fold_losses: list[float] = []
        for train_positions, validation_positions in splitter.split(matrix):
            pipeline = build_ridge_pipeline(float(candidate))
            fit_with_weights(
                pipeline,
                matrix[train_positions],
                targets[train_positions],
                None if sample_weight is None else sample_weight[train_positions],
            )
            prediction = np.asarray(
                pipeline.predict(matrix[validation_positions]), dtype=float
            )
            error = prediction - targets[validation_positions]
            fold_losses.append(float(np.mean(np.square(error))))
        mean_loss = float(np.mean(fold_losses))
        if mean_loss < best_loss:
            best_value = float(candidate)
            best_loss = mean_loss
    return best_value


def select_logistic_c(
    features: pd.DataFrame,
    targets: np.ndarray,
    *,
    candidates: Sequence[float],
    n_splits: int,
    random_state: int,
    sample_weight: np.ndarray | None = None,
) -> float:
    """Choose C by chronological validation Brier loss.

    Folds whose training segment contains only one class are skipped.  If all
    folds are single-class, the first configured candidate is returned and the
    final trainer uses a safe constant-probability fallback.
    """

    if not candidates:
        raise ValueError("candidates must not be empty")
    splitter = _splitter(len(features), n_splits)
    if splitter is None:
        return float(candidates[0])

    matrix = as_model_matrix(features)
    best_value = float(candidates[0])
    best_loss = float("inf")
    for candidate in candidates:
        fold_losses: list[float] = []
        for train_positions, validation_positions in splitter.split(matrix):
            training_targets = targets[train_positions]
            if len(np.unique(training_targets)) < 2:
                continue
            pipeline = build_logistic_pipeline(
                float(candidate), random_state=random_state
            )
            fit_with_weights(
                pipeline,
                matrix[train_positions],
                training_targets,
                None if sample_weight is None else sample_weight[train_positions],
            )
            probabilities = np.asarray(
                pipeline.predict_proba(matrix[validation_positions]),
                dtype=float,
            )[:, 1]
            actual = targets[validation_positions].astype(float)
            fold_losses.append(float(np.mean(np.square(probabilities - actual))))
        if not fold_losses:
            continue
        mean_loss = float(np.mean(fold_losses))
        if mean_loss < best_loss:
            best_value = float(candidate)
            best_loss = mean_loss
    return best_value
