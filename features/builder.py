"""Pure, ticker-aware feature engineering for OHLC price histories."""

from __future__ import annotations

from collections.abc import Sequence

import numpy as np
import pandas as pd

RETURN_WINDOWS: tuple[int, ...] = (1, 2, 3, 5, 20)
PRICE_FEATURE_COLUMNS: tuple[str, ...] = (
    "return_1d",
    "return_2d",
    "return_3d",
    "return_5d",
    "return_20d",
    "log_return_1d",
    "volatility_5d",
    "volatility_20d",
    "open_close_return",
    "high_low_range",
    "ma20_deviation",
)


def _validate_columns(frame: pd.DataFrame, required: Sequence[str]) -> None:
    missing = [column for column in required if column not in frame.columns]
    if missing:
        raise ValueError(f"missing required columns: {missing}")


def _finite_ratio(numerator: pd.Series, denominator: pd.Series) -> pd.Series:
    ratio = numerator / denominator.where(denominator != 0.0)
    return ratio.replace([np.inf, -np.inf], np.nan)


def _ordered_frame(
    frame: pd.DataFrame,
    *,
    ticker_column: str | None,
    date_column: str | None,
) -> pd.DataFrame:
    result = frame.copy(deep=True)
    if "__feature_row_order" in result.columns:
        raise ValueError("reserved column __feature_row_order is present")
    result["__feature_row_order"] = np.arange(len(result), dtype=np.int64)
    sort_columns: list[str] = []
    if ticker_column is not None:
        _validate_columns(result, [ticker_column])
        sort_columns.append(ticker_column)
    if date_column is not None and date_column in result.columns:
        sort_columns.append(date_column)
    if sort_columns:
        result = result.sort_values(
            [*sort_columns, "__feature_row_order"], kind="stable"
        )
    return result


def build_price_features(
    frame: pd.DataFrame,
    *,
    ticker_column: str | None = "ticker",
    date_column: str | None = "market_date",
) -> pd.DataFrame:
    """Add return, volatility, range, and moving-average features.

    Calculations are isolated by ticker and ordered by ``date_column``.  The
    returned rows retain the caller's original order and index.  Ratios with a
    zero/non-positive denominator, and log returns from non-positive prices,
    become ``NaN`` so that a training-only imputer can handle them safely.

    ``high_low_range`` is defined as ``high / low - 1`` and volatility is the
    sample standard deviation of one-session close returns.
    """

    _validate_columns(frame, ["open", "high", "low", "close"])
    if frame.empty:
        result = frame.copy(deep=True)
        for column in PRICE_FEATURE_COLUMNS:
            result[column] = pd.Series(dtype="float64", index=result.index)
        return result

    ordered = _ordered_frame(
        frame, ticker_column=ticker_column, date_column=date_column
    )
    for column in ("open", "high", "low", "close"):
        ordered[column] = pd.to_numeric(ordered[column], errors="coerce")

    group_keys: pd.Series
    if ticker_column is None:
        group_keys = pd.Series(0, index=ordered.index)
    else:
        group_keys = ordered[ticker_column]

    def transform_close(function: object) -> pd.Series:
        return (
            ordered["close"]
            .groupby(group_keys, sort=False, dropna=False)
            .transform(function)
        )

    for window in RETURN_WINDOWS:
        ordered[f"return_{window}d"] = transform_close(
            lambda values, periods=window: values.pct_change(
                periods=periods, fill_method=None
            )
        ).replace([np.inf, -np.inf], np.nan)

    prior_close = transform_close(lambda values: values.shift(1))
    positive_close = ordered["close"].where(ordered["close"] > 0.0)
    positive_prior_close = prior_close.where(prior_close > 0.0)
    ordered["log_return_1d"] = np.log(
        _finite_ratio(positive_close, positive_prior_close)
    )

    one_day_return = ordered["return_1d"]
    ordered["volatility_5d"] = one_day_return.groupby(
        group_keys, sort=False, dropna=False
    ).transform(lambda values: values.rolling(5, min_periods=5).std(ddof=1))
    ordered["volatility_20d"] = one_day_return.groupby(
        group_keys, sort=False, dropna=False
    ).transform(lambda values: values.rolling(20, min_periods=20).std(ddof=1))

    ordered["open_close_return"] = (
        _finite_ratio(ordered["close"], ordered["open"]) - 1.0
    )
    ordered["high_low_range"] = _finite_ratio(ordered["high"], ordered["low"]) - 1.0
    moving_average_20 = transform_close(
        lambda values: values.rolling(20, min_periods=20).mean()
    )
    ordered["ma20_deviation"] = _finite_ratio(ordered["close"], moving_average_20) - 1.0

    ordered = ordered.sort_values("__feature_row_order", kind="stable")
    return ordered.drop(columns="__feature_row_order")


def add_intraday_targets(frame: pd.DataFrame) -> pd.DataFrame:
    """Add the modeled intraday return and display-only price difference."""

    _validate_columns(frame, ["open", "close"])
    result = frame.copy(deep=True)
    opening = pd.to_numeric(result["open"], errors="coerce")
    closing = pd.to_numeric(result["close"], errors="coerce")
    result["intraday_return"] = _finite_ratio(closing, opening) - 1.0
    result["price_difference"] = closing - opening
    return result
