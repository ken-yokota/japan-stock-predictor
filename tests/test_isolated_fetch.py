"""A hung symbol must cost one symbol, not the run.

Measured on 2026-08-15: after 69 downloads in an hour, a single `^GSPC` request
sat for four minutes and forty seconds on 1.1 seconds of CPU, and a `SIGALRM`
handler set to twenty seconds never fired. `yfinance` blocks inside `curl_cffi`
below Python, so no in-process timeout can end that wait - only a parent
holding SIGKILL can.

These pin the properties that follow from that, including the ones that are
about restraint rather than mechanism: a throttled host must not be retried
into a ban, work already on disk must not be re-requested, and a symbol that
failed must arrive downstream as failed rather than as a filled-in number.
"""

from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path

import pandas as pd
import pytest

from research import isolated_fetch
from research.cache_state import (
    CACHED,
    CORRUPT,
    MISSING,
    PARTIAL,
    STALE,
    inspect_symbol,
)
from research.isolated_fetch import (
    MAX_ATTEMPTS,
    OK,
    SKIPPED_CACHED,
    TIMEOUT,
    fetch_missing,
    fetch_symbol,
)

START = date(2026, 1, 6)
END = date(2026, 8, 14)


def _write_cache(directory: Path, symbol: str, start: date, end: date) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(
        {
            "market_date": [start.isoformat()],
            "open": [1.0],
            "high": [1.0],
            "low": [1.0],
            "close": [1.0],
            "volume": [1],
        }
    ).to_csv(directory / f"{symbol}.csv", index=False)
    (directory / f"{symbol}.json").write_text(
        json.dumps(
            {"symbol": symbol, "start": start.isoformat(), "end": end.isoformat()}
        ),
        encoding="utf-8",
    )


def _hanging(seconds: float = 30.0):
    def build(symbol, start, end, cache_dir):
        del symbol, start, end, cache_dir
        return [sys.executable, "-c", f"import time; time.sleep({seconds})"]

    return build


def _succeeding(rows: int = 120):
    def build(symbol, start, end, cache_dir):
        del start, end, cache_dir
        payload = json.dumps({"symbol": symbol, "status": "OK", "rows": rows})
        return [sys.executable, "-c", f"print({payload!r})"]

    return build


# --- the hard timeout ------------------------------------------------------


def test_a_hung_child_is_killed_and_reported(tmp_path: Path) -> None:
    """The whole point: the wait ends because the parent ends it."""

    outcome = fetch_symbol(
        "^GSPC",
        START,
        END,
        cache_dir=tmp_path,
        timeout_seconds=1.0,
        build_command=_hanging(30.0),
    )
    assert outcome.status == TIMEOUT
    assert outcome.seconds < 15.0, "must not have waited for the child to finish"
    assert not outcome.succeeded


def test_a_timeout_is_not_retried(tmp_path: Path) -> None:
    """A host that hangs will hang again; pressing it is how a throttle sticks."""

    outcome = fetch_symbol(
        "^GSPC",
        START,
        END,
        cache_dir=tmp_path,
        timeout_seconds=1.0,
        attempts=3,
        build_command=_hanging(30.0),
    )
    assert outcome.status == TIMEOUT
    assert outcome.attempts == 1


def test_attempts_are_capped_however_many_are_asked_for(tmp_path: Path) -> None:
    calls: list[str] = []

    def build(symbol, start, end, cache_dir):
        del start, end, cache_dir
        calls.append(symbol)
        return [sys.executable, "-c", "raise SystemExit(1)"]

    fetch_symbol(
        "^GSPC",
        START,
        END,
        cache_dir=tmp_path,
        timeout_seconds=10.0,
        attempts=99,
        build_command=build,
    )
    assert len(calls) == MAX_ATTEMPTS, "unbounded retry is how a throttle becomes a ban"


def test_one_attempt_is_the_default(tmp_path: Path) -> None:
    calls: list[str] = []

    def build(symbol, start, end, cache_dir):
        del start, end, cache_dir
        calls.append(symbol)
        return [sys.executable, "-c", "raise SystemExit(1)"]

    fetch_symbol(
        "X", START, END, cache_dir=tmp_path, timeout_seconds=10.0, build_command=build
    )
    assert len(calls) == 1


# --- not asking for what is already held -----------------------------------


def test_a_cached_symbol_is_never_requested(tmp_path: Path) -> None:
    """One series is one request whether it wants five days or five hundred."""

    _write_cache(tmp_path, "SPY", START, END)
    requested: list[str] = []

    def build(symbol, start, end, cache_dir):
        requested.append(symbol)
        return _succeeding()(symbol, start, end, cache_dir)

    outcomes, states = fetch_missing(
        ["SPY"],
        START,
        END,
        cache_dir=tmp_path,
        build_command=build,
        sleep=lambda _: None,
    )
    assert requested == []
    assert outcomes[0].status == SKIPPED_CACHED
    assert states[0].status == CACHED


def test_only_the_symbols_the_cache_cannot_satisfy_are_requested(
    tmp_path: Path,
) -> None:
    _write_cache(tmp_path, "SPY", START, END)
    _write_cache(tmp_path, "QQQ", START, date(2026, 6, 1))  # stale
    requested: list[str] = []

    def build(symbol, start, end, cache_dir):
        requested.append(symbol)
        return _succeeding()(symbol, start, end, cache_dir)

    outcomes, _ = fetch_missing(
        ["SPY", "QQQ", "^GSPC"],
        START,
        END,
        cache_dir=tmp_path,
        build_command=build,
        sleep=lambda _: None,
    )
    assert requested == ["QQQ", "^GSPC"]
    assert [o.status for o in outcomes] == [SKIPPED_CACHED, OK, OK]


# --- partial success and fail-closed ---------------------------------------


def test_one_hung_symbol_does_not_lose_the_others(tmp_path: Path) -> None:
    """A batch that dies halfway must keep everything that already landed."""

    def build(symbol, start, end, cache_dir):
        if symbol == "^GSPC":
            return _hanging(30.0)(symbol, start, end, cache_dir)
        return _succeeding()(symbol, start, end, cache_dir)

    outcomes, _ = fetch_missing(
        ["^NDX", "^GSPC", "^DJI"],
        START,
        END,
        cache_dir=tmp_path,
        timeout_seconds=1.0,
        build_command=build,
        sleep=lambda _: None,
    )
    by_symbol = {o.symbol: o.status for o in outcomes}
    assert by_symbol == {"^NDX": OK, "^GSPC": TIMEOUT, "^DJI": OK}


def test_a_failure_carries_no_rows_to_be_mistaken_for_data(tmp_path: Path) -> None:
    """Fail closed: nothing downstream may read a timeout as a value."""

    outcome = fetch_symbol(
        "^GSPC",
        START,
        END,
        cache_dir=tmp_path,
        timeout_seconds=1.0,
        build_command=_hanging(30.0),
    )
    assert outcome.rows == 0
    assert not outcome.succeeded
    # And nothing was written for it.
    assert not (tmp_path / "^GSPC.csv").exists()


def test_requests_are_spaced_so_a_run_is_not_a_burst(tmp_path: Path) -> None:
    pauses: list[float] = []
    fetch_missing(
        ["A", "B", "C"],
        START,
        END,
        cache_dir=tmp_path,
        build_command=_succeeding(),
        sleep=pauses.append,
    )
    # One pause between requests, none before the first.
    assert len(pauses) == 2
    assert all(pause > 0 for pause in pauses)


# --- cache classification --------------------------------------------------


def test_cache_states_are_distinguished(tmp_path: Path) -> None:
    _write_cache(tmp_path, "FULL", START, END)
    _write_cache(tmp_path, "LATE", date(2026, 3, 1), END)
    _write_cache(tmp_path, "OLD", START, date(2026, 6, 1))
    (tmp_path / "BROKEN.csv").write_text("nonsense\n", encoding="utf-8")
    (tmp_path / "BROKEN.json").write_text("{}", encoding="utf-8")

    def status(symbol: str) -> str:
        return inspect_symbol(symbol, START, END, cache_dir=tmp_path).status

    assert status("FULL") == CACHED
    assert status("LATE") == PARTIAL
    assert status("OLD") == STALE
    assert status("BROKEN") == CORRUPT
    assert status("ABSENT") == MISSING


def test_only_a_covering_entry_avoids_a_request(tmp_path: Path) -> None:
    _write_cache(tmp_path, "OLD", START, date(2026, 6, 1))
    assert inspect_symbol("OLD", START, END, cache_dir=tmp_path).needs_fetch


@pytest.mark.parametrize("status", [CACHED])
def test_a_covering_entry_needs_nothing(tmp_path: Path, status: str) -> None:
    _write_cache(tmp_path, "FULL", START, END)
    state = inspect_symbol("FULL", START, END, cache_dir=tmp_path)
    assert state.status == status
    assert not state.needs_fetch


def test_the_child_module_is_importable_without_touching_the_network() -> None:
    """The parent builds a command against it, so it must at least import."""

    assert isolated_fetch._default_command("X", START, END, Path("/tmp"))[1] == "-m"
