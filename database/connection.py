"""SQLAlchemy engine and session construction."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.orm import Session, sessionmaker


def normalize_database_url(url: str) -> str:
    """Normalize common hosted PostgreSQL URLs to psycopg 3."""

    if url.startswith("postgres://"):
        return "postgresql+psycopg://" + url.removeprefix("postgres://")
    if url.startswith("postgresql://"):
        return "postgresql+psycopg://" + url.removeprefix("postgresql://")
    return url


def create_database_engine(
    url: str,
    *,
    allow_sqlite: bool = False,
    echo: bool = False,
) -> Engine:
    """Create a production PostgreSQL engine (SQLite is test-only)."""

    normalized = normalize_database_url(url)
    if not normalized.startswith("postgresql+psycopg://") and not (
        allow_sqlite and normalized.startswith("sqlite")
    ):
        raise ValueError("DATABASE_URL must use PostgreSQL with psycopg")
    kwargs: dict[str, object] = {"pool_pre_ping": True, "echo": echo}
    if normalized.startswith("sqlite"):
        kwargs.pop("pool_pre_ping")
    engine = create_engine(normalized, **kwargs)
    if not normalized.startswith("sqlite"):
        _pin_search_path(engine)
    return engine


def _pin_search_path(engine: Engine) -> None:
    """Set ``search_path`` on every new PostgreSQL connection.

    Some hosted providers (Neon among them) issue roles with an empty
    ``search_path``, so unqualified table names resolve nowhere and DDL fails
    with "no schema has been selected to create in".  This is applied as a
    post-connect ``SET`` rather than a libpq ``options`` startup parameter
    because connection poolers reject unknown startup parameters outright.
    """

    @event.listens_for(engine, "connect")
    def _set_search_path(dbapi_connection: object, _record: object) -> None:
        cursor = dbapi_connection.cursor()  # type: ignore[attr-defined]
        try:
            cursor.execute("SET search_path TO public")
        finally:
            cursor.close()


def create_session_factory(engine: Engine) -> sessionmaker[Session]:
    """Return short-lived SQLAlchemy 2 sessions."""

    return sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)


@contextmanager
def session_scope(factory: sessionmaker[Session]) -> Iterator[Session]:
    """Commit on success and roll back on failure."""

    session = factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
