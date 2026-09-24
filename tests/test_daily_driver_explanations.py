"""A daily explanation must describe this forecast, not just fitted weights."""

from __future__ import annotations

from types import SimpleNamespace

import numpy as np
import pandas as pd

from services.prediction import _drivers


def test_daily_driver_sign_uses_the_current_standardized_value() -> None:
    statistics = SimpleNamespace(
        means={"fx": 100.0, "oil": 0.0},
        scales={"fx": 10.0, "oil": 2.0},
    )
    model = SimpleNamespace(
        feature_names=("fx", "oil"),
        scaler_statistics=lambda task: statistics,
    )
    dataset = SimpleNamespace(current_frame=pd.DataFrame({"fx": [90.0], "oil": [-2.0]}))

    positive, negative, explained = _drivers(
        model, dataset, {"fx": 0.02, "oil": -0.01}
    )

    assert explained
    assert positive == ("oil (+1.00%)",)
    assert negative == ("fx (-2.00%)",)


def test_missing_stats_or_input_never_falls_back_to_coefficient_sign() -> None:
    dataset = SimpleNamespace(current_frame=pd.DataFrame({"fx": [90.0]}))
    unavailable = SimpleNamespace(
        feature_names=("fx",), scaler_statistics=lambda task: None
    )
    assert _drivers(unavailable, dataset, {"fx": 0.02}) == ((), (), False)

    statistics = SimpleNamespace(means={"fx": 100.0}, scales={"fx": 10.0})
    available = SimpleNamespace(
        feature_names=("fx",), scaler_statistics=lambda task: statistics
    )
    missing = SimpleNamespace(current_frame=pd.DataFrame({"fx": [np.nan]}))
    assert _drivers(available, missing, {"fx": 0.02}) == ((), (), False)
