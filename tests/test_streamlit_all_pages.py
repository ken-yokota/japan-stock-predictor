"""Discover and execute every page; an empty DB must remain a usable screen."""

from pathlib import Path

import pytest
import streamlit as st
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool
from streamlit.testing.v1 import AppTest

from dashboard.query_service import DashboardQueryService
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
    # Streamlit runs the bodies of all st.tabs during each script execution.
    # Empty tables must not trigger hidden tab exceptions.
    engine.dispose()
    st.cache_resource.clear()
