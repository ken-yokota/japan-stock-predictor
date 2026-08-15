"""Run each Yahoo fetch in a child process the parent can kill outright.

The problem this solves was measured, not anticipated. `yfinance` reaches
`curl_cffi`, which blocks inside C, and a `SIGALRM` handler set to twenty
seconds did not interrupt a `^GSPC` request that then sat for four minutes and
forty seconds using 1.1 seconds of CPU. No in-process timeout can end that
wait. A parent holding SIGKILL can.

The rules the batch follows, each because its absence caused a real failure:

* **One attempt by default.** The rate limit that stopped this work counted
  requests. Retrying a throttled host is how a throttle becomes a ban, so extra
  attempts are opt-in, capped, and spaced.
* **Never re-ask for what is on disk.** One series is one request whether it
  covers five days or five hundred, so the only saving is skipping it.
* **Partial success survives.** Each child writes its own cache entry, so a
  batch that dies halfway keeps everything that already landed.
* **Failure is recorded, never filled.** A symbol that times out is reported as
  timed out. Nothing downstream receives a substituted or interpolated value,
  and the caller decides whether it can proceed without it.
"""

from __future__ import annotations

import json
import subprocess
import sys
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from research.cache_state import CACHED, SymbolState, inspect_symbol

OK = "OK"
EMPTY = "EMPTY"
ERROR = "ERROR"
TIMEOUT = "TIMEOUT"
SKIPPED_CACHED = "SKIPPED_CACHED"

# Long enough for a slow but working response, short enough that a hung symbol
# does not consume the batch. The measured healthy fetch was 2.3 seconds.
DEFAULT_TIMEOUT_SECONDS = 90.0

# A cap, not a target. Two attempts covers a dropped connection; more than that
# is arguing with a rate limiter.
MAX_ATTEMPTS = 3
DEFAULT_ATTEMPTS = 1

# Space between a batch's requests, so a run cannot become a burst.
DEFAULT_PAUSE_SECONDS = 1.5


@dataclass(frozen=True, slots=True)
class FetchOutcome:
    """What happened to one symbol, including how long it was waited on."""

    symbol: str
    status: str
    seconds: float = 0.0
    rows: int = 0
    attempts: int = 0
    detail: str = ""

    @property
    def succeeded(self) -> bool:
        return self.status in (OK, SKIPPED_CACHED)


def _default_command(symbol: str, start: date, end: date, cache_dir: Path) -> list[str]:
    return [
        sys.executable,
        "-m",
        "research.fetch_one",
        symbol,
        start.isoformat(),
        end.isoformat(),
        "--cache-dir",
        str(cache_dir),
    ]


CommandBuilder = Callable[[str, date, date, Path], Sequence[str]]


def fetch_symbol(
    symbol: str,
    start: date,
    end: date,
    *,
    cache_dir: Path,
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    attempts: int = DEFAULT_ATTEMPTS,
    build_command: CommandBuilder = _default_command,
) -> FetchOutcome:
    """Fetch one symbol in a child process, killing it if it overruns.

    ``subprocess.run`` sends SIGKILL on timeout, which is the point: a signal
    handler inside the child could not have run anyway, because the child is
    blocked below Python.
    """

    attempts = max(1, min(int(attempts), MAX_ATTEMPTS))
    started = time.monotonic()
    last = FetchOutcome(symbol, ERROR, detail="not attempted")

    for attempt in range(1, attempts + 1):
        command = list(build_command(symbol, start, end, cache_dir))
        try:
            completed = subprocess.run(  # noqa: S603 - command is built here
                command,
                capture_output=True,
                text=True,
                timeout=timeout_seconds,
                check=False,
            )
        except subprocess.TimeoutExpired:
            # The child is already killed by the time this is raised.
            last = FetchOutcome(
                symbol,
                TIMEOUT,
                time.monotonic() - started,
                attempts=attempt,
                detail=f"killed after {timeout_seconds:.0f}s",
            )
            break  # A host that hangs will hang again; do not press it.

        payload: dict[str, object] = {}
        for line in reversed(completed.stdout.splitlines()):
            try:
                payload = json.loads(line)
                break
            except json.JSONDecodeError:
                continue

        fallback = OK if completed.returncode == 0 else ERROR
        status = str(payload.get("status") or fallback)
        last = FetchOutcome(
            symbol,
            status,
            time.monotonic() - started,
            rows=int(payload.get("rows") or 0),
            attempts=attempt,
            detail=str(payload.get("detail") or completed.stderr.strip()[:200]),
        )
        if status == OK:
            break
        if attempt < attempts:
            time.sleep(DEFAULT_PAUSE_SECONDS)

    return last


def fetch_missing(
    symbols: Sequence[str],
    start: date,
    end: date,
    *,
    cache_dir: Path,
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    attempts: int = DEFAULT_ATTEMPTS,
    pause_seconds: float = DEFAULT_PAUSE_SECONDS,
    build_command: CommandBuilder = _default_command,
    sleep: Callable[[float], None] = time.sleep,
) -> tuple[list[FetchOutcome], list[SymbolState]]:
    """Fetch only the symbols the cache cannot already satisfy.

    Returns the outcome of every symbol - including the ones skipped, so the
    caller can report what it did *not* ask for - alongside the cache state each
    decision was made from.
    """

    outcomes: list[FetchOutcome] = []
    states: list[SymbolState] = []
    requested = 0
    for symbol in symbols:
        state = inspect_symbol(symbol, start, end, cache_dir=cache_dir)
        states.append(state)
        if state.status == CACHED:
            outcomes.append(
                FetchOutcome(symbol, SKIPPED_CACHED, rows=state.rows, detail="on disk")
            )
            continue
        if requested:
            sleep(pause_seconds)
        requested += 1
        outcomes.append(
            fetch_symbol(
                symbol,
                start,
                end,
                cache_dir=cache_dir,
                timeout_seconds=timeout_seconds,
                attempts=attempts,
                build_command=build_command,
            )
        )
    return outcomes, states
