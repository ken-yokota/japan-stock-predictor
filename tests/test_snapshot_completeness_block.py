"""The audit has to reach the operator without anyone running a command.

It is published inside the snapshot JSON, which regenerates on every morning
run and is readable from a plain URL, so the question "did today have the data
it needed" is answered by opening a link rather than by opening a terminal.
"""

from __future__ import annotations

from dashboard.types import QueryResult
from scripts.export_dashboard_snapshot import _completeness_block

RECORDED_CLEAN = {
    "missing_required_indicators": [],
    "missing_optional_indicators": [],
    "indicator_coverage": 1.0,
}
DEGRADED = {
    "missing_required_indicators": ["usdjpy", "eurjpy"],
    "missing_optional_indicators": [],
    "indicator_coverage": 0.9,
}
LEGACY = {"feature_names": ["a"]}


def _rows(*pairs: tuple[str, dict[str, object]]) -> QueryResult:
    return QueryResult.from_rows(
        tuple({"ticker": ticker, "details": details} for ticker, details in pairs)
    )


def test_a_clean_morning_publishes_no_misses() -> None:
    block = _completeness_block(_rows(("9101", RECORDED_CLEAN)), ())
    assert block["data_status"] == "NORMAL"
    assert block["degraded"] == 0
    assert block["missing_required_ranking"] == []


def test_a_degraded_morning_publishes_what_was_missing() -> None:
    block = _completeness_block(
        _rows(("9101", DEGRADED), ("9107", DEGRADED)),
        ({"ticker": "9101", "signal": "BUY", "feature_coverage": 1.0},),
    )
    assert block["data_status"] == "WARNING"
    assert block["degraded"] == 2
    assert block["degraded_buy"] == 1
    assert block["degraded_buy_tickers"] == ["9101"]
    assert {"indicator": "usdjpy", "stocks": 2} in block["missing_required_ranking"]


def test_a_legacy_morning_is_not_published_as_clean() -> None:
    block = _completeness_block(_rows(("9101", LEGACY)), ())
    assert block["legacy_unknown"] == 1
    assert block["clean"] == 0
    assert block["data_status"] == "UNKNOWN"


def test_the_watched_series_are_always_listed() -> None:
    block = _completeness_block(_rows(("9101", DEGRADED)), ())
    watched = {item["indicator"]: item["stocks"] for item in block["watched"]}
    assert watched["usdjpy"] == 1
    assert watched["kre"] == 0


def test_an_unavailable_read_says_so_rather_than_inventing_a_clean_day() -> None:
    block = _completeness_block(QueryResult.unavailable(), ())
    assert block["stocks"] == []
    assert "data_status" not in block


def test_no_price_or_value_reaches_the_published_block() -> None:
    block = _completeness_block(
        _rows(("9101", DEGRADED)),
        ({"ticker": "9101", "signal": "BUY", "feature_coverage": 1.0},),
    )
    keys = set(block["stocks"][0])
    assert keys == {
        "ticker",
        "status",
        "indicator_coverage",
        "missing_required",
        "missing_optional",
        "signal",
    }
