"""Strict one-step-ahead walk-forward validation."""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from models import (
    InsufficientTrainingData,
    ModelTrainingConfig,
    train_ticker_model,
)

WALK_FORWARD_COLUMNS: tuple[str, ...] = (
    "ticker",
    "prediction_date",
    "training_start",
    "training_end",
    "training_sessions",
    "predicted_return",
    "probability_up",
    "actual_return",
    "ridge_alpha",
    "logistic_c",
    "ridge_coefficients",
    "logistic_coefficients",
    "status",
)


@dataclass(frozen=True, slots=True)
class WalkForwardConfig:
    """Walk-forward settings; each prediction consumes only prior rows."""

    model: ModelTrainingConfig = field(default_factory=ModelTrainingConfig)


def _empty_result() -> pd.DataFrame:
    return pd.DataFrame(columns=WALK_FORWARD_COLUMNS)


def assert_walk_forward_oos(results: pd.DataFrame) -> None:
    """Assert that every successful prediction was trained strictly earlier."""

    required = {"prediction_date", "training_end", "status"}
    missing = sorted(required - set(results.columns))
    if missing:
        raise ValueError(f"missing walk-forward columns: {missing}")
    successful = results.loc[results["status"] == "OK"]
    if successful.empty:
        return
    invalid = successful["training_end"] >= successful["prediction_date"]
    if bool(invalid.any()):
        raise AssertionError("walk-forward result contains look-ahead training rows")


def walk_forward_validate(
    frame: pd.DataFrame,
    *,
    feature_names: tuple[str, ...],
    ticker_column: str = "ticker",
    date_column: str = "market_date",
    target_column: str = "intraday_return",
    config: WalkForwardConfig | None = None,
) -> pd.DataFrame:
    """Retrain on a rolling window and predict only the immediately next row.

    Results are genuinely out of sample: for a prediction at position ``t``,
    the trainer receives rows ``[t-window_size, t)``.  Duplicate ticker/date
    pairs are rejected because their ordering would be ambiguous.
    """

    settings = config or WalkForwardConfig()
    required = {ticker_column, date_column, target_column, *feature_names}
    missing = sorted(required - set(frame.columns))
    if missing:
        raise ValueError(f"missing required columns: {missing}")
    if not feature_names:
        raise ValueError("feature_names must not be empty")
    if frame.empty:
        return _empty_result()
    if frame.duplicated([ticker_column, date_column]).any():
        raise ValueError("duplicate ticker/date rows are not allowed")

    rows: list[dict[str, object]] = []
    ordered = frame.sort_values([ticker_column, date_column], kind="stable")
    window_size = settings.model.window_size
    for raw_ticker, group in ordered.groupby(ticker_column, sort=False):
        ticker = str(raw_ticker)
        ticker_rows = group.reset_index(drop=True)
        for prediction_position in range(window_size, len(ticker_rows)):
            training = ticker_rows.iloc[
                prediction_position - window_size : prediction_position
            ]
            current = ticker_rows.iloc[[prediction_position]]
            prediction_date = current.iloc[0][date_column]
            training_start = training.iloc[0][date_column]
            training_end = training.iloc[-1][date_column]
            if training_end >= prediction_date:
                raise ValueError(
                    f"{ticker} dates are not strictly increasing at {prediction_date}"
                )
            base: dict[str, object] = {
                "ticker": ticker,
                "prediction_date": prediction_date,
                "training_start": training_start,
                "training_end": training_end,
                "training_sessions": len(training),
                "actual_return": float(current.iloc[0][target_column]),
            }
            try:
                model = train_ticker_model(
                    ticker,
                    training.loc[:, feature_names],
                    training[target_column],
                    feature_names=feature_names,
                    config=settings.model,
                )
            except InsufficientTrainingData:
                rows.append(
                    {
                        **base,
                        "predicted_return": np.nan,
                        "probability_up": np.nan,
                        "ridge_alpha": np.nan,
                        "logistic_c": np.nan,
                        "ridge_coefficients": {},
                        "logistic_coefficients": {},
                        "status": "INSUFFICIENT_DATA",
                    }
                )
                continue
            prediction = model.predict_one(current.loc[:, feature_names])
            rows.append(
                {
                    **base,
                    "training_sessions": model.training_sessions,
                    "predicted_return": prediction.predicted_return,
                    "probability_up": prediction.probability_up,
                    "ridge_alpha": prediction.ridge_alpha,
                    "logistic_c": prediction.logistic_c,
                    "ridge_coefficients": model.regression_coefficients(),
                    "logistic_coefficients": model.classification_coefficients(),
                    "status": "OK",
                }
            )

    result = pd.DataFrame.from_records(rows, columns=WALK_FORWARD_COLUMNS)
    assert_walk_forward_oos(result)
    return result


# Readable alias used by batch integrations.
run_walk_forward = walk_forward_validate
