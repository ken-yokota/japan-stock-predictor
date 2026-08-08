"""Japanese exchange session helpers."""

from __future__ import annotations

from datetime import UTC, date, datetime
from functools import lru_cache
from typing import Any, cast

import exchange_calendars as xcals


class MarketCalendarError(RuntimeError):
    """Raised when the exchange calendar cannot answer safely."""


@lru_cache(maxsize=4)
def _calendar(name: str = "XTKS") -> Any:
    try:
        return xcals.get_calendar(name)
    except Exception as exc:  # pragma: no cover - library-specific exception types
        raise MarketCalendarError(f"unable to load calendar {name}") from exc


def is_japan_business_day(value: date) -> bool:
    """Return whether ``value`` is a Tokyo Stock Exchange trading session."""

    try:
        return bool(_calendar().is_session(value.isoformat()))
    except Exception as exc:
        if isinstance(exc, MarketCalendarError):
            raise
        raise MarketCalendarError(f"calendar lookup failed for {value}") from exc


def previous_japan_session(value: date) -> date:
    """Return the session strictly before ``value``."""

    try:
        session = _calendar().date_to_session(value.isoformat(), direction="previous")
        if session.date() == value:
            session = _calendar().previous_session(session)
        return cast(date, session.date())
    except Exception as exc:
        raise MarketCalendarError(
            f"previous-session lookup failed for {value}"
        ) from exc


def japan_sessions_before(value: date, count: int) -> tuple[date, ...]:
    """Return the newest ``count`` JPX sessions strictly before ``value``."""

    if count <= 0:
        raise ValueError("count must be positive")
    try:
        calendar = _calendar()
        last = calendar.date_to_session(value.isoformat(), direction="previous")
        if last.date() == value:
            last = calendar.previous_session(last)
        sessions = [last]
        for _ in range(count - 1):
            sessions.append(calendar.previous_session(sessions[-1]))
        sessions.reverse()
        return tuple(cast(date, session.date()) for session in sessions)
    except Exception as exc:
        if isinstance(exc, (MarketCalendarError, ValueError)):
            raise
        raise MarketCalendarError(
            f"session-window lookup failed for {value} ({count} sessions)"
        ) from exc


def japan_sessions_between(start_date: date, end_date: date) -> tuple[date, ...]:
    """Return all JPX sessions in the inclusive calendar-date interval."""

    if start_date > end_date:
        raise ValueError("start_date must not be after end_date")
    try:
        sessions = _calendar().sessions_in_range(start_date, end_date)
        return tuple(cast(date, session.date()) for session in sessions)
    except Exception as exc:
        if isinstance(exc, (MarketCalendarError, ValueError)):
            raise
        raise MarketCalendarError(
            f"session-range lookup failed for {start_date}..{end_date}"
        ) from exc


def japan_session_close(value: date) -> datetime:
    """Return the official calendar close for one JPX session in UTC."""

    try:
        calendar = _calendar()
        if not calendar.is_session(value.isoformat()):
            raise MarketCalendarError(f"{value} is not a JPX session")
        close = calendar.session_close(value.isoformat())
        return cast(datetime, close.to_pydatetime()).astimezone(UTC)
    except Exception as exc:
        if isinstance(exc, MarketCalendarError):
            raise
        raise MarketCalendarError(f"session-close lookup failed for {value}") from exc


def japan_session_open(value: date) -> datetime:
    """Return the official calendar open for one JPX session in UTC."""

    try:
        calendar = _calendar()
        if not calendar.is_session(value.isoformat()):
            raise MarketCalendarError(f"{value} is not a JPX session")
        market_open = calendar.session_open(value.isoformat())
        return cast(datetime, market_open.to_pydatetime()).astimezone(UTC)
    except Exception as exc:
        if isinstance(exc, MarketCalendarError):
            raise
        raise MarketCalendarError(f"session-open lookup failed for {value}") from exc
