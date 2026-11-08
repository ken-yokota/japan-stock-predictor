"""Recover missing live outcomes without changing the historical publication."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from types import SimpleNamespace
from typing import Any

import pytest
from sqlalchemy.orm import Session, sessionmaker

from data.config import AppConfig
from data.market_calendar import MarketCalendarError
from database.models import PredictionSet
from pipeline.close import ClosePipeline, unsettled_close_dates
from tests.test_pipeline import (
    PREDICTION_DATE,
    _environment,
    _seed_prediction_set,
    _stock_revision,
)
from tests.test_pipeline import (
    app_config as app_config,
)
from tests.test_pipeline import (
    sqlite_factory as sqlite_factory,
)


def test_missing_outcome_remains_visible_until_a_complete_retry(
    sqlite_factory: sessionmaker[Session], app_config: AppConfig
) -> None:
    _seed_prediction_set(sqlite_factory, app_config, with_buy=True)
    with sqlite_factory() as session:
        assert unsettled_close_dates(session, PREDICTION_DATE, session_count=10) == (
            PREDICTION_DATE,
        )
        session.add(
            _stock_revision(
                close="6060",
                raw_hash="d" * 64,
                first_observed_at=datetime(2026, 8, 12, 6, 55, tzinfo=UTC),
            )
        )
        session.commit()
    result = ClosePipeline(sqlite_factory, app_config, _environment()).run(
        PREDICTION_DATE,
        observed_at=datetime(2026, 8, 12, 7, 0, tzinfo=UTC),
        fetch_data=False,
    )
    assert result.status == "SUCCESS"
    with sqlite_factory() as session:
        assert unsettled_close_dates(session, PREDICTION_DATE, session_count=10) == ()


def test_recovery_excludes_late_publications_and_bounds_its_window(
    sqlite_factory: sessionmaker[Session], app_config: AppConfig
) -> None:
    set_id = _seed_prediction_set(sqlite_factory, app_config, with_buy=True)
    with sqlite_factory() as session:
        assert unsettled_close_dates(session, date(2026, 8, 14), session_count=1) == ()
        saved = session.get(PredictionSet, set_id)
        assert saved is not None
        saved.published_at = saved.cutoff_at + timedelta(minutes=1)
        session.commit()
        assert unsettled_close_dates(session, date(2026, 8, 14), session_count=10) == ()


def test_a_newer_live_set_does_not_hide_behind_an_older_finalized_set(
    sqlite_factory: sessionmaker[Session], app_config: AppConfig
) -> None:
    first_id = _seed_prediction_set(sqlite_factory, app_config, with_buy=True)
    with sqlite_factory() as session:
        session.add(
            _stock_revision(
                close="6060",
                raw_hash="d" * 64,
                first_observed_at=datetime(2026, 8, 12, 6, 55, tzinfo=UTC),
            )
        )
        session.commit()
    ClosePipeline(sqlite_factory, app_config, _environment()).run(
        PREDICTION_DATE,
        observed_at=datetime(2026, 8, 12, 7, 0, tzinfo=UTC),
        fetch_data=False,
    )
    second_id = _seed_prediction_set(sqlite_factory, app_config, with_buy=True)
    with sqlite_factory() as session:
        first = session.get(PredictionSet, first_id)
        second = session.get(PredictionSet, second_id)
        assert first is not None and second is not None
        second.published_at = first.published_at + timedelta(seconds=1)
        session.commit()
        assert unsettled_close_dates(session, PREDICTION_DATE, session_count=10) == (
            PREDICTION_DATE,
        )


def test_calendar_failure_cannot_silently_choose_an_unopened_day(
    monkeypatch: Any,
) -> None:
    import scripts.run_close_update as script

    def unavailable(_: datetime) -> date:
        raise MarketCalendarError("calendar unavailable")

    monkeypatch.setattr(script, "latest_settled_session", unavailable)
    config = SimpleNamespace(
        settings=SimpleNamespace(application=SimpleNamespace(timezone="Asia/Tokyo"))
    )
    with pytest.raises(MarketCalendarError):
        script._settled_session(config)


def test_runtime_configuration_targets_the_prior_session_after_midnight(
    app_config: AppConfig, monkeypatch: Any
) -> None:
    import scripts.run_close_update as script

    clock = SimpleNamespace(
        now=lambda timezone: datetime(2026, 11, 3, 1, 32, tzinfo=timezone)
    )
    monkeypatch.setattr(script, "datetime", clock)
    assert script._settled_session(app_config) == date(2026, 11, 2)
