"""The one definition of completeness, held to the cases that broke before.

The dashboard, the mail and the audit read this module rather than each
deciding for themselves, so these assertions are what keeps the three from
disagreeing about the same morning.
"""

from __future__ import annotations

import pytest

from dashboard.completeness import (
    CLEAN,
    CLEAN_BUY,
    DEGRADED,
    DEGRADED_BUY,
    LEGACY_UNKNOWN,
    NON_BUY,
    NORMAL,
    UNKNOWN,
    WARNING,
    StockCompleteness,
    stock_from_details,
    summarise,
)

RECORDED_CLEAN = {
    "missing_required_indicators": [],
    "missing_optional_indicators": [],
    "indicator_coverage": 1.0,
}
LEGACY = {"feature_names": ["a"], "feature_coverage": 1.0}


def _degraded(*missing: str, coverage: float = 0.9) -> dict[str, object]:
    return {
        "missing_required_indicators": list(missing),
        "missing_optional_indicators": [],
        "indicator_coverage": coverage,
    }


def _stock(
    ticker: str, details: dict[str, object], signal: str = ""
) -> StockCompleteness:
    return stock_from_details(ticker, details, feature_coverage=1.0, signal=signal)


# --- per stock ------------------------------------------------------------


def test_a_recorded_complete_run_is_clean() -> None:
    assert _stock("9101", RECORDED_CLEAN).status == CLEAN


def test_a_recorded_miss_is_degraded() -> None:
    stock = _stock("9101", _degraded("usdjpy"))
    assert stock.status == DEGRADED
    assert stock.missing_required == ("usdjpy",)
    assert stock.label == "⚠ DEGRADED"


def test_a_feature_set_from_before_the_fields_existed_is_unknown() -> None:
    stock = _stock("9101", LEGACY)
    assert stock.status == LEGACY_UNKNOWN
    assert stock.label == "UNKNOWN"
    assert stock.missing_required == ()


def test_an_optional_miss_alone_stays_clean() -> None:
    details = {
        "missing_required_indicators": [],
        "missing_optional_indicators": ["iron_ore"],
        "indicator_coverage": 0.97,
    }
    stock = _stock("9101", details)
    assert stock.status == CLEAN
    assert stock.missing_optional == ("iron_ore",)


def test_full_feature_coverage_does_not_hide_a_missing_indicator() -> None:
    """The exact failure that went unnoticed for three days."""

    stock = _stock("9101", _degraded("usdjpy", coverage=0.95))
    assert stock.feature_coverage == pytest.approx(1.0)
    assert stock.indicator_coverage == pytest.approx(0.95)
    assert stock.hidden_by_feature_coverage is True


# --- buys -----------------------------------------------------------------


def test_a_buy_on_complete_data_is_a_clean_buy() -> None:
    assert _stock("9101", RECORDED_CLEAN, "BUY").buy_class == CLEAN_BUY


def test_a_buy_with_a_missing_required_indicator_is_degraded() -> None:
    assert _stock("9101", _degraded("usdjpy"), "BUY").buy_class == DEGRADED_BUY


def test_a_non_buy_is_neither() -> None:
    assert _stock("9101", RECORDED_CLEAN, "HOLD").buy_class == NON_BUY


# --- the day --------------------------------------------------------------


def test_a_fully_clean_day_is_normal() -> None:
    summary = summarise([_stock(str(9100 + n), RECORDED_CLEAN) for n in range(22)])
    assert summary.stock_count == 22
    assert summary.clean_count == 22
    assert summary.data_status == NORMAL


def test_one_degraded_stock_puts_the_day_in_warning() -> None:
    stocks = [_stock(str(9100 + n), RECORDED_CLEAN) for n in range(21)]
    stocks.append(_stock("9999", _degraded("usdjpy")))
    summary = summarise(stocks)
    assert summary.degraded_count == 1
    assert summary.data_status == WARNING


def test_a_legacy_only_day_is_unknown_not_normal() -> None:
    summary = summarise([_stock(str(9100 + n), LEGACY) for n in range(22)])
    assert summary.unknown_count == 22
    assert summary.clean_count == 0
    assert summary.data_status == UNKNOWN


def test_every_stock_degraded_is_reported_as_such() -> None:
    """USDJPY and EURJPY are required by all 22, so this is the realistic case."""

    summary = summarise(
        [_stock(str(9100 + n), _degraded("usdjpy", "eurjpy")) for n in range(22)]
    )
    assert summary.degraded_count == 22
    assert summary.missing_required_ranking[0] == ("usdjpy", 22)
    assert dict(summary.missing_required_ranking)["eurjpy"] == 22
    assert summary.data_status == WARNING


def test_the_ranking_orders_by_affected_stocks() -> None:
    stocks = [
        _stock("9101", _degraded("usdjpy", "audjpy")),
        _stock("9104", _degraded("usdjpy")),
        _stock("8306", _degraded("usdjpy")),
    ]
    summary = summarise(stocks)
    assert summary.missing_required_ranking[0] == ("usdjpy", 3)
    assert ("audjpy", 1) in summary.missing_required_ranking


def test_watched_series_report_zero_rather_than_disappearing() -> None:
    summary = summarise([_stock("9101", _degraded("usdjpy"))])
    watched = dict(summary.watched())
    assert watched["usdjpy"] == 1
    assert watched["kre"] == 0


def test_a_day_with_no_buys_is_still_a_measured_day() -> None:
    summary = summarise([_stock(str(9100 + n), RECORDED_CLEAN) for n in range(22)])
    assert summary.buy_count == 0
    assert summary.data_status == NORMAL


def test_buy_counts_split_clean_and_degraded() -> None:
    stocks = [
        _stock("9107", RECORDED_CLEAN, "BUY"),
        _stock("9101", _degraded("usdjpy"), "BUY"),
        _stock("7267", RECORDED_CLEAN, "HOLD"),
    ]
    summary = summarise(stocks)
    assert summary.buy_count == 2
    assert summary.clean_buy_count == 1
    assert summary.degraded_buy_count == 1
    assert [item.ticker for item in summary.degraded_buys] == ["9101"]


def test_hidden_by_feature_coverage_is_listed_for_the_day() -> None:
    stocks = [
        _stock("9101", _degraded("usdjpy", coverage=0.95)),
        _stock("9107", RECORDED_CLEAN),
    ]
    assert summarise(stocks).hidden_by_feature_coverage == ("9101",)
