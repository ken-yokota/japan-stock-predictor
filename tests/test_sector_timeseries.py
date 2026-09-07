"""Sector-level predicted-vs-realised series, and the alert band above it.

Two things are worth pinning here. The realised number must be open-to-close,
because that is what the model predicts -- measuring it against the previous
close would score the model on the overnight gap it never forecast. And the
alert band must keep errors inline while folding advisories away: the whole
point of collapsing it is that the red ones stop being buried, so a change
that hides an error would defeat the reason the change was made.
"""

from __future__ import annotations

from typing import Any

import pytest

from dashboard.presenters import Alert, AlertLevel
from dashboard.sector_history import sector_timeseries


def _row(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "ticker": "7203",
        "prediction_date": "2026-09-01",
        "predicted_return": 0.01,
        "probability_up": 0.6,
        "actual_open": 100.0,
        "actual_close": 102.0,
        "outcome_status": "FINAL",
        "result_version": 1,
    }
    base.update(overrides)
    return base


def test_realised_return_is_open_to_close() -> None:
    rows = sector_timeseries([_row(actual_open=100.0, actual_close=102.0)])
    assert len(rows) == 1
    assert rows[0].sector == "自動車"
    assert rows[0].actual_mean == pytest.approx(0.02)
    assert rows[0].predicted_mean == pytest.approx(0.01)
    assert rows[0].count == 1


def test_sectors_and_days_are_grouped_separately() -> None:
    rows = sector_timeseries(
        [
            _row(ticker="7203", actual_close=102.0),
            _row(ticker="7267", actual_close=104.0),
            _row(ticker="8306", actual_close=110.0),
            _row(ticker="7203", prediction_date="2026-09-02", actual_close=99.0),
        ]
    )
    keyed = {(day.date, day.sector): day for day in rows}
    assert keyed[("2026-09-01", "自動車")].actual_mean == pytest.approx(0.03)
    assert keyed[("2026-09-01", "自動車")].count == 2
    assert keyed[("2026-09-01", "金融")].actual_mean == pytest.approx(0.10)
    assert keyed[("2026-09-02", "自動車")].actual_mean == pytest.approx(-0.01)


def test_rows_are_ordered_by_date_then_sector() -> None:
    rows = sector_timeseries(
        [
            _row(ticker="8306", prediction_date="2026-09-02"),
            _row(ticker="7203", prediction_date="2026-09-01"),
            _row(ticker="9101", prediction_date="2026-09-01"),
        ]
    )
    assert [(day.date, day.sector) for day in rows] == [
        ("2026-09-01", "海運"),
        ("2026-09-01", "自動車"),
        ("2026-09-02", "金融"),
    ]


def test_unusable_rows_are_dropped_rather_than_counted_as_zero() -> None:
    # A missing or non-positive price is not a flat day. Averaging it in as
    # zero would drag a sector's realised line toward the axis and make the
    # model look better or worse than the sessions that actually settled.
    assert sector_timeseries([_row(actual_open=None)]) == []
    assert sector_timeseries([_row(actual_close=None)]) == []
    assert sector_timeseries([_row(actual_open=0.0)]) == []
    assert sector_timeseries([_row(actual_close=-1.0)]) == []
    assert sector_timeseries([_row(predicted_return=None)]) == []
    assert sector_timeseries([_row(prediction_date="")]) == []


def test_empty_input_is_empty_output() -> None:
    assert sector_timeseries([]) == []


def test_unmapped_ticker_falls_into_its_own_bucket() -> None:
    rows = sector_timeseries([_row(ticker="0000")])
    assert rows[0].sector == "未分類"


class _Recorder:
    """Minimal stand-in for the streamlit surface render_alerts touches."""

    def __init__(self) -> None:
        self.errors: list[str] = []
        self.warnings: list[str] = []
        self.infos: list[str] = []
        self.expanders: list[tuple[str, bool]] = []
        self.tables: list[object] = []

    def error(self, body: str) -> None:
        self.errors.append(body)

    def warning(self, body: str) -> None:
        self.warnings.append(body)

    def info(self, body: str) -> None:
        self.infos.append(body)

    def dataframe(self, rows: object, **_: object) -> None:
        self.tables.append(rows)

    def expander(self, label: str, expanded: bool = False) -> _Recorder:
        self.expanders.append((label, expanded))
        return self

    def __enter__(self) -> _Recorder:
        return self

    def __exit__(self, *_: object) -> bool:
        return False


def _render(alerts: tuple[Alert, ...], monkeypatch: Any) -> _Recorder:
    import dashboard.ui as ui

    recorder = _Recorder()
    monkeypatch.setattr(ui, "st", recorder)
    ui.render_alerts(alerts)
    return recorder


def test_errors_stay_inline_and_are_never_collapsed(monkeypatch: Any) -> None:
    recorder = _render(
        (
            Alert(AlertLevel.ERROR, "STALE / MISSING", "3系列"),
            Alert(AlertLevel.WARNING, "品質注意", "17系列"),
        ),
        monkeypatch,
    )
    assert len(recorder.errors) == 1
    assert "STALE / MISSING" in recorder.errors[0]
    assert recorder.expanders == [("⚠️ 注意 1件", False)]


def test_advisories_collapse_behind_a_count(monkeypatch: Any) -> None:
    alerts = tuple(
        Alert(AlertLevel.WARNING, f"{ticker} warning", "FREE_UNVERIFIED")
        for ticker in ("7203", "7267", "8306")
    )
    recorder = _render(alerts, monkeypatch)
    assert recorder.errors == []
    assert recorder.expanders == [("⚠️ 注意 3件", False)]
    assert len(recorder.warnings) == 3


def test_label_separates_warnings_from_info(monkeypatch: Any) -> None:
    recorder = _render(
        (
            Alert(AlertLevel.WARNING, "品質注意", "17系列"),
            Alert(AlertLevel.INFO, "参考", "補足"),
        ),
        monkeypatch,
    )
    assert recorder.expanders == [("⚠️ 注意 2件（警告 1件）", False)]
    assert len(recorder.warnings) == 1
    assert len(recorder.infos) == 1


def test_a_large_band_becomes_one_table_not_hundreds_of_widgets(
    monkeypatch: Any,
) -> None:
    # 2026-09-07 produced 446 of these. Stacked callouts are built even while
    # the expander is shut, so the table path is what keeps the page usable.
    from dashboard.ui import ADVISORY_TABLE_THRESHOLD

    alerts = tuple(
        Alert(AlertLevel.WARNING, f"{index} warning", "FREE_UNVERIFIED")
        for index in range(ADVISORY_TABLE_THRESHOLD)
    )
    recorder = _render(alerts, monkeypatch)
    assert recorder.warnings == []
    assert len(recorder.tables) == 1
    assert len(recorder.tables[0]) == ADVISORY_TABLE_THRESHOLD  # type: ignore[arg-type]
    assert recorder.expanders == [(f"⚠️ 注意 {ADVISORY_TABLE_THRESHOLD}件", False)]


def test_a_small_band_stays_as_readable_callouts(monkeypatch: Any) -> None:
    from dashboard.ui import ADVISORY_TABLE_THRESHOLD

    alerts = tuple(
        Alert(AlertLevel.WARNING, f"{index} warning", "FREE_UNVERIFIED")
        for index in range(ADVISORY_TABLE_THRESHOLD - 1)
    )
    recorder = _render(alerts, monkeypatch)
    assert recorder.tables == []
    assert len(recorder.warnings) == ADVISORY_TABLE_THRESHOLD - 1


def test_no_expander_when_there_is_nothing_to_fold(monkeypatch: Any) -> None:
    recorder = _render((Alert(AlertLevel.ERROR, "Pipeline FAILED", "x"),), monkeypatch)
    assert recorder.expanders == []
    assert len(recorder.errors) == 1

    empty = _render((), monkeypatch)
    assert empty.expanders == []
    assert empty.errors == []
