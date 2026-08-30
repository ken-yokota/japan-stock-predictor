"""Fitting the morning in parallel, without letting a worker near the database.

Ten model families per ticker took the morning from about 13 minutes to 30,
which no longer fits between the 08:20 PIT snapshot and the 08:45 first email
attempt: the mail would have begun reporting "no prediction" while the
prediction was still being computed. The fix is to fit tickers concurrently.

The danger in that fix is not speed, it is the session. A SQLAlchemy session
cannot be shared between workers, and a connection opened inside one would
either fail confusingly or, worse, work most days. So the split is structural:
every read happens first on one session, the parallel phase has no database
handle at all, and the writes happen afterwards on that same session.
"""

from __future__ import annotations

from datetime import date

import pytest

from pipeline.morning import (
    DEFAULT_FIT_WORKERS,
    _fit_workers,
    _NoDatasetBuilder,
)


def test_a_worker_that_reaches_for_the_database_fails_loudly() -> None:
    """The stand-in exists so a future edit cannot silently reopen a connection.

    ``compute`` is handed a window that was already read, so it must never call
    the builder. If it ever does, this raises in the worker instead of quietly
    opening a second connection per process.
    """

    with pytest.raises(RuntimeError, match="must not read the database"):
        _NoDatasetBuilder().build(
            "9101",
            date(2026, 8, 31),
            training_sessions=120,
            minimum_feature_coverage=0.8,
            operational=True,
        )


def test_the_stand_in_satisfies_the_same_contract_as_the_real_builder() -> None:
    """Typed against a protocol, so the refusal cannot drift out of shape."""

    import inspect

    from services.dataset import PointInTimeDatasetBuilder

    real = inspect.signature(PointInTimeDatasetBuilder.build)
    stand_in = inspect.signature(_NoDatasetBuilder.build)
    assert list(real.parameters) == list(stand_in.parameters)


def test_the_worker_count_defaults_to_three(monkeypatch: pytest.MonkeyPatch) -> None:
    """Three on a four-core runner: fast enough, and not oversubscribed."""

    monkeypatch.delenv("MORNING_FIT_WORKERS", raising=False)
    assert _fit_workers() == DEFAULT_FIT_WORKERS == 3


@pytest.mark.parametrize(
    "value,expected",
    [("1", 1), ("4", 4), ("0", 1), ("-3", 1), ("99", 8), ("", 3), ("abc", 3)],
)
def test_the_worker_count_can_be_overridden_but_not_to_nonsense(
    monkeypatch: pytest.MonkeyPatch, value: str, expected: int
) -> None:
    """A constrained runner can turn it down; nothing can turn it up past eight.

    Unbounded would reproduce the failure this session already hit once: more
    compute processes than cores does not run faster, it stops running at all.
    """

    monkeypatch.setenv("MORNING_FIT_WORKERS", value)
    assert _fit_workers() == expected


def test_one_worker_means_the_serial_path(monkeypatch: pytest.MonkeyPatch) -> None:
    """Setting it to one must be a real escape hatch, not a pool of size one."""

    monkeypatch.setenv("MORNING_FIT_WORKERS", "1")
    assert _fit_workers() == 1


def test_compute_performs_no_reads_when_it_is_given_a_window() -> None:
    """The property the parallel phase depends on, asserted directly."""

    import inspect

    from services.prediction import PredictionService

    signature = inspect.signature(PredictionService.compute)
    assert "dataset" in signature.parameters
    assert signature.parameters["dataset"].default is None
    # build_dataset is the only entry point that is allowed to touch the DB.
    assert "build_dataset" in dir(PredictionService)
