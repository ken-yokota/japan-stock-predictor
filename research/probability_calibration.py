"""Probability calibration using strictly earlier, outcome-available OOS rows."""

from __future__ import annotations

from typing import Literal

import numpy as np
import pandas as pd
from sklearn.isotonic import IsotonicRegression  # type: ignore[import-untyped]
from sklearn.linear_model import LogisticRegression  # type: ignore[import-untyped]


def probability_metrics(y: np.ndarray, p: np.ndarray) -> dict[str, object]:
    mask = np.isfinite(y) & np.isfinite(p)
    y, p = y[mask], np.clip(p[mask], 1e-8, 1 - 1e-8)
    if not len(y):
        return {"n": 0, "brier": None, "log_loss": None, "ece": None, "reliability": []}
    bins: list[dict[str, object]] = []
    ece = 0.0
    bin_ids = np.minimum((p * 10).astype(int), 9)
    for bin_id in range(10):
        lo = bin_id / 10
        selected = bin_ids == bin_id
        if not selected.any():
            continue
        mean, frequency = float(p[selected].mean()), float(y[selected].mean())
        count = int(selected.sum())
        ece += count / len(y) * abs(mean - frequency)
        bins.append(
            {
                "lower": float(lo),
                "n": count,
                "mean_probability": mean,
                "up_frequency": frequency,
            }
        )
    return {
        "n": len(y),
        "brier": float(np.mean((p - y) ** 2)),
        "log_loss": float(-np.mean(y * np.log(p) + (1 - y) * np.log(1 - p))),
        "ece": ece,
        "reliability": bins,
    }


def calibrate_oos(
    frame: pd.DataFrame,
    *,
    method: Literal["sigmoid", "isotonic"] = "sigmoid",
    scope: Literal["global", "sector", "ticker"] = "global",
    minimum_pairs: int = 100,
) -> pd.DataFrame:
    """Keep feature versions separate; today's realized target never enters fit.

    Input columns: date, cutoff_at, outcome_available_at, feature_version,
    ticker, sector, probability_up, actual_return. Labels unavailable at today's
    cutoff are excluded even when their calendar date is earlier.
    """
    required = {
        "date",
        "cutoff_at",
        "outcome_available_at",
        "feature_version",
        "ticker",
        "sector",
        "probability_up",
        "actual_return",
    }
    if not required <= set(frame):
        raise ValueError("missing probability calibration lineage")
    out = frame.copy().sort_values(["date", "ticker"]).reset_index(drop=True)
    out["calibrated_probability"] = out["probability_up"]
    out["calibration_status"] = "UNCALIBRATED_LOW_SAMPLE"
    out["calibration_pairs"] = 0
    out["calibration_training_end"] = None
    availability = pd.to_datetime(out["outcome_available_at"], utc=True)
    cutoff = pd.to_datetime(out["cutoff_at"], utc=True)
    for idx, row in out.iterrows():
        mask = (out["date"] < row["date"]) & (availability < cutoff.iloc[idx])
        mask &= out["feature_version"] == row["feature_version"]
        if scope != "global":
            mask &= out[scope] == row[scope]
        past = out.loc[mask].dropna(subset=["probability_up", "actual_return"])
        if len(past) < minimum_pairs or not np.isfinite(float(row["probability_up"])):
            continue
        p = np.clip(past["probability_up"].to_numpy(float), 1e-6, 1 - 1e-6)
        y = (past["actual_return"].to_numpy(float) > 0).astype(int)
        if len(np.unique(y)) < 2 or not np.isfinite(p).all():
            continue
        current = float(np.clip(row["probability_up"], 1e-6, 1 - 1e-6))
        if method == "sigmoid":
            model = LogisticRegression(C=1.0, random_state=42)
            model.fit(np.log(p / (1 - p)).reshape(-1, 1), y)
            answer = float(
                model.predict_proba([[np.log(current / (1 - current))]])[0, 1]
            )
        else:
            model = IsotonicRegression(out_of_bounds="clip", y_min=0.01, y_max=0.99)
            model.fit(p, y)
            answer = float(model.predict([current])[0])
        out.loc[idx, "calibrated_probability"] = answer
        out.loc[idx, "calibration_status"] = (
            f"{scope.upper()}_{method.upper()}_PAST_OOS"
        )
        out.loc[idx, "calibration_pairs"] = len(past)
        out.loc[idx, "calibration_training_end"] = past["date"].max()
    return out
