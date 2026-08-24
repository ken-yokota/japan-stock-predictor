"""The after-close mail has to arrive every weekday, and exactly once.

Two failures are being guarded against here, and they pull in opposite
directions. The mail can fail to go out at all -- a workflow that errors, a
report that cannot be assembled, or a cron GitHub simply does not fire, which
has happened -- and nobody notices, because the signal for "nothing happened"
is an empty inbox. Or the retries added to fix that can mail the operator three
copies, which is how a mailbox stops being read.

So: three scheduled attempts, one delivery record keyed on the date, and a mail
that goes out even when the report behind it could not be built.
"""

from __future__ import annotations

import os
from datetime import UTC, date, datetime
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from database.models import Base
from database.repository import PredictionPipelineRepository
from scripts.send_daily_summary import (
    SUMMARY_TEMPLATE,
    _fallback_report,
    already_delivered,
    summary_idempotency_key,
)
from scripts.verify_daily_delivery import summary_check

WORKFLOWS = Path(".github/workflows")


# --------------------------------------------------------------------------
# Exactly once


def test_the_delivery_key_is_one_per_date() -> None:
    assert summary_idempotency_key(date(2026, 8, 24)) == "daily-summary-2026-08-24"
    assert summary_idempotency_key(date(2026, 8, 24)) != summary_idempotency_key(
        date(2026, 8, 25)
    )


def _engine() -> Engine | None:
    # Its own database. Sharing one with another module means each fixture's
    # drop_all runs against connections the other still holds.
    url = os.environ.get("TEST_DELIVERY_POSTGRES_URL") or (
        "postgresql+psycopg://yokotaken@localhost:5432/jsp_delivery_test"
    )
    try:
        engine = create_engine(url)
        with engine.connect():
            pass
    except Exception:
        return None
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    return engine


@pytest.fixture()
def postgres() -> Engine:
    engine = _engine()
    if engine is None:
        pytest.skip("no local PostgreSQL available")
    return engine


def _register(engine: Engine, day: date) -> None:
    with Session(engine) as session:
        PredictionPipelineRepository(session).create_operational_email_log(
            recipient="operator@example.com",
            template_version=SUMMARY_TEMPLATE,
            subject="【大引け後】テスト",
            idempotency_key=summary_idempotency_key(day),
        )
        session.commit()


def test_a_day_with_no_record_has_not_been_delivered(postgres: Engine) -> None:
    assert already_delivered(postgres, date(2026, 8, 24)) is False


def test_a_registered_but_unsent_mail_does_not_count_as_delivered(
    postgres: Engine,
) -> None:
    """PENDING is not SENT. A retry must still try."""

    _register(postgres, date(2026, 8, 24))

    assert already_delivered(postgres, date(2026, 8, 24)) is False


def test_a_sent_mail_stops_the_next_attempt(postgres: Engine) -> None:
    day = date(2026, 8, 24)
    _register(postgres, day)
    with Session(postgres) as session:
        repository = PredictionPipelineRepository(session)
        assert repository.claim_email(summary_idempotency_key(day)) is True
        repository.mark_email_sent(
            summary_idempotency_key(day),
            provider_message_id="msg-1",
            sent_at=datetime.now(UTC),
        )
        session.commit()

    assert already_delivered(postgres, day) is True
    # ...and the following day is unaffected: the key carries the date.
    assert already_delivered(postgres, date(2026, 8, 25)) is False


def test_registering_twice_returns_the_same_delivery(postgres: Engine) -> None:
    """The second and third cron must find the first one's row, not add another."""

    day = date(2026, 8, 24)
    _register(postgres, day)
    _register(postgres, day)

    with postgres.connect() as connection:
        count = connection.scalar(
            text("select count(*) from email_logs where idempotency_key = :key"),
            {"key": summary_idempotency_key(day)},
        )
    assert count == 1


def test_a_claimed_mail_cannot_be_claimed_again(postgres: Engine) -> None:
    day = date(2026, 8, 24)
    _register(postgres, day)
    with Session(postgres) as session:
        repository = PredictionPipelineRepository(session)
        assert repository.claim_email(summary_idempotency_key(day)) is True
        session.commit()
    with Session(postgres) as session:
        repository = PredictionPipelineRepository(session)
        assert repository.claim_email(summary_idempotency_key(day)) is False


def test_an_operational_mail_needs_no_prediction_set(postgres: Engine) -> None:
    """A JPX holiday has no publication, and is exactly a day not to mail thrice."""

    _register(postgres, date(2026, 8, 22))

    with postgres.connect() as connection:
        set_id = connection.scalar(
            text(
                "select prediction_set_id from email_logs"
                " where idempotency_key = :key"
            ),
            {"key": summary_idempotency_key(date(2026, 8, 22))},
        )
    assert set_id is None


def test_a_conflicting_identity_under_one_key_is_refused(postgres: Engine) -> None:
    day = date(2026, 8, 24)
    _register(postgres, day)
    with Session(postgres) as session, pytest.raises(ValueError):
        PredictionPipelineRepository(session).create_operational_email_log(
            recipient="somebody-else@example.com",
            template_version=SUMMARY_TEMPLATE,
            subject="別のメール",
            idempotency_key=summary_idempotency_key(day),
        )


def test_the_delivery_check_survives_an_unreachable_database() -> None:
    """A database that cannot be read must not be read as "already sent"."""

    engine = create_engine("postgresql+psycopg://nobody@127.0.0.1:1/nothing")

    assert already_delivered(engine, date(2026, 8, 24)) is False
    assert already_delivered(None, date(2026, 8, 24)) is False


# --------------------------------------------------------------------------
# At least once


def test_a_report_that_cannot_be_built_still_produces_a_mail() -> None:
    """Silence is the one outcome the operator cannot detect."""

    subject, text_body, html_body = _fallback_report(
        date(2026, 8, 24), RuntimeError("connection to host 10.0.0.1 failed")
    )

    assert "2026-08-24" in subject
    assert "組み立てられませんでした" in subject
    assert "RuntimeError" in html_body
    assert "できなかったこと" in html_body
    assert "組み立てられませんでした" in text_body


def test_the_fallback_never_repeats_the_exception_message() -> None:
    """A message can carry a host, a user, or a connection string."""

    secret = "postgresql://user:hunter2@db.internal:5432/prod"
    subject, text_body, html_body = _fallback_report(
        date(2026, 8, 24), RuntimeError(secret)
    )

    for body in (subject, text_body, html_body):
        assert secret not in body
        assert "hunter2" not in body


def test_the_watchdog_fails_when_the_summary_never_went_out() -> None:
    assert not summary_check(None).ok
    assert summary_check("2026-08-24T08:54:00").ok


# --------------------------------------------------------------------------
# The migration has to run on both engines


def test_the_migrations_run_on_sqlite_as_well_as_postgresql(tmp_path: Path) -> None:
    """CI verifies the migrations on SQLite, and 0004 failed there first.

    PostgreSQL takes ``ALTER COLUMN ... DROP NOT NULL``; SQLite has no ALTER
    COLUMN at all, so the change has to go through alembic's batch mode, which
    copies the table. Verifying only on the engine production uses passed the
    migration and broke the build. Running it here means the next one fails on
    a laptop instead.
    """

    import subprocess
    import sys

    database = tmp_path / "migration.sqlite3"
    environment = {
        **os.environ,
        "DATABASE_URL": f"sqlite+pysqlite:///{database}",
    }
    for command in (["upgrade", "head"], ["check"]):
        result = subprocess.run(
            [sys.executable, "-m", "alembic", *command],
            capture_output=True,
            text=True,
            env=environment,
            timeout=180,
        )
        assert result.returncode == 0, result.stderr[-2000:]


# --------------------------------------------------------------------------
# The schedule that has to fire


def _crons(filename: str) -> list[str]:
    import re

    source = (WORKFLOWS / filename).read_text(encoding="utf-8")
    return re.findall(r'cron:\s*"([^"]+)"', source)


def _minutes_utc(cron: str) -> list[int]:
    minutes, hours = cron.split()[0], cron.split()[1]
    return sorted(
        int(hour) * 60 + int(minute)
        for hour in hours.split(",")
        for minute in minutes.split(",")
    )


def test_the_after_close_mail_has_more_than_one_chance_to_fire() -> None:
    """GitHub does not guarantee a scheduled run fires; one cron is one hope."""

    crons = _crons("daily_summary.yml")

    assert len(crons) >= 3
    assert all(cron.endswith("1-5") for cron in crons)


def test_the_watchdog_judges_after_the_last_attempt() -> None:
    """A watchdog that runs mid-retry reports a failure that is still in progress."""

    last_attempt = max(
        minute for cron in _crons("daily_summary.yml") for minute in _minutes_utc(cron)
    )
    evening_ticks = [
        minute
        for cron in _crons("delivery_watchdog.yml")
        for minute in _minutes_utc(cron)
        # The morning window's ticks are the ones just after midnight UTC.
        if minute > 6 * 60
    ]

    assert evening_ticks
    assert min(evening_ticks) > last_attempt


def test_the_summary_workflow_migrates_before_it_records_a_delivery() -> None:
    """The delivery record needs 0004; the job must not rely on another workflow."""

    source = (WORKFLOWS / "daily_summary.yml").read_text(encoding="utf-8")

    assert "alembic upgrade head" in source
    assert source.index("alembic upgrade head") < source.index("cli daily-summary")
