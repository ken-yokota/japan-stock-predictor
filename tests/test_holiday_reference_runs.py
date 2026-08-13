"""A prediction for a day the market never opens is not a morning.

2026-08-11 was a JPX holiday. A run forced onto it was recorded as MORNING,
published READY with three BUYs, and became indistinguishable from a live
morning in the production record - a session that never opened, carrying buy
signals that could never settle.

The calendar check was never broken: a second run that morning skipped
correctly. What was missing is that forcing past the check did not change what
the run claimed to be.
"""

from __future__ import annotations

import inspect
from datetime import date

from data.market_calendar import is_japan_business_day
from pipeline.morning import (
    LIVE_RUN_TYPE,
    REFERENCE_RUN_TYPE,
    morning_run_type,
)


def test_the_calendar_knows_the_holiday() -> None:
    assert is_japan_business_day(date(2026, 8, 10)) is True
    assert is_japan_business_day(date(2026, 8, 11)) is False  # 山の日
    assert is_japan_business_day(date(2026, 8, 12)) is True


def test_a_closed_market_produces_a_reference_run() -> None:
    assert morning_run_type(is_business_day=False) == REFERENCE_RUN_TYPE
    assert morning_run_type(is_business_day=True) == LIVE_RUN_TYPE


def test_a_reference_run_is_not_called_a_morning() -> None:
    """The two must stay distinguishable after the fact."""

    assert REFERENCE_RUN_TYPE != LIVE_RUN_TYPE


def test_the_pipeline_no_longer_hardcodes_morning() -> None:
    """Forcing a holiday must not be able to claim MORNING again."""

    source = inspect.getsource(__import__("pipeline.morning", fromlist=["x"]))
    assert 'run_type="MORNING"' not in source


def test_scoring_only_settles_a_live_morning() -> None:
    from pipeline import close

    source = inspect.getsource(close)
    assert "DailyRun.run_type == \"MORNING\"" in source


def test_the_morning_mail_only_carries_a_live_morning() -> None:
    from services import email

    source = inspect.getsource(email)
    assert "DailyRun.run_type == \"MORNING\"" in source


def test_the_dashboard_production_reads_are_scoped_to_mornings() -> None:
    from dashboard import query_service

    source = inspect.getsource(query_service)
    # latest set, today's predictions, and the completeness read.
    assert source.count("run_type = 'MORNING'") >= 3
