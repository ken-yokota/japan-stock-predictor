"""Assemble one ticker's lagged predictor matrix for a named feature set.

The lag is the whole safety argument, so it lives in one place. Three families
of column arrive here and all three are shifted one JPX session before a model
sees them:

* the ticker's own price features, several of which (``open_close_return``,
  ``overnight_gap``) are computed from the same session being predicted;
* overseas indicator moves, aligned onto JPX dates and carried forward;
* the ticker's own ADR, which trades in New York overnight.

After the shift, a prediction for date ``t`` reads nothing stamped later than
the close of ``t - 1``. The overseas close of ``t - 1`` lands at roughly 05:00
JST on ``t``, so this is available at 08:30 without being same-day information.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

from features.builder import PRICE_FEATURE_COLUMNS, build_price_features
from research.feature_sets import FeatureSet, IndicatorSpec
from research.history import DEFAULT_CACHE_DIR, download_daily
from research.price_features import add_extended_price_features


@dataclass(frozen=True, slots=True)
class IndicatorPanel:
    """Overseas indicator columns, plus the symbols that returned nothing."""

    frame: pd.DataFrame
    names: list[str]
    missing: list[str]


@dataclass(frozen=True, slots=True)
class StockFrame:
    """One ticker's modelling rows, its feature names, and any dead series."""

    frame: pd.DataFrame
    feature_names: tuple[str, ...]
    missing: list[str]

    @property
    def is_empty(self) -> bool:
        return bool(self.frame.empty)


def _series_features(frame: pd.DataFrame, spec: IndicatorSpec) -> pd.DataFrame:
    """Return one indicator's per-date feature columns."""

    series = frame.loc[:, ["market_date", "close"]].copy()
    series["close"] = pd.to_numeric(series["close"], errors="coerce")
    series = series.sort_values("market_date")
    for window, name in zip(spec.windows, spec.column_names(), strict=True):
        if spec.transform == "return":
            series[name] = series["close"].pct_change(periods=window, fill_method=None)
        else:
            series[name] = series["close"].diff(periods=window)
    columns = ["market_date", *spec.column_names()]
    return series.loc[:, columns].replace([np.inf, -np.inf], np.nan)


def build_indicator_frame(
    specs: Iterable[IndicatorSpec],
    start: date,
    end: date,
    *,
    cache_dir: Path | None = DEFAULT_CACHE_DIR,
) -> IndicatorPanel:
    """Return one row per calendar date of overseas indicator moves.

    A series that returns nothing is dropped rather than filled, and its symbol
    is reported in ``missing``. Dropping keeps a dead symbol from contributing a
    column of zeros; reporting keeps a throttled download from quietly turning
    one feature set into a smaller one, which would make any comparison against
    it a comparison of the wrong thing.
    """

    merged: pd.DataFrame | None = None
    missing: list[str] = []
    for spec in specs:
        raw = download_daily(spec.symbol, start, end, cache_dir=cache_dir)
        if raw.empty:
            missing.append(spec.symbol)
            continue
        part = _series_features(raw, spec)
        merged = (
            part
            if merged is None
            else merged.merge(part, on="market_date", how="outer")
        )
    if merged is None:
        return IndicatorPanel(pd.DataFrame(columns=["market_date"]), [], missing)
    merged = merged.sort_values("market_date").reset_index(drop=True)
    names = [name for name in merged.columns if name != "market_date"]
    return IndicatorPanel(merged, names, missing)


def _attach_lagged(
    stock: pd.DataFrame, extra: pd.DataFrame, names: list[str]
) -> pd.DataFrame:
    if not names:
        return stock
    merged = stock.merge(extra, on="market_date", how="left")
    merged = merged.sort_values("market_date").reset_index(drop=True)
    for name in names:
        # Carry the last known overseas value forward, then lag one JPX session
        # so day t can never read a value stamped on day t.
        merged[name] = merged[name].ffill().shift(1)
    return merged


def build_stock_frame(
    ticker: str,
    symbol: str,
    start: date,
    end: date,
    *,
    feature_set: FeatureSet,
    indicators: IndicatorPanel,
    cache_dir: Path | None = DEFAULT_CACHE_DIR,
) -> StockFrame:
    """Return the ticker's modelling frame and the feature names to fit on.

    An empty result means the provider returned no history for the ticker; the
    caller decides whether that is fatal.
    """

    raw = download_daily(symbol, start, end, cache_dir=cache_dir)
    if raw.empty:
        return StockFrame(pd.DataFrame(), (), [symbol])

    raw = raw.copy()
    raw["ticker"] = ticker
    featured = build_price_features(
        raw, ticker_column="ticker", date_column="market_date"
    )
    price_columns = list(PRICE_FEATURE_COLUMNS)
    if feature_set.extra_price_features:
        featured = add_extended_price_features(featured)
        price_columns.extend(feature_set.extra_price_features)
    featured = featured.sort_values("market_date").reset_index(drop=True)

    # The target is same-session; every predictor must come from earlier rows.
    lagged = featured.copy()
    for column in price_columns:
        lagged[column] = featured[column].shift(1)
    lagged["prev_close"] = pd.to_numeric(featured["close"], errors="coerce").shift(1)
    opening = pd.to_numeric(featured["open"], errors="coerce")
    closing = pd.to_numeric(featured["close"], errors="coerce")
    lagged["intraday_return"] = (closing / opening.where(opening > 0.0)) - 1.0

    # Drop the indicator columns this ticker is not scoped for, so a series can
    # be given to one stock without reaching the other twenty-one.
    scoped = {
        spec.key for spec in feature_set.indicators if not spec.covers(ticker)
    }
    indicator_names = [
        name
        for name in indicators.names
        if not any(name.startswith(f"{key}_") for key in scoped)
    ]
    frame = _attach_lagged(lagged, indicators.frame, indicator_names)

    adr_names: list[str] = []
    missing: list[str] = []
    adr_symbol = feature_set.adr_symbols.get(ticker)
    if adr_symbol is not None:
        adr = build_indicator_frame(
            [IndicatorSpec("adr", adr_symbol)], start, end, cache_dir=cache_dir
        )
        adr_names = adr.names
        missing = adr.missing
        frame = _attach_lagged(frame, adr.frame, adr_names)

    names = tuple([*price_columns, *indicator_names, *adr_names])
    return StockFrame(frame, names, missing)
