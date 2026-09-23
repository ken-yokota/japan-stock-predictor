"""Discover and execute every page; an empty DB must remain a usable screen."""

from pathlib import Path

import pytest
import streamlit as st
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool
from streamlit.testing.v1 import AppTest

from dashboard.query_service import DashboardQueryService
from dashboard.types import QueryResult
from database.models import Base

ROOT = Path(__file__).resolve().parents[1]
PAGES = [*sorted((ROOT / "pages").glob("*.py")), ROOT / "app.py"]


@pytest.mark.parametrize("page", PAGES, ids=lambda p: p.name)
@pytest.mark.parametrize("migrated", [True, False], ids=["empty-schema", "no-schema"])
def test_streamlit_all_pages(page, migrated, monkeypatch):
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    if migrated:
        Base.metadata.create_all(engine)
    service = DashboardQueryService(engine)
    import dashboard.ui as ui

    monkeypatch.setattr(ui, "service_from_environment", lambda: service)
    st.cache_resource.clear()
    app = AppTest.from_file(str(page), default_timeout=30).run()
    assert not app.exception, [(e.message, e.stack_trace) for e in app.exception]
    # The initial selected tab must remain safe even with an empty schema.
    engine.dispose()
    st.cache_resource.clear()


def test_history_only_reads_the_selected_window(monkeypatch):
    """Hidden History tabs must not triple reads and chart work on page load."""

    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    service = DashboardQueryService(engine)
    import dashboard.ui as ui

    seen: list[str | None] = []

    def history_window(
        _service: DashboardQueryService, since: str | None
    ) -> QueryResult:
        seen.append(since)
        return QueryResult.from_rows(())

    monkeypatch.setattr(ui, "service_from_environment", lambda: service)
    monkeypatch.setattr(ui, "cached_prediction_history_window", history_window)
    st.cache_resource.clear()
    app = AppTest.from_file(
        str(ROOT / "pages" / "2_History.py"), default_timeout=30
    ).run()
    assert not app.exception
    assert len(seen) == 1
    assert seen[0] is not None
    engine.dispose()
    st.cache_resource.clear()
