"""The watchdog exists so a missed send is loud rather than invisible.

Its own failure modes matter more than most: it is the only thing that
notices when everything else stops quietly.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, time

import pytest

from scripts.verify_daily_delivery import (
    Check,
    Outcome,
    _alert_bodies,
    automation_check,
    capacity_band,
    email_check,
    snapshot_check,
    window_is_due,
    window_start,
)

TODAY = date(2026, 8, 12)


def _outcome(verdict: str = "CHECKED", **results: bool) -> Outcome:
    return Outcome(
        window="morning",
        for_date=TODAY,
        verdict=verdict,
        checks=[Check(name, ok, "detail") for name, ok in results.items()],
    )


# --- the flag that silences everything else -------------------------------


def test_automation_enabled_true_passes() -> None:
    assert automation_check("true").ok


def test_automation_enabled_false_is_a_failure() -> None:
    check = automation_check("false")
    assert not check.ok
    assert "false" in check.detail


def test_automation_enabled_missing_is_a_failure() -> None:
    """Unset is as dangerous as false: the workflow `if:` sees neither."""

    assert not automation_check(None).ok
    assert not automation_check("   ").ok


def test_automation_enabled_invalid_is_a_failure() -> None:
    assert not automation_check("yes").ok


def test_a_disabled_flag_alerts_even_outside_the_window() -> None:
    """The watchdog must not wait for 09:10 to report that nothing will run."""

    outcome = _outcome(verdict="NOT_YET_DUE", automation=False)
    assert outcome.alerting

    holiday = _outcome(verdict="NON_TRADING_DAY", automation=False)
    assert holiday.alerting


# --- not every red is a fault ---------------------------------------------


def test_before_the_window_closes_nothing_is_due() -> None:
    early = datetime(2026, 8, 12, 6, 17, tzinfo=UTC).astimezone()
    assert not window_is_due("morning", datetime(2026, 8, 12, 6, 17, tzinfo=UTC))
    assert early is not None


def test_after_the_window_closes_it_is_due() -> None:
    # 09:10 JST is 00:10 UTC.
    assert window_is_due("morning", datetime(2026, 8, 12, 0, 10, tzinfo=UTC))


def test_a_window_that_is_not_due_does_not_alert() -> None:
    outcome = _outcome(verdict="NOT_YET_DUE", automation=True)
    assert not outcome.alerting


def test_a_holiday_does_not_alert() -> None:
    outcome = _outcome(verdict="NON_TRADING_DAY", automation=True)
    assert not outcome.alerting


# --- email --------------------------------------------------------------


def test_no_sent_row_is_a_missing_email() -> None:
    assert not email_check([]).ok


def test_exactly_one_sent_row_is_healthy() -> None:
    assert email_check([{"sent_at": "2026-08-12T08:45", "idempotency_key": "k"}]).ok


def test_two_sent_rows_are_a_duplicate_send() -> None:
    check = email_check(
        [
            {"sent_at": "2026-08-12T08:45", "idempotency_key": "a"},
            {"sent_at": "2026-08-12T08:50", "idempotency_key": "b"},
        ]
    )
    assert not check.ok
    assert "重複" in check.detail


# --- snapshot -----------------------------------------------------------


def _snapshot(generated: str, prediction_date: str) -> dict[str, object]:
    return {
        "generated_at": generated,
        "prediction_set": {"prediction_date": prediction_date},
    }


def test_a_fresh_snapshot_for_today_passes() -> None:
    check = snapshot_check(
        _snapshot("2026-08-11T23:41:00+00:00", "2026-08-12"),
        for_date=TODAY,
        since=window_start("morning", TODAY),
    )
    assert check.ok


def test_yesterdays_healthy_snapshot_does_not_stand_in_for_today() -> None:
    check = snapshot_check(
        _snapshot("2026-08-10T23:41:00+00:00", "2026-08-11"),
        for_date=TODAY,
        since=window_start("morning", TODAY),
    )
    assert not check.ok


def test_a_snapshot_older_than_the_window_is_stale() -> None:
    """Right date, but generated before the window produced anything."""

    check = snapshot_check(
        _snapshot("2026-08-11T16:03:00+00:00", "2026-08-12"),
        for_date=TODAY,
        since=window_start("morning", TODAY),
    )
    assert not check.ok
    assert "古い" in check.detail


def test_an_unreachable_snapshot_is_a_failure() -> None:
    check = snapshot_check(None, for_date=TODAY, since=window_start("morning", TODAY))
    assert not check.ok


# --- database capacity ---------------------------------------------------


@pytest.mark.parametrize(
    ("ratio", "expected"),
    [
        (0.10, "NORMAL"),
        (0.69, "NORMAL"),
        (0.70, "WARNING"),
        (0.86, "ALERT"),
        (0.99, "CRITICAL"),
    ],
)
def test_capacity_bands(ratio: float, expected: str) -> None:
    assert capacity_band(int(512 * 1024 * 1024 * ratio)) == expected


# --- the alert itself ----------------------------------------------------


def test_the_subject_names_what_is_missing() -> None:
    """The operator reads the subject on a phone; it has to carry the fact."""

    subject, text_body, html_body = _alert_bodies(
        _outcome(prediction=True, email=False, dashboard=False)
    )
    assert "email" in subject and "dashboard" in subject
    assert "ALERT" in subject
    for name in ("prediction", "email", "dashboard"):
        assert name in text_body
        assert name in html_body
    assert "NG" in text_body and "OK" in text_body


def test_window_hours_are_ordered() -> None:
    for window in ("morning", "evening"):
        start = window_start(window, TODAY)
        assert isinstance(start, datetime)
        assert window_is_due(window, datetime.combine(TODAY, time(23, 0), start.tzinfo))
