"""Prediction helpers kept separate from persistence and presentation."""

from __future__ import annotations

import pandas as pd

from models.base import TickerPrediction
from models.training import TickerModelBundle


def predict_ticker(
    model: TickerModelBundle, features: pd.DataFrame
) -> TickerPrediction:
    """Predict one row with a previously fitted ticker model."""

    return model.predict_one(features)
