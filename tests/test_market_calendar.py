from datetime import date
from zoneinfo import ZoneInfo

from data.market_calendar import (
    is_japan_business_day,
    japan_session_close,
    previous_japan_session,
)


def test_jpx_holiday_and_weekend() -> None:
    assert is_japan_business_day(date(2026, 8, 10))
    assert not is_japan_business_day(date(2026, 8, 8))
    assert not is_japan_business_day(date(2026, 8, 11))  # Mountain Day


def test_previous_session_skips_jpx_holiday() -> None:
    assert previous_japan_session(date(2026, 8, 12)) == date(2026, 8, 10)


def test_jpx_calendar_contains_2024_close_extension() -> None:
    tokyo = ZoneInfo("Asia/Tokyo")
    before = japan_session_close(date(2024, 11, 1)).astimezone(tokyo)
    after = japan_session_close(date(2024, 11, 5)).astimezone(tokyo)
    assert (before.hour, before.minute) == (15, 0)
    assert (after.hour, after.minute) == (15, 30)


# --------------------------------------------------------------------------
# Scheduled work must target a session, not a calendar date


def test_a_run_before_the_close_targets_the_previous_session() -> None:
    """The bug this exists for, dated 2026-08-28.

    Three close updates fired eleven hours late, at 02:53, 02:55 and 03:16 JST.
    Each read "today" from the clock, got 08-28, found nothing to settle for a
    session that had not opened, and returned SKIPPED. The evening summary made
    the same mistake at 04:00 and additionally consumed 08-28's per-date
    delivery key, so the real summary was suppressed as already sent.
    """

    from datetime import datetime
    from zoneinfo import ZoneInfo

    from data.market_calendar import latest_settled_session

    tokyo = ZoneInfo("Asia/Tokyo")

    assert latest_settled_session(
        datetime(2026, 8, 28, 3, 16, tzinfo=tokyo)
    ) == date(2026, 8, 27)


def test_after_the_close_the_session_is_todays() -> None:
    from datetime import datetime
    from zoneinfo import ZoneInfo

    from data.market_calendar import latest_settled_session

    tokyo = ZoneInfo("Asia/Tokyo")

    assert latest_settled_session(
        datetime(2026, 8, 28, 17, 0, tzinfo=tokyo)
    ) == date(2026, 8, 28)


def test_the_close_comes_from_the_calendar_not_from_a_fixed_three_pm() -> None:
    """JPX moved the close to 15:30 in November 2024; 15:00 is not the close."""

    from datetime import datetime
    from zoneinfo import ZoneInfo

    from data.market_calendar import latest_settled_session

    tokyo = ZoneInfo("Asia/Tokyo")

    assert latest_settled_session(
        datetime(2026, 8, 28, 15, 15, tzinfo=tokyo)
    ) == date(2026, 8, 27)


def test_a_weekend_run_targets_friday() -> None:
    from datetime import datetime
    from zoneinfo import ZoneInfo

    from data.market_calendar import latest_settled_session

    tokyo = ZoneInfo("Asia/Tokyo")

    assert latest_settled_session(
        datetime(2026, 8, 30, 20, 0, tzinfo=tokyo)
    ) == date(2026, 8, 28)


def test_a_naive_clock_is_refused_rather_than_assumed_to_be_tokyo() -> None:
    from datetime import datetime

    import pytest

    from data.market_calendar import latest_settled_session

    with pytest.raises(ValueError, match="timezone-aware"):
        latest_settled_session(datetime(2026, 8, 28, 17, 0))
