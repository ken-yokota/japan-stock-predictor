"""Tests for the research feature sets, their lag, and the download cache."""

from __future__ import annotations

from datetime import date, timedelta

import numpy as np
import pandas as pd
import pytest

from models.base import ModelTrainingConfig
from models.training import recency_weights, train_ticker_model
from research import feature_sets, history
from research.dataset import (
    IndicatorPanel,
    build_indicator_frame,
    build_stock_frame,
)
from research.feature_sets import IndicatorSpec
from research.price_features import add_extended_price_features
from research.walk import WindowResult, require_complete_data
from scripts.run_feature_comparison import (
    Variant,
    _parse_half_lives,
    _sign_test,
    _variants,
    _verdict,
)


def _sessions(count: int, start: date = date(2026, 1, 5)) -> list[date]:
    """Return weekday-only dates so a synthetic series looks like a calendar."""

    days: list[date] = []
    cursor = start
    while len(days) < count:
        if cursor.weekday() < 5:
            days.append(cursor)
        cursor += timedelta(days=1)
    return days


def _bars(count: int, *, base: float = 100.0, step: float = 1.0) -> pd.DataFrame:
    dates = _sessions(count)
    closes = [base + step * index for index in range(count)]
    return pd.DataFrame(
        {
            "market_date": dates,
            # A distinct open/close relationship per row makes a leaked
            # same-session value obvious rather than coincidentally equal.
            "open": [value - 0.5 for value in closes],
            "high": [value + 1.0 for value in closes],
            "low": [value - 1.5 for value in closes],
            "close": closes,
            "volume": [1_000 + 10 * index for index in range(count)],
        }
    )


# --- extended price features -------------------------------------------------


def test_overnight_gap_is_the_open_against_the_previous_close() -> None:
    frame = add_extended_price_features(_bars(5))
    expected = frame["open"].iloc[3] / frame["close"].iloc[2] - 1.0
    assert frame["overnight_gap"].iloc[3] == pytest.approx(expected)
    assert pd.isna(frame["overnight_gap"].iloc[0])


def test_rsi_is_100_when_every_session_closed_higher() -> None:
    frame = add_extended_price_features(_bars(40))
    assert frame["rsi14"].iloc[-1] == pytest.approx(100.0)
    # Wilder's RSI needs a full period before it means anything.
    assert pd.isna(frame["rsi14"].iloc[5])


def test_atr_equals_the_range_when_every_session_has_the_same_range() -> None:
    frame = add_extended_price_features(_bars(40, step=0.0))
    # high - low is 2.5 on every row and the close never moves, so the true
    # range is 2.5 throughout and ATR must converge to it.
    assert frame["atr14_ratio"].iloc[-1] == pytest.approx(2.5 / 100.0)


def test_volume_features_are_absent_rather_than_zero_without_volume() -> None:
    frame = add_extended_price_features(_bars(30).drop(columns="volume"))
    assert frame["volume_change_1d"].isna().all()
    assert frame["volume_ratio_20d"].isna().all()


# --- lag safety --------------------------------------------------------------


@pytest.fixture
def offline_download(monkeypatch: pytest.MonkeyPatch) -> dict[str, pd.DataFrame]:
    """Serve every symbol from an in-memory table instead of the network."""

    table: dict[str, pd.DataFrame] = {}

    def fake_download(symbol: str, start: date, end: date, **_: object) -> pd.DataFrame:
        frame = table.get(symbol)
        if frame is None:
            return pd.DataFrame()
        window = frame.loc[
            (frame["market_date"] >= start) & (frame["market_date"] <= end)
        ]
        return window.reset_index(drop=True)

    monkeypatch.setattr("research.dataset.download_daily", fake_download)
    return table


def test_every_predictor_reads_only_sessions_before_the_predicted_one(
    offline_download: dict[str, pd.DataFrame],
) -> None:
    stock = _bars(60)
    offline_download["9101.T"] = stock
    for spec in feature_sets.FOCUSED.indicators:
        offline_download[spec.symbol] = _bars(60, base=50.0, step=0.25)

    indicators = build_indicator_frame(
        feature_sets.FOCUSED.indicators,
        stock["market_date"].iloc[0],
        date(2026, 12, 31),
    )
    built = build_stock_frame(
        "9101",
        "9101.T",
        stock["market_date"].iloc[0],
        date(2026, 12, 31),
        feature_set=feature_sets.FOCUSED,
        indicators=indicators,
    )
    frame, names = built.frame, built.feature_names

    unlagged = add_extended_price_features(stock)
    position = 50
    # The gap the model sees on row 50 is the gap that happened on row 49. If
    # the shift were dropped, this would equal row 50's own gap, which is not
    # knowable at 08:30 because the Open has not printed.
    assert frame["overnight_gap"].iloc[position] == pytest.approx(
        unlagged["open"].iloc[position - 1] / unlagged["close"].iloc[position - 2] - 1.0
    )
    assert frame["overnight_gap"].iloc[position] != pytest.approx(
        unlagged["open"].iloc[position] / unlagged["close"].iloc[position - 1] - 1.0
    )

    overseas = "spy_return_1d"
    assert overseas in names
    assert frame[overseas].iloc[position] == pytest.approx(
        indicators.frame.loc[
            indicators.frame["market_date"] == frame["market_date"].iloc[position - 1],
            overseas,
        ].iloc[0]
    )


def test_only_the_target_uses_the_predicted_session(
    offline_download: dict[str, pd.DataFrame],
) -> None:
    stock = _bars(40)
    offline_download["9101.T"] = stock
    built = build_stock_frame(
        "9101",
        "9101.T",
        stock["market_date"].iloc[0],
        date(2026, 12, 31),
        feature_set=feature_sets.BASELINE,
        indicators=IndicatorPanel(pd.DataFrame(columns=["market_date"]), [], []),
    )
    row = built.frame.iloc[20]
    assert row["intraday_return"] == pytest.approx(row["close"] / row["open"] - 1.0)


def test_a_ticker_without_an_adr_gets_no_adr_column(
    offline_download: dict[str, pd.DataFrame],
) -> None:
    offline_download["9101.T"] = _bars(40)
    built = build_stock_frame(
        "9101",
        "9101.T",
        date(2026, 1, 5),
        date(2026, 12, 31),
        feature_set=feature_sets.FOCUSED,
        indicators=IndicatorPanel(pd.DataFrame(columns=["market_date"]), [], []),
    )
    assert "adr_return_1d" not in built.feature_names


def test_a_ticker_with_an_adr_gets_a_lagged_adr_column(
    offline_download: dict[str, pd.DataFrame],
) -> None:
    offline_download["7203.T"] = _bars(40)
    offline_download["TM"] = _bars(40, base=200.0, step=2.0)
    built = build_stock_frame(
        "7203",
        "7203.T",
        date(2026, 1, 5),
        date(2026, 12, 31),
        feature_set=feature_sets.FOCUSED,
        indicators=IndicatorPanel(pd.DataFrame(columns=["market_date"]), [], []),
    )
    assert "adr_return_1d" in built.feature_names
    assert built.frame["adr_return_1d"].notna().any()
    assert built.missing == []


# --- feature-set definitions -------------------------------------------------


def test_baseline_stays_exactly_the_seven_series_it_has_always_used() -> None:
    # The comparison is only meaningful while the control group is fixed.
    assert [spec.symbol for spec in feature_sets.BASELINE.indicators] == [
        "SPY",
        "QQQ",
        "^VIX",
        "JPY=X",
        "CL=F",
        "HG=F",
        "GC=F",
    ]
    assert feature_sets.BASELINE.extra_price_features == ()
    assert feature_sets.BASELINE.adr_symbols == {}
    assert feature_sets.DEFAULT_FEATURE_SET == "baseline"


def test_yield_series_are_expressed_as_level_changes_not_percent_changes() -> None:
    yields = [
        spec for spec in feature_sets.EXTENDED.indicators if spec.key.endswith("_yield")
    ]
    assert yields
    assert all(spec.transform == "difference" for spec in yields)
    assert yields[0].column_names()[0].endswith("_change_1d")


def test_an_unknown_feature_set_lists_the_valid_names() -> None:
    with pytest.raises(SystemExit) as error:
        feature_sets.resolve("kitchen_sink")
    assert "baseline" in str(error.value)


def test_a_dead_symbol_shrinks_the_feature_set_instead_of_adding_zeros(
    offline_download: dict[str, pd.DataFrame],
) -> None:
    offline_download["SPY"] = _bars(30, base=400.0)
    panel = build_indicator_frame(
        [IndicatorSpec("spy", "SPY"), IndicatorSpec("gone", "DELISTED")],
        date(2026, 1, 5),
        date(2026, 12, 31),
    )
    assert panel.names == ["spy_return_1d", "spy_return_5d"]
    assert not any(name.startswith("gone") for name in panel.frame.columns)
    # The dead symbol must be reported, not just dropped: a silently smaller
    # feature set would invalidate any comparison made against it.
    assert panel.missing == ["DELISTED"]


# --- download cache ----------------------------------------------------------


def test_a_covered_range_is_served_from_cache_without_a_second_request(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[tuple[date, date]] = []

    def fake_fetch(symbol: str, start: date, end: date) -> pd.DataFrame:
        calls.append((start, end))
        return _bars(30)

    monkeypatch.setattr(history, "_fetch", fake_fetch)
    today = date(2026, 8, 9)
    first = history.download_daily(
        "SPY", date(2026, 1, 5), date(2026, 2, 5), cache_dir=tmp_path, today=today
    )
    second = history.download_daily(
        "SPY", date(2026, 1, 12), date(2026, 2, 2), cache_dir=tmp_path, today=today
    )
    assert len(calls) == 1
    assert not first.empty
    assert second["market_date"].min() >= date(2026, 1, 12)
    assert second["market_date"].max() <= date(2026, 2, 2)


def test_a_range_reaching_today_is_never_cached_because_the_bar_is_unfinished(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[str] = []

    def fake_fetch(symbol: str, start: date, end: date) -> pd.DataFrame:
        calls.append(symbol)
        return _bars(30)

    monkeypatch.setattr(history, "_fetch", fake_fetch)
    today = date(2026, 8, 9)
    for _ in range(2):
        history.download_daily(
            "SPY", date(2026, 1, 5), today, cache_dir=tmp_path, today=today
        )
    assert len(calls) == 2
    assert not list(tmp_path.glob("*.csv"))


# --- the adoption decision ---------------------------------------------------


def test_sign_test_ignores_the_predictions_both_sets_agreed_on() -> None:
    candidate = pd.Series([True, True, False, True, False])
    baseline = pd.Series([True, False, True, False, False])
    result = _sign_test(candidate, baseline)
    assert result["candidate_only_correct"] == 2
    assert result["baseline_only_correct"] == 1
    assert result["discordant_pairs"] == 3


def test_a_higher_accuracy_that_could_be_noise_is_not_adopted() -> None:
    assert _verdict(0.012, 0.33).startswith("不採用")
    assert _verdict(0.012, 0.01).startswith("採用候補")
    assert _verdict(-0.012, 0.01).startswith("不採用")


def test_a_throttled_run_stops_instead_of_reporting_a_smaller_feature_set() -> None:
    result = WindowResult(missing_series=["SOXX", "NIY=F"])
    with pytest.raises(SystemExit) as error:
        require_complete_data(result, feature_sets.FOCUSED, allow_missing=False)
    assert "SOXX" in str(error.value)
    require_complete_data(result, feature_sets.FOCUSED, allow_missing=True)


# --- weighting recent sessions more heavily ----------------------------------


def test_weights_halve_every_half_life_and_keep_the_total_unchanged() -> None:
    weights = recency_weights(10, 5)
    assert weights is not None
    # Newest row is the reference; five sessions back counts half as much.
    assert weights[-1] / weights[-6] == pytest.approx(2.0)
    assert weights[-1] / weights[0] == pytest.approx(2.0 ** (9 / 5))
    # Ridge's alpha is defined against the total weight. Holding the total at
    # the row count keeps a given alpha meaning the same amount of shrinkage,
    # so a half-life change cannot masquerade as a regularization change.
    assert float(weights.sum()) == pytest.approx(10.0)


def test_no_half_life_returns_no_weights_at_all() -> None:
    # Not a vector of ones: None keeps the unweighted path calling plain fit,
    # so today's production behaviour is bit-for-bit unchanged.
    assert recency_weights(10, None) is None


def test_a_non_positive_half_life_is_rejected() -> None:
    with pytest.raises(ValueError, match="recency_half_life_sessions"):
        ModelTrainingConfig(recency_half_life_sessions=0)


def test_only_recency_weighting_recovers_a_reversed_recent_regime() -> None:
    """A feature that flipped sign halfway through the window.

    Weighted equally, the old and recent halves cancel and the model learns
    nothing. This is exactly the case recency weighting exists for.
    """

    generator = np.random.default_rng(0)
    count = 200
    signal = generator.normal(size=count)
    target = np.where(
        np.arange(count) < count // 2, -0.02 * signal, 0.02 * signal
    ) + generator.normal(scale=0.001, size=count)
    frame = pd.DataFrame({"signal": signal, "unrelated": generator.normal(size=count)})

    def coefficient(half_life: int | None) -> float:
        model = train_ticker_model(
            "TEST",
            frame,
            target,
            feature_names=("signal", "unrelated"),
            config=ModelTrainingConfig(
                window_size=count,
                minimum_training_sessions=20,
                recency_half_life_sessions=half_life,
            ),
        )
        return model.regression_coefficients()["signal"]

    assert abs(coefficient(None)) < 0.002
    assert coefficient(30) > 0.01


# --- comparison variants -----------------------------------------------------


def test_a_variant_is_named_by_both_of_the_things_it_changes() -> None:
    assert Variant("baseline", None).key == "baseline"
    assert Variant("focused", 60).key == "focused@hl60"


def test_the_variant_matrix_crosses_every_set_with_every_weighting() -> None:
    # A 2x2 is what separates "more factors helped" from "recency helped".
    keys = [variant.key for variant in _variants(["baseline", "focused"], [None, 60])]
    assert keys == ["baseline", "baseline@hl60", "focused", "focused@hl60"]


def test_half_life_none_is_spelled_several_ways_and_defaults_to_unweighted() -> None:
    assert _parse_half_lives("none,60") == [None, 60]
    assert _parse_half_lives("") == [None]
    assert _parse_half_lives("flat") == [None]
