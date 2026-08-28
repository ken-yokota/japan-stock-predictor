"""The audit has to find a leak, and has to admit when it checked nothing.

Both halves matter and the second is the one that went wrong first. Written
naively, the audit collapsed "no violation found" and "no provenance to read"
into a single verdict, and reported a retention boundary as INVALID -- which
reads as a leak having been discovered. Feature history here is evicted after
the two most recent runs because a morning writes about 133 MB into a 512 MB
database, so most published sessions have nothing left to check, and saying so
is the whole point.
"""

from __future__ import annotations

import os
from datetime import UTC, date, datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from database.models import (
    Base,
    DailyRun,
    FeatureInput,
    FeatureSet,
    FeatureValue,
    MarketData,
    Prediction,
    PredictionSet,
)
from scripts.audit_leakage import COLUMNS, audit

CUTOFF = datetime(2026, 8, 27, 8, 30, tzinfo=UTC) - timedelta(hours=9)
DAY = date(2026, 8, 27)


def _engine() -> Engine | None:
    # Its own database, for the reason the other PostgreSQL suites carry one:
    # a shared database means each module's drop_all runs against connections
    # another module still holds, and the failures look like flakes.
    url = os.environ.get("TEST_LEAKAGE_POSTGRES_URL") or (
        "postgresql+psycopg://yokotaken@localhost:5432/jsp_leakage_test"
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


def _chain(
    engine: Engine,
    *,
    ticker: str = "7203",
    with_provenance: bool = True,
    retrieved_at: datetime | None = None,
    cutoff: datetime = CUTOFF,
) -> None:
    """One published prediction, optionally with its input provenance."""

    stamp = retrieved_at or (cutoff - timedelta(minutes=30))
    # The set-level maxima have their own CHECK against the cutoff, so a late
    # input row cannot also be summarised as late -- which is the shape a real
    # leak would have to take to get past the schema: the row-level provenance
    # says one thing and the summary above it says another. Keeping the summary
    # compliant is what makes the row-level check worth running at all.
    summary = min(stamp, cutoff)
    row_id = 10_000 + abs(hash(ticker)) % 10_000
    with Session(engine) as session:
        if session.get(DailyRun, "run-1") is None:
            session.add(
                DailyRun(
                    run_id="run-1",
                    run_type="MORNING",
                    prediction_date=DAY,
                    started_at=cutoff,
                    status="SUCCEEDED",
                    data_version="v1",
                )
            )
            session.add(
                PredictionSet(
                    prediction_set_id="set-1",
                    run_id="run-1",
                    prediction_date=DAY,
                    cutoff_at=cutoff,
                    status="READY",
                    feature_version="f1",
                    model_version="m1",
                    strategy_version="s1",
                    training_start=date(2026, 1, 6),
                    training_end=date(2026, 8, 26),
                    generated_at=cutoff,
                    idempotency_key="key-set-1",
                )
            )
            session.flush()
        session.add(
            FeatureSet(
                feature_set_id=f"fs-{ticker}",
                run_id="run-1",
                ticker=ticker,
                prediction_date=DAY,
                cutoff_at=cutoff,
                feature_version="f1",
                set_kind="MORNING",
                training_start=date(2026, 1, 6),
                training_end=date(2026, 8, 26),
                config_hash="h",
                status="READY",
                required_feature_count=1,
                missing_feature_count=0,
                missing_ratio=0.0,
                created_at=cutoff,
                idempotency_key=f"key-fs-{ticker}",
                max_available_timestamp=summary if with_provenance else None,
                max_first_observed_at=summary if with_provenance else None,
                max_retrieved_at=summary if with_provenance else None,
            )
        )
        session.flush()
        session.add(
            Prediction(
                prediction_id=f"pred-{ticker}",
                prediction_set_id="set-1",
                ticker=ticker,
                feature_set_id=f"fs-{ticker}",
                status="INSUFFICIENT_DATA",
                reference_basis="PREV_CLOSE",
                signal="NONE",
                return_threshold=0.003,
                probability_threshold=0.6,
                created_at=cutoff,
                idempotency_key=f"key-pred-{ticker}",
            )
        )
        if with_provenance:
            session.add(
                MarketData(
                    id=row_id,
                    canonical_symbol="SPY",
                    symbol="SPY",
                    provider="yahoo",
                    market="US",
                    market_timezone="America/New_York",
                    market_date=DAY,
                    timestamp=stamp - timedelta(hours=1),
                    available_timestamp=stamp,
                    first_observed_at=stamp,
                    retrieved_at=stamp,
                    last_seen_at=stamp,
                    interval="1d",
                    availability_method="CLOSE",
                    data_quality="OK",
                    close=100.0,
                    raw_hash=f"{ticker:_>64}"[:64],
                )
            )
            session.flush()
            session.add(
                FeatureValue(
                    feature_value_id=row_id,
                    feature_set_id=f"fs-{ticker}",
                    sample_date=DAY,
                    sample_cutoff_at=cutoff,
                    row_role="SCORE",
                    value_kind="FEATURE",
                    feature_name="spy_return",
                    is_missing=False,
                    value=0.01,
                    created_at=cutoff,
                    available_timestamp=summary,
                )
            )
            session.flush()
            session.add(
                FeatureInput(
                    feature_value_id=row_id,
                    input_role="SPY_CLOSE",
                    source_type="MARKET_DATA",
                    source_row_id=row_id,
                    market_data_id=row_id,
                    raw_hash=f"{ticker:_>64}"[:64],
                    available_timestamp=stamp,
                    first_observed_at=stamp,
                    retrieved_at=stamp,
                    created_at=cutoff,
                )
            )
        session.commit()


def test_a_clean_day_passes_with_every_prediction_accounted_for(
    postgres: Engine,
) -> None:
    _chain(postgres)

    result = audit(postgres)

    assert result.verdict == "NO LEAKAGE DETECTED"
    assert result.verified_predictions == 1
    assert result.unchecked_predictions == []


def test_a_row_fetched_after_the_cutoff_makes_the_record_invalid(
    postgres: Engine,
) -> None:
    """The case one timestamp hides: published in time, fetched too late."""

    _chain(postgres, retrieved_at=CUTOFF + timedelta(minutes=1))

    result = audit(postgres)

    assert result.verdict == "INVALID"
    columns = {violation.column for violation in result.violations}
    assert "retrieved_at" in columns
    assert all(v.late_by_minutes > 0 for v in result.violations)


def test_one_late_row_among_many_clean_ones_still_invalidates(
    postgres: Engine,
) -> None:
    """A leak is not diluted by the predictions that did not have one."""

    _chain(postgres, ticker="7203")
    _chain(postgres, ticker="6758")
    _chain(postgres, ticker="8058", retrieved_at=CUTOFF + timedelta(minutes=5))

    result = audit(postgres)

    assert result.verdict == "INVALID"
    # One row, reported once per timestamp that was late -- the column is the
    # diagnosis, so they are not collapsed.
    assert {v.ticker for v in result.violations} == {"8058"}
    assert {v.column for v in result.violations} == set(COLUMNS)


def test_a_session_whose_provenance_was_evicted_is_not_counted_as_clean(
    postgres: Engine,
) -> None:
    """Retention removes the evidence; it does not create a pass."""

    _chain(postgres, ticker="7203")
    _chain(postgres, ticker="6758", with_provenance=False)

    result = audit(postgres)

    assert result.verdict == "VERIFIED WITHIN RETENTION"
    assert result.verified_predictions == 1
    assert result.unchecked_predictions == [f"{DAY} 6758"]


def test_a_cutoff_that_is_not_the_declared_one_is_a_failure(
    postgres: Engine,
) -> None:
    """Auditing a set against its own convenient cutoff proves nothing."""

    _chain(postgres, cutoff=CUTOFF + timedelta(hours=2))

    result = audit(postgres)

    assert result.verdict == "INVALID"
    assert result.wrong_cutoff


def test_an_empty_database_reports_nothing_verified_rather_than_success(
    postgres: Engine,
) -> None:
    result = audit(postgres)

    assert result.predictions == 0
    assert result.verified_dates == ()
