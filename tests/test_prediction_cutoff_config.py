"""The cutoff comes from the settings file, not from a Python default.

``config/settings.yaml`` declares ``prediction_cutoff: "08:30"`` and
``data/availability.prediction_cutoff`` defaulted to the same string. The two
agreed by coincidence, not by construction: editing the settings file moved
nothing, so the configuration was free to describe a system that did not exist.

These pin the wiring rather than the value, so the day the operator changes
08:30 the code follows - and a production call that quietly falls back to the
default fails here instead of in a published prediction.
"""

from __future__ import annotations

import ast
import pathlib
from datetime import date, datetime
from zoneinfo import ZoneInfo

import pytest

from data.availability import prediction_cutoff
from data.config import load_app_config

JST = ZoneInfo("Asia/Tokyo")
PREDICTION_DATE = date(2026, 8, 12)
REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent

# Where a cutoff is decided for real work. Tests deliberately keep using the
# default, so they are not scanned.
PRODUCTION_PACKAGES = (
    "pipeline",
    "services",
    "data",
    "scripts",
    "models",
    "features",
    "database",
)


def test_the_shipped_settings_and_the_code_agree_today() -> None:
    config = load_app_config()
    assert config.settings.schedule.prediction_cutoff == "08:30"
    cutoff = prediction_cutoff(
        PREDICTION_DATE,
        cutoff_time=config.settings.schedule.prediction_cutoff,
        timezone_name=config.settings.application.timezone,
    )
    assert datetime(2026, 8, 12, 8, 30, tzinfo=JST) == cutoff


def test_a_different_configured_cutoff_moves_the_cutoff() -> None:
    """The value has to travel; a default that cannot be overridden is a lie."""

    cutoff = prediction_cutoff(
        PREDICTION_DATE, cutoff_time="08:15", timezone_name="Asia/Tokyo"
    )
    assert datetime(2026, 8, 12, 8, 15, tzinfo=JST) == cutoff


@pytest.mark.parametrize("started_at_hour", [8, 9, 12, 23])
def test_the_execution_hour_never_reaches_the_cutoff(started_at_hour: int) -> None:
    """Whatever time the job actually starts, the cutoff is the date's."""

    del started_at_hour
    config = load_app_config()
    assert datetime(2026, 8, 12, 8, 30, tzinfo=JST) == prediction_cutoff(
        PREDICTION_DATE,
        cutoff_time=config.settings.schedule.prediction_cutoff,
        timezone_name=config.settings.application.timezone,
    )


def _production_sources() -> list[pathlib.Path]:
    files: list[pathlib.Path] = []
    for package in PRODUCTION_PACKAGES:
        files.extend(sorted((REPO_ROOT / package).rglob("*.py")))
    return files


def test_no_production_call_relies_on_the_default_cutoff() -> None:
    """Every real call passes the configured value explicitly.

    A bare ``prediction_cutoff(day)`` reintroduces exactly the divergence this
    change removed, and it would be invisible until the settings file was
    edited and nothing happened.
    """

    offenders: list[str] = []
    for path in _production_sources():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            name = getattr(node.func, "id", None) or getattr(node.func, "attr", None)
            if name != "prediction_cutoff":
                continue
            keywords = {keyword.arg for keyword in node.keywords}
            if "cutoff_time" not in keywords or "timezone_name" not in keywords:
                relative = path.relative_to(REPO_ROOT)
                offenders.append(f"{relative}:{node.lineno}")

    assert not offenders, (
        "these calls fall back to the Python default instead of the settings "
        "file: " + ", ".join(offenders)
    )
