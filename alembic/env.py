"""Alembic environment configured exclusively from DATABASE_URL."""

from __future__ import annotations

import os
from logging.config import fileConfig

from alembic import context
from database.connection import create_database_engine, normalize_database_url
from database.models import Base

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def _database_url() -> str:
    value = os.environ.get("DATABASE_URL", "")
    if not value:
        raise RuntimeError("DATABASE_URL is required for Alembic")
    return normalize_database_url(value)


def run_migrations_offline() -> None:
    context.configure(
        url=_database_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    # Share the application's engine builder so migrations inherit the same
    # search_path handling; a hosted role with an empty search_path would
    # otherwise fail DDL with "no schema has been selected to create in".
    engine = create_database_engine(_database_url(), allow_sqlite=True)
    with engine.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
        )
        with context.begin_transaction():
            context.run_migrations()
    engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
