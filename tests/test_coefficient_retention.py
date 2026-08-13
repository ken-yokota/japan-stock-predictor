"""Coefficients age out, but a year of drift stays visible.

8,156 rows a day is 549 MB a year against a 512 MB ceiling, so the window has
to be finite. Keeping the first run of each month is what stops a bounded
window from also erasing the ability to see how the model changed over a year.
"""

from __future__ import annotations

from datetime import date

from services.retention import _monthly_anchors


def test_the_first_day_of_each_month_is_an_anchor() -> None:
    dates = [
        date(2026, 6, 1),
        date(2026, 6, 15),
        date(2026, 7, 2),
        date(2026, 7, 30),
        date(2026, 8, 3),
    ]
    assert _monthly_anchors(dates) == {
        date(2026, 6, 1),
        date(2026, 7, 2),
        date(2026, 8, 3),
    }


def test_the_earliest_seen_day_wins_even_out_of_order() -> None:
    """Dates arrive newest-first from the query; the anchor is still the oldest."""

    dates = [date(2026, 7, 30), date(2026, 7, 2), date(2026, 7, 15)]
    assert _monthly_anchors(dates) == {date(2026, 7, 2)}


def test_a_month_with_one_day_keeps_that_day() -> None:
    assert _monthly_anchors([date(2026, 9, 18)]) == {date(2026, 9, 18)}


def test_no_dates_yields_no_anchors() -> None:
    assert _monthly_anchors([]) == set()


def test_years_do_not_collide() -> None:
    """August 2025 and August 2026 are different months."""

    dates = [date(2025, 8, 4), date(2026, 8, 3)]
    assert _monthly_anchors(dates) == {date(2025, 8, 4), date(2026, 8, 3)}
