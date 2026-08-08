"""Point-in-time-safe features derived from official Treasury observations.

The ``1d``, ``3d`` and ``5d`` feature suffixes mean one, three and five prior
U.S. Treasury *observation rows*.  They are not calendar-day differences: a
Friday-to-Monday move is a 1d observation change when no rate was published on
the intervening weekend.
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterable, Mapping, Sequence
from datetime import date
from decimal import Decimal

from data.providers.treasury import TREASURY_TENORS
from data.schemas import (
    AvailabilityMethod,
    DataInterval,
    DataQuality,
    MarketBar,
)

TREASURY_SPREAD_SYMBOL = "us_10y_minus_2y_spread"
TREASURY_CHANGE_LAGS: tuple[int, ...] = (1, 3, 5)
TREASURY_CHANGE_SYMBOLS: dict[tuple[str, int], str] = {
    (tenor, lag): f"us_{tenor.lower()}_yield_change_{lag}d"
    for tenor in TREASURY_TENORS
    for lag in TREASURY_CHANGE_LAGS
}

OBSERVATION_LAG_FLAG = "treasury_observation_day_lag_not_calendar_day"
_DERIVED_PROVIDER = "internal"
_QUALITY_RANK = {
    DataQuality.MISSING: 0,
    DataQuality.DELAYED: 1,
    DataQuality.FREE_UNVERIFIED: 2,
    DataQuality.EOD_CONFIRMED: 3,
    DataQuality.OFFICIAL: 4,
}


def _availability_method(inputs: Sequence[MarketBar]) -> AvailabilityMethod:
    latest = max(row.available_timestamp for row in inputs)
    candidates = {
        row.availability_method for row in inputs if row.available_timestamp == latest
    }
    # Actual observation evidence is the conservative choice when a tie mixes
    # schedule estimates with a first-observed timestamp.
    if AvailabilityMethod.FIRST_OBSERVED in candidates:
        return AvailabilityMethod.FIRST_OBSERVED
    if len(candidates) == 1:
        return next(iter(candidates))
    if AvailabilityMethod.PROVIDER_TIMESTAMP in candidates:
        return AvailabilityMethod.PROVIDER_TIMESTAMP
    if AvailabilityMethod.PUBLISHED_SCHEDULE in candidates:
        return AvailabilityMethod.PUBLISHED_SCHEDULE
    return AvailabilityMethod.PROVIDER_SLA_ESTIMATE


def _data_quality(inputs: Sequence[MarketBar]) -> DataQuality:
    return min(inputs, key=lambda row: _QUALITY_RANK[row.data_quality]).data_quality


def _derived_hash(
    canonical_symbol: str,
    value: Decimal,
    inputs: Sequence[MarketBar],
) -> str:
    source_ids = sorted(
        row.raw_hash
        or (
            f"{row.canonical_symbol}:{row.market_date.isoformat()}:"
            f"{row.close}:{row.available_timestamp.isoformat()}"
        )
        for row in inputs
    )
    payload = "|".join((canonical_symbol, str(value), *source_ids))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _derived_bar(
    *,
    canonical_symbol: str,
    market_date: date,
    value: Decimal,
    inputs: Sequence[MarketBar],
    feature_flags: tuple[str, ...],
) -> MarketBar:
    latest = max(inputs, key=lambda row: (row.market_date, row.timestamp))
    source_flags = sorted({flag for row in inputs for flag in row.quality_flags})
    currencies = {row.currency for row in inputs}
    currency = next(iter(currencies)) if len(currencies) == 1 else None
    return MarketBar(
        canonical_symbol=canonical_symbol,
        provider_symbol=f"INTERNAL:{canonical_symbol.upper()}",
        provider=_DERIVED_PROVIDER,
        market=latest.market,
        market_timezone=latest.market_timezone,
        market_date=market_date,
        timestamp=max(row.timestamp for row in inputs),
        source_timestamp=None,
        available_timestamp=max(row.available_timestamp for row in inputs),
        first_observed_at=max(row.first_observed_at for row in inputs),
        retrieved_at=max(row.retrieved_at for row in inputs),
        interval=DataInterval.EOD,
        availability_method=_availability_method(inputs),
        data_quality=_data_quality(inputs),
        is_realtime=False,
        is_delayed=any(row.is_delayed for row in inputs),
        close=value,
        currency=currency,
        raw_hash=_derived_hash(canonical_symbol, value, inputs),
        quality_flags=tuple(
            (
                *source_flags,
                "derived_from_treasury_yields",
                *feature_flags,
            )
        ),
    )


def build_treasury_features(
    bars: Iterable[MarketBar],
    *,
    tenor_symbols: Mapping[str, str] | None = None,
) -> list[MarketBar]:
    """Build 10Y-2Y spread and 1/3/5-observation changes.

    A spread is emitted only when both tenors exist for the same market date.
    Each change is ``current rate - rate N Treasury observations earlier``;
    weekends, holidays and missing publication dates do not count as rows.  A
    derived bar becomes available at the maximum availability of its inputs.
    """

    configured_tenors = TREASURY_TENORS if tenor_symbols is None else tenor_symbols
    requested = {tenor.upper(): symbol for tenor, symbol in configured_tenors.items()}
    symbol_to_tenor = {symbol: tenor for tenor, symbol in requested.items()}
    indexed: dict[str, dict[date, MarketBar]] = {tenor: {} for tenor in requested}
    for row in bars:
        tenor = symbol_to_tenor.get(row.canonical_symbol)
        if tenor is None:
            continue
        previous = indexed[tenor].get(row.market_date)
        if previous is not None:
            raise ValueError(
                "duplicate Treasury observations for "
                f"{row.canonical_symbol} on {row.market_date.isoformat()}"
            )
        indexed[tenor][row.market_date] = row

    derived: list[MarketBar] = []
    two_year = indexed.get("2Y", {})
    ten_year = indexed.get("10Y", {})
    for market_date in sorted(two_year.keys() & ten_year.keys()):
        inputs = (ten_year[market_date], two_year[market_date])
        derived.append(
            _derived_bar(
                canonical_symbol=TREASURY_SPREAD_SYMBOL,
                market_date=market_date,
                value=inputs[0].close - inputs[1].close,
                inputs=inputs,
                feature_flags=("ten_year_minus_two_year_spread",),
            )
        )

    for tenor in TREASURY_TENORS:
        observations = sorted(
            indexed.get(tenor, {}).values(),
            key=lambda row: row.market_date,
        )
        for lag in TREASURY_CHANGE_LAGS:
            canonical_symbol = TREASURY_CHANGE_SYMBOLS[(tenor, lag)]
            for index in range(lag, len(observations)):
                current = observations[index]
                prior = observations[index - lag]
                derived.append(
                    _derived_bar(
                        canonical_symbol=canonical_symbol,
                        market_date=current.market_date,
                        value=current.close - prior.close,
                        inputs=(current, prior),
                        feature_flags=(
                            OBSERVATION_LAG_FLAG,
                            f"treasury_observation_lag_{lag}d",
                        ),
                    )
                )

    return sorted(
        derived,
        key=lambda row: (row.market_date, row.canonical_symbol),
    )


def derive_treasury_features(
    bars: Iterable[MarketBar],
    *,
    tenor_symbols: Mapping[str, str] | None = None,
) -> list[MarketBar]:
    """Compatibility alias with an action-oriented name."""

    return build_treasury_features(bars, tenor_symbols=tenor_symbols)


build_treasury_feature_bars = build_treasury_features
