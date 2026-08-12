"""The audit's terminal view, and its agreement with the shared summary.

The point of the shared layer is that three surfaces cannot disagree about the
same morning, so this asserts the rendered text carries exactly the counts the
summary computed rather than recomputing them on its way to the screen.
"""

from __future__ import annotations

from datetime import date

from dashboard.completeness import stock_from_details, summarise
from scripts.audit_morning_completeness import render

FOR_DATE = date(2026, 8, 13)
RECORDED_CLEAN = {
    "missing_required_indicators": [],
    "missing_optional_indicators": [],
    "indicator_coverage": 1.0,
}
LEGACY = {"feature_names": ["a"], "feature_coverage": 1.0}


def _degraded(*missing: str) -> dict[str, object]:
    return {
        "missing_required_indicators": list(missing),
        "missing_optional_indicators": [],
        "indicator_coverage": 0.9,
    }


def _stock(ticker, details, signal=""):  # type: ignore[no-untyped-def]
    return stock_from_details(ticker, details, feature_coverage=1.0, signal=signal)


def test_the_report_names_every_stock_and_its_state() -> None:
    summary = summarise(
        [
            _stock("9107", RECORDED_CLEAN, "BUY"),
            _stock("9101", _degraded("usdjpy"), "BUY"),
            _stock("8306", LEGACY),
        ]
    )
    text = render(summary, FOR_DATE)
    assert "2026-08-13" in text
    for ticker in ("9107", "9101", "8306"):
        assert ticker in text
    assert "LEGACY_UNKNOWN" in text
    assert "usdjpy" in text


def test_the_report_counts_match_the_summary() -> None:
    summary = summarise(
        [
            _stock("9107", RECORDED_CLEAN, "BUY"),
            _stock("9101", _degraded("usdjpy"), "BUY"),
            _stock("8306", LEGACY),
        ]
    )
    text = render(summary, FOR_DATE)
    assert f"CLEAN_BUY                   : {summary.clean_buy_count}" in text
    assert f"DEGRADED_BUY                : {summary.degraded_buy_count}" in text
    assert f"LEGACY_UNKNOWN (not recorded) : {summary.unknown_count}" in text
    assert f"data status                   : {summary.data_status}" in text


def test_a_watched_series_with_no_misses_is_still_listed() -> None:
    """Silence about a series is not the same as it being fine."""

    text = render(summarise([_stock("9101", _degraded("usdjpy"), "BUY")]), FOR_DATE)
    assert "usdjpy       missing for   1 stocks" in text
    assert "kre          missing for   0 stocks" in text


def test_all_22_degraded_reads_as_such() -> None:
    summary = summarise(
        [_stock(str(9100 + n), _degraded("usdjpy", "eurjpy")) for n in range(22)]
    )
    text = render(summary, FOR_DATE)
    assert "DEGRADED (missing required)   : 22" in text
    assert "usdjpy        22 stocks" in text
