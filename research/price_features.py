"""Additional own-price features for the research feature-set comparison.

``features/builder.py`` is production code and is left untouched: the morning
pipeline must keep producing exactly the columns it produces today. Anything
new is added here first, measured, and only promoted after it earns it.

Every column is computed from one ticker's own OHLCV history and, like the
production price features, is lagged one session by the caller before it
reaches a model. ``overnight_gap`` uses the same row's Open, which is not known
at 08:30 -- it is safe only because of that lag, and only the lag.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

EXTRA_PRICE_FEATURE_COLUMNS: tuple[str, ...] = (
    "overnight_gap",
    "volume_change_1d",
    "volume_ratio_20d",
    "atr14_ratio",
    "rsi14",
    "ma5_deviation",
    "ma60_deviation",
)

_WILDER_PERIOD = 14


def _finite_ratio(numerator: pd.Series, denominator: pd.Series) -> pd.Series:
    ratio = numerator / denominator.where(denominator != 0.0)
    return ratio.replace([np.inf, -np.inf], np.nan)


def _wilder_average(values: pd.Series, period: int) -> pd.Series:
    return values.ewm(alpha=1.0 / period, adjust=False, min_periods=period).mean()


def add_extended_price_features(frame: pd.DataFrame) -> pd.DataFrame:
    """Add gap, volume, ATR, RSI, and short/long moving-average deviations.

    ``frame`` must hold exactly one ticker, sorted ascending by session. A
    missing ``volume`` column produces all-``NaN`` volume features rather than
    an error, because index and futures series often carry no volume.
    """

    missing = [name for name in ("open", "high", "low", "close") if name not in frame]
    if missing:
        raise ValueError(f"missing required columns: {missing}")

    result = frame.copy(deep=True)
    opening = pd.to_numeric(result["open"], errors="coerce")
    high = pd.to_numeric(result["high"], errors="coerce")
    low = pd.to_numeric(result["low"], errors="coerce")
    close = pd.to_numeric(result["close"], errors="coerce")
    previous_close = close.shift(1)

    result["overnight_gap"] = _finite_ratio(opening, previous_close) - 1.0

    if "volume" in result.columns:
        volume = pd.to_numeric(result["volume"], errors="coerce")
        volume = volume.where(volume > 0.0)
        result["volume_change_1d"] = volume.pct_change(fill_method=None).replace(
            [np.inf, -np.inf], np.nan
        )
        result["volume_ratio_20d"] = (
            _finite_ratio(volume, volume.rolling(20, min_periods=20).mean()) - 1.0
        )
    else:
        empty = pd.Series(np.nan, index=result.index, dtype="float64")
        result["volume_change_1d"] = empty
        result["volume_ratio_20d"] = empty

    true_range = pd.concat(
        [high - low, (high - previous_close).abs(), (low - previous_close).abs()],
        axis=1,
    ).max(axis=1)
    result["atr14_ratio"] = _finite_ratio(
        _wilder_average(true_range, _WILDER_PERIOD), close
    )

    change = close.diff()
    average_gain = _wilder_average(change.clip(lower=0.0), _WILDER_PERIOD)
    average_loss = _wilder_average((-change).clip(lower=0.0), _WILDER_PERIOD)
    # A window with no down sessions has zero average loss; RSI is 100 there,
    # not undefined, so the zero denominator is resolved explicitly.
    relative_strength = _finite_ratio(average_gain, average_loss)
    rsi = 100.0 - 100.0 / (1.0 + relative_strength)
    result["rsi14"] = rsi.where(
        ~((average_loss == 0.0) & average_gain.notna()), 100.0
    ).where(average_gain.notna())

    result["ma5_deviation"] = (
        _finite_ratio(close, close.rolling(5, min_periods=5).mean()) - 1.0
    )
    result["ma60_deviation"] = (
        _finite_ratio(close, close.rolling(60, min_periods=60).mean()) - 1.0
    )
    return result
