"""Shared runtime construction for command-line entry points."""

from __future__ import annotations

from pathlib import Path

from sqlalchemy import Engine
from sqlalchemy.orm import Session, sessionmaker

from data.config import AppConfig, load_app_config
from data.env import EnvironmentSettings
from database.connection import create_database_engine, create_session_factory


def load_runtime(
    config_dir: Path,
) -> tuple[
    AppConfig,
    EnvironmentSettings,
    Engine,
    sessionmaker[Session],
]:
    config = load_app_config(config_dir)
    environment = EnvironmentSettings()
    engine = create_database_engine(environment.require_database_url())
    return config, environment, engine, create_session_factory(engine)
