from __future__ import annotations

from datetime import date, timedelta
from typing import Any

import pytest

from data.availability import prediction_cutoff
from data.config import load_app_config
from data.env import EnvironmentSettings
from data.fetch import IngestionReport
from scripts import prefetch_morning_data as prefetch
from services.ingestion import IngestionOutcome


def _outcome(report: IngestionReport) -> IngestionOutcome:
    return IngestionOutcome("ingestion-run", report)


def test_prefetch_uses_fixed_cutoff_history_window_and_no_snapshots(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    prediction_date = date(2026, 8, 12)
    captured: dict[str, Any] = {}
    report = IngestionReport(requested_sources=3, succeeded_sources=2)
    report.skipped_sources["live_factor"] = prefetch.SNAPSHOT_SKIP_REASON

    def fake_ingest(*args: object, **kwargs: object) -> IngestionOutcome:
        captured.update(kwargs)
        return _outcome(report)

    monkeypatch.setattr(prefetch, "ingest_free_morning_data", fake_ingest)
    result = prefetch.run_prefetch(
        object(),  # type: ignore[arg-type]
        load_app_config(),
        EnvironmentSettings(_env_file=None),
        prediction_date=prediction_date,
    )

    assert captured["prediction_date"] == prediction_date
    assert captured["start_date"] == prediction_date - timedelta(days=550)
    assert captured["end_date"] == prediction_date - timedelta(days=1)
    assert captured["include_snapshots"] is False
    assert result.cutoff_at == prediction_cutoff(prediction_date)
    assert result.status == "SUCCESS"
    assert result.ingestion_status == "PARTIAL"
    assert prefetch._exit_code(result) == 0


def test_prefetch_holiday_is_skipped_without_ingestion(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def unexpected(*args: object, **kwargs: object) -> IngestionOutcome:
        raise AssertionError("holiday must not start ingestion")

    monkeypatch.setattr(prefetch, "ingest_free_morning_data", unexpected)
    result = prefetch.run_prefetch(
        object(),  # type: ignore[arg-type]
        load_app_config(),
        EnvironmentSettings(_env_file=None),
        prediction_date=date(2026, 8, 11),  # Mountain Day
    )

    assert result.status == "SKIPPED"
    assert result.run_id is None
    assert prefetch._exit_code(result) == 0


@pytest.mark.parametrize("failure_kind", ["failed", "unresolved", "unexpected_skip"])
def test_prefetch_fails_closed_for_non_snapshot_gaps(
    monkeypatch: pytest.MonkeyPatch,
    failure_kind: str,
) -> None:
    report = IngestionReport(requested_sources=1)
    if failure_kind == "failed":
        report.failed_sources["nikkei"] = "provider unavailable"
    elif failure_kind == "unresolved":
        report.unresolved_required.append("required_factor")
    else:
        report.skipped_sources["treasury"] = "latest session is not published"
    monkeypatch.setattr(
        prefetch,
        "ingest_free_morning_data",
        lambda *args, **kwargs: _outcome(report),
    )

    result = prefetch.run_prefetch(
        object(),  # type: ignore[arg-type]
        load_app_config(),
        EnvironmentSettings(_env_file=None),
        prediction_date=date(2026, 8, 12),
    )

    assert result.status == "FAILED"
    assert prefetch._exit_code(result) == 2


def test_prefetch_second_run_can_report_every_series_as_covered(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    report = IngestionReport(requested_sources=2, succeeded_sources=2)
    report.covered_sources.update(
        {
            "7203": "保存済み: 取得を省略",
            "sp500": "保存済み: 取得を省略",
        }
    )
    monkeypatch.setattr(
        prefetch,
        "ingest_free_morning_data",
        lambda *args, **kwargs: _outcome(report),
    )

    result = prefetch.run_prefetch(
        object(),  # type: ignore[arg-type]
        load_app_config(),
        EnvironmentSettings(_env_file=None),
        prediction_date=date(2026, 8, 12),
    )

    assert result.status == "SUCCESS"
    assert result.inserted_rows == 0
    assert result.covered_sources == report.covered_sources


def test_prefetch_override_reaches_ingestion_on_a_holiday(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The holiday gate must be opt-out, and the override must reach ingestion."""

    holiday = date(2026, 8, 11)
    captured: dict[str, Any] = {}
    report = IngestionReport(requested_sources=3, succeeded_sources=3)

    def fake_ingest(*args: object, **kwargs: object) -> IngestionOutcome:
        captured.update(kwargs)
        return _outcome(report)

    monkeypatch.setattr(prefetch, "ingest_free_morning_data", fake_ingest)
    result = prefetch.run_prefetch(
        object(),  # type: ignore[arg-type]
        load_app_config(),
        EnvironmentSettings(_env_file=None),
        prediction_date=holiday,
        allow_non_business_day=True,
    )

    assert captured["prediction_date"] == holiday
    assert captured["end_date"] == holiday - timedelta(days=1)
    assert result.status == "SUCCESS"
    assert result.run_id == "ingestion-run"
