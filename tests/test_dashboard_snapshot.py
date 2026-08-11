"""The published snapshot is public, so this tests what must never reach it.

Two independent guarantees are checked: that only allowlisted fields are
copied out of a row, and that a credential value which somehow survived into
the text aborts the write. The second one exists precisely because the first
one can be got wrong later.
"""

from __future__ import annotations

import json
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

import pytest

from dashboard.types import QueryResult, QueryState
from scripts.export_dashboard_snapshot import (
    SnapshotError,
    assert_no_secrets,
    build_snapshot,
    secret_values,
)

NEON_URL = "postgresql+psycopg://user:sup3rs3cret@ep-x-y.ap-southeast-2.aws.neon.tech/db"


class _StubService:
    """Returns fixed reads in place of the database."""

    def __init__(
        self,
        *,
        run: QueryResult,
        prediction_set: QueryResult,
        predictions: QueryResult,
        health: QueryResult | None = None,
    ) -> None:
        self._run = run
        self._prediction_set = prediction_set
        self._predictions = predictions
        self._health = health or QueryResult.from_rows(({"ok": True},))

    def database_health(self) -> QueryResult:
        return self._health

    def latest_run(self) -> QueryResult:
        return self._run

    def latest_prediction_set(self) -> QueryResult:
        return self._prediction_set

    def today_predictions(self) -> QueryResult:
        return self._predictions


def _run_row(**overrides: Any) -> dict[str, Any]:
    row: dict[str, Any] = {
        "run_id": "11111111-2222-3333-4444-555555555555",
        "run_type": "MORNING",
        "prediction_date": date(2026, 8, 12),
        "cutoff_at": datetime(2026, 8, 12, 8, 30, tzinfo=UTC),
        "started_at": datetime(2026, 8, 12, 8, 10, tzinfo=UTC),
        "finished_at": datetime(2026, 8, 12, 8, 35, tzinfo=UTC),
        "status": "READY",
        "current_step": None,
        "model_version": "m1",
        "data_version": "cfg-hash",
        "failed_symbols": ["9107"],
    }
    row.update(overrides)
    return row


def _prediction_row(ticker: str, **overrides: Any) -> dict[str, Any]:
    row: dict[str, Any] = {
        "ticker": ticker,
        "status": "SUCCESS",
        "signal": "BUY",
        "rank": 1,
        "predicted_intraday_return": Decimal("0.0123"),
        "probability_up": 0.61,
        "reference_price": Decimal("3210.5"),
        "feature_coverage": 0.98,
        "warnings": [],
        "created_at": datetime(2026, 8, 12, 8, 34, tzinfo=UTC),
        "actual_open": None,
    }
    row.update(overrides)
    return row


def _service(**overrides: Any) -> _StubService:
    defaults: dict[str, Any] = {
        "run": QueryResult.from_rows((_run_row(),)),
        "prediction_set": QueryResult.from_rows(
            ({"prediction_set_id": "ps-1", "status": "READY"},)
        ),
        "predictions": QueryResult.from_rows(
            (_prediction_row("9101"), _prediction_row("8306", signal="HOLD", rank=None))
        ),
    }
    defaults.update(overrides)
    return _StubService(**defaults)


def test_only_allowlisted_fields_are_published() -> None:
    """A column the query gains later must not appear in a public file."""

    leaky = _run_row(internal_connection_note=NEON_URL, some_new_column="x")
    snapshot = build_snapshot(_service(run=QueryResult.from_rows((leaky,))))

    assert "internal_connection_note" not in snapshot["latest_run"]
    assert "some_new_column" not in snapshot["latest_run"]
    # data_version is a real column of the query and still not published.
    assert "data_version" not in snapshot["latest_run"]
    assert snapshot["latest_run"]["run_type"] == "MORNING"
    assert NEON_URL not in json.dumps(snapshot, ensure_ascii=False)


def test_snapshot_reports_run_time_cutoff_and_per_ticker_predictions() -> None:
    snapshot = build_snapshot(_service())

    assert snapshot["latest_run"]["started_at"] == "2026-08-12T08:10:00+00:00"
    assert snapshot["latest_run"]["cutoff_at"] == "2026-08-12T08:30:00+00:00"
    assert [row["ticker"] for row in snapshot["predictions"]] == ["9101", "8306"]
    assert snapshot["predictions"][0]["predicted_intraday_return"] == pytest.approx(
        0.0123
    )
    assert snapshot["summary"]["ticker_count"] == 2
    assert snapshot["summary"]["buy_count"] == 1
    assert snapshot["summary"]["failed_symbols"] == ["9107"]


def test_error_state_is_carried_without_connection_detail() -> None:
    """An unreadable database must still produce a publishable file."""

    unavailable = QueryResult.unavailable()
    snapshot = build_snapshot(
        _service(run=unavailable, prediction_set=unavailable, predictions=unavailable)
    )

    assert snapshot["availability"]["predictions"]["state"] == QueryState.UNAVAILABLE
    assert snapshot["latest_run"] == {}
    assert snapshot["predictions"] == []
    assert snapshot["summary"]["ticker_count"] == 0
    text = json.dumps(snapshot, ensure_ascii=False)
    for fragment in ("postgres", "neon.tech", "@", "password"):
        assert fragment not in text


def test_a_credential_in_the_payload_aborts_the_write() -> None:
    environment = {"DATABASE_URL": NEON_URL, "PATH": "/usr/bin"}
    with pytest.raises(SnapshotError):
        assert_no_secrets(f'{{"leaked": "{NEON_URL}"}}', environment)


def test_ordinary_text_is_not_mistaken_for_a_credential() -> None:
    environment = {"DATABASE_URL": NEON_URL, "TIMEZONE": "Asia/Tokyo"}
    assert_no_secrets('{"ticker": "9101", "timezone": "Asia/Tokyo"}', environment)


def test_short_environment_values_are_not_searched_for() -> None:
    """A port or a one-word host would match everything and block every run."""

    assert secret_values({"DB_PORT": "5432", "API_KEY": "short"}) == []
    assert secret_values({"EODHD_API_KEY": "abcdefghijklmnop"}) == ["abcdefghijklmnop"]


def test_every_secret_shaped_variable_is_covered_not_just_the_database_url() -> None:
    environment = {
        "DATABASE_URL": NEON_URL,
        "EODHD_API_KEY": "eodhd-1234567890",
        "SMTP_PASSWORD": "hunter2-hunter2",
        "RESEND_API_KEY": "re_1234567890abcd",
        "HOME": "/home/runner",
    }
    found = set(secret_values(environment))
    assert found == {
        NEON_URL,
        "eodhd-1234567890",
        "hunter2-hunter2",
        "re_1234567890abcd",
    }
