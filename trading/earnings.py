"""Earnings-window exclusion.

Around an earnings release, a stock's intraday move is driven by the release
itself, not by the overseas indicators this system models. Those sessions are
therefore excluded from trading and from evaluation rather than being scored as
model successes or failures.

## Why this is not look-ahead

Only the *scheduled announcement date* is used, never the release contents.
Japanese issuers publish their reporting date weeks in advance, so the schedule
is known long before the 08:30 cutoff of any session it affects. Skipping a day
because a release is known to be scheduled is information a real operator has
that morning.

## Why the window is asymmetric by default

Japanese companies almost always announce after the close (15:00 JST or later).
The session that reacts is therefore the *next* one. A window of "the
announcement date plus the following session" covers the reaction day while also
covering the rarer case of an intraday or pre-open release.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

DEFAULT_CALENDAR_PATH = Path("config/earnings_dates.json")


@dataclass(frozen=True, slots=True)
class EarningsWindow:
    """How many sessions around an announcement are excluded."""

    days_before: int = 1
    days_after: int = 1

    def __post_init__(self) -> None:
        if self.days_before < 0 or self.days_after < 0:
            raise ValueError("days_before and days_after must not be negative")


class EarningsCalendar:
    """Scheduled announcement dates, keyed by ticker."""

    def __init__(self, dates: Mapping[str, Iterable[str | date]]) -> None:
        self._dates: dict[str, frozenset[date]] = {}
        for ticker, values in dates.items():
            parsed = set()
            for value in values:
                parsed.add(
                    value if isinstance(value, date) else date.fromisoformat(str(value))
                )
            self._dates[str(ticker)] = frozenset(parsed)

    @classmethod
    def load(cls, path: Path | None = None) -> EarningsCalendar:
        """Read the cached calendar, or return an empty one if unreadable.

        An empty calendar excludes nothing. That is the safe direction: a
        missing file must not silently drop every session, and it must not
        pretend a release did not happen either -- the caller reports coverage
        so a stale file is visible rather than assumed correct.
        """

        target = path or DEFAULT_CALENDAR_PATH
        try:
            payload = json.loads(target.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, TypeError, ValueError):
            return cls({})
        dates = payload.get("dates") if isinstance(payload, Mapping) else None
        if not isinstance(dates, Mapping):
            return cls({})
        return cls(dates)

    @property
    def tickers(self) -> frozenset[str]:
        """Return the tickers this calendar has any date for."""

        return frozenset(self._dates)

    def dates_for(self, ticker: str) -> frozenset[date]:
        """Return the announcement dates known for one ticker."""

        return self._dates.get(str(ticker), frozenset())

    def is_blackout(
        self,
        ticker: str,
        session: date,
        *,
        window: EarningsWindow | None = None,
        sessions: Sequence[date] | None = None,
    ) -> bool:
        """Return whether ``session`` falls inside ``ticker``'s earnings window.

        ``sessions`` should be the trading calendar in ascending order. When
        supplied, the window is counted in trading sessions, so a Friday
        announcement correctly blacks out the following Monday rather than the
        weekend. Without it the window falls back to calendar days, which is
        wider and therefore still safe.
        """

        announcements = self.dates_for(ticker)
        if not announcements:
            return False
        settings = window or EarningsWindow()

        if sessions:
            ordered = list(sessions)
            try:
                position = ordered.index(session)
            except ValueError:
                return self._calendar_day_blackout(session, announcements, settings)
            low = max(0, position - settings.days_after)
            high = min(len(ordered) - 1, position + settings.days_before)
            # A session is blacked out when an announcement lands on it, or
            # close enough before it that this session is the reaction.
            nearby = set(ordered[low : high + 1])
            return bool(nearby & announcements)

        return self._calendar_day_blackout(session, announcements, settings)

    @staticmethod
    def _calendar_day_blackout(
        session: date, announcements: frozenset[date], settings: EarningsWindow
    ) -> bool:
        for offset in range(-settings.days_after, settings.days_before + 1):
            if session + timedelta(days=offset) in announcements:
                return True
        return False


def blackout_reason(
    ticker: str,
    session: date,
    calendar: EarningsCalendar,
    *,
    window: EarningsWindow | None = None,
    sessions: Sequence[date] | None = None,
) -> str | None:
    """Return a human-readable exclusion reason, or ``None`` to trade normally."""

    if not calendar.dates_for(ticker):
        return None
    if calendar.is_blackout(ticker, session, window=window, sessions=sessions):
        return "EARNINGS_WINDOW"
    return None
