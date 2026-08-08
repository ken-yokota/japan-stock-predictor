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
