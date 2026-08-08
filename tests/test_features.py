from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import numpy as np
import pandas as pd
import pytest

from features import (
    PRICE_FEATURE_COLUMNS,
    FeatureLineage,
    PointInTimeFeatureSet,
    PointInTimeViolation,
    add_intraday_targets,
    build_price_features,
)


def test_build_price_features_is_ticker_isolated_and_chronological() -> None:
    dates = pd.date_range("2026-01-01", periods=25, freq="B")
    first = pd.DataFrame(
        {
            "ticker": "1111",
            "market_date": dates,
            "open": np.arange(100.0, 125.0),
            "high": np.arange(102.0, 127.0),
            "low": np.arange(99.0, 124.0),
            "close": np.arange(101.0, 126.0),
        }
    )
    second = first.assign(
        ticker="2222",
        open=lambda value: value["open"] * 2.0,
        high=lambda value: value["high"] * 2.0,
        low=lambda value: value["low"] * 2.0,
        close=lambda value: value["close"] * 2.0,
    )
    raw = pd.concat([first, second], ignore_index=True).sample(frac=1.0, random_state=7)

    result = build_price_features(raw)
    ticker = result.loc[result["ticker"] == "1111"].sort_values("market_date")

    assert all(column in result for column in PRICE_FEATURE_COLUMNS)
    assert ticker.iloc[0]["return_1d"] != ticker.iloc[0]["return_1d"]  # NaN
    assert ticker.iloc[2]["return_2d"] == pytest.approx(103.0 / 101.0 - 1.0)
    assert ticker.iloc[5]["return_5d"] == pytest.approx(106.0 / 101.0 - 1.0)
    assert ticker.iloc[20]["return_20d"] == pytest.approx(121.0 / 101.0 - 1.0)
    assert ticker.iloc[1]["log_return_1d"] == pytest.approx(np.log(102.0 / 101.0))
    assert ticker.iloc[4]["volatility_5d"] != ticker.iloc[4]["volatility_5d"]
    assert np.isfinite(ticker.iloc[5]["volatility_5d"])
    assert ticker.iloc[-1]["open_close_return"] == pytest.approx(125.0 / 124.0 - 1)
    assert ticker.iloc[-1]["high_low_range"] == pytest.approx(126.0 / 123.0 - 1)
    assert np.isfinite(ticker.iloc[-1]["ma20_deviation"])
    assert list(result.index) == list(raw.index)


def test_feature_builder_and_targets_handle_zero_prices_without_infinity() -> None:
    raw = pd.DataFrame(
        {
            "ticker": ["x", "x"],
            "market_date": [date(2026, 1, 1), date(2026, 1, 2)],
            "open": [0.0, 10.0],
            "high": [1.0, 11.0],
            "low": [0.0, 9.0],
            "close": [0.0, 10.5],
        }
    )
    featured = build_price_features(raw)
    targeted = add_intraday_targets(raw)

    values = featured.loc[:, PRICE_FEATURE_COLUMNS].to_numpy(dtype=float)
    assert not np.isinf(values).any()
    assert np.isnan(targeted.iloc[0]["intraday_return"])
    assert targeted.iloc[1]["price_difference"] == pytest.approx(0.5)


def test_point_in_time_lineage_rejects_future_sources() -> None:
    cutoff = datetime(2026, 8, 10, 8, 30, tzinfo=UTC)
    safe = FeatureLineage(
        feature_name="spy_return_1d",
        source_symbol="SPY",
        source_market_date=date(2026, 8, 7),
        available_timestamp=cutoff - timedelta(minutes=1),
        prediction_timestamp=cutoff,
    )
    feature_set = PointInTimeFeatureSet(
        values={"spy_return_1d": 0.01},
        lineage=(safe,),
        prediction_timestamp=cutoff,
    )
    assert feature_set.values["spy_return_1d"] == 0.01

    with pytest.raises(PointInTimeViolation):
        FeatureLineage(
            feature_name="future",
            source_symbol="SPY",
            source_market_date=date(2026, 8, 10),
            available_timestamp=cutoff + timedelta(seconds=1),
            prediction_timestamp=cutoff,
        )


def test_point_in_time_feature_set_requires_lineage_for_every_value() -> None:
    cutoff = datetime(2026, 8, 10, 8, 30, tzinfo=UTC)
    item = FeatureLineage(
        feature_name="known",
        source_symbol="SPY",
        source_market_date=date(2026, 8, 7),
        available_timestamp=cutoff,
        prediction_timestamp=cutoff,
    )
    with pytest.raises(ValueError, match="no lineage"):
        PointInTimeFeatureSet(
            values={"known": 1.0, "unknown": 2.0},
            lineage=(item,),
            prediction_timestamp=cutoff,
        )
