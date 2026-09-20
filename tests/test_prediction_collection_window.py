from datetime import date, datetime
from zoneinfo import ZoneInfo

import pytest

from data.availability import prediction_cutoff
from scripts.wait_for_prediction_window import wait_seconds


@pytest.mark.parametrize(
    ("hour", "minute", "expected"),
    [(8, 5, 900), (8, 10, 600), (8, 20, 0), (8, 30, 0), (9, 0, 0)],
)
def test_early_and_delayed_jobs_keep_the_same_cutoff(hour, minute, expected):
    day = date(2026, 9, 24)
    now = datetime(2026, 9, 24, hour, minute, tzinfo=ZoneInfo("Asia/Tokyo"))
    assert wait_seconds(now, day) == expected
    assert prediction_cutoff(day).hour == 8
    assert prediction_cutoff(day).minute == 30


def test_holiday_or_backfill_does_not_sleep_until_another_session():
    now = datetime(2026, 9, 21, 8, 5, tzinfo=ZoneInfo("Asia/Tokyo"))
    assert wait_seconds(now, date(2026, 9, 21)) == 0
    assert wait_seconds(now, date(2026, 9, 18)) == 0
    assert wait_seconds(now, date(2026, 9, 24)) == 0


def test_unexpected_early_trigger_does_not_publish_after_incomplete_wait():
    now = datetime(2026, 9, 24, 7, 55, tzinfo=ZoneInfo("Asia/Tokyo"))
    with pytest.raises(ValueError, match="before 08:05"):
        wait_seconds(now, now.date())


def test_clock_must_be_explicit_and_utc_clock_has_jst_semantics():
    with pytest.raises(ValueError, match="timezone-aware"):
        wait_seconds(datetime(2026, 9, 24, 8, 10), date(2026, 9, 24))
    now = datetime(2026, 9, 23, 23, 10, tzinfo=ZoneInfo("UTC"))
    assert wait_seconds(now, date(2026, 9, 24)) == 600
