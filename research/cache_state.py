"""Say what the cache already holds before asking Yahoo for anything.

Requesting a series that is already on disk is the expensive mistake here: one
series is one HTTP call whether it asks for five days or five hundred, and the
rate limit that stopped this work counted calls, not bytes. So the batch has to
know, per symbol, whether it needs to ask at all.

Five states, because "not usable" hides four different repairs:

``CACHED``   the stored range covers the window; do not ask
``MISSING``  nothing on disk
``PARTIAL``  stored, but starts later than the window needs
``STALE``    stored, but ends before the window needs
``CORRUPT``  present and unreadable, or missing the bar columns

`PARTIAL` and `STALE` are separated on purpose: a stale entry needs only the
recent tail, while a partial one needs history that may no longer be offered.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import pandas as pd

from research.history import BAR_COLUMNS, _safe_name

CACHED = "CACHED"
MISSING = "MISSING"
PARTIAL = "PARTIAL"
STALE = "STALE"
CORRUPT = "CORRUPT"

# Only these need a request; CACHED is finished and CORRUPT is a local repair
# that a refetch happens to perform.
NEEDS_FETCH = (MISSING, PARTIAL, STALE, CORRUPT)


@dataclass(frozen=True, slots=True)
class SymbolState:
    """One symbol's cache entry, judged against the window that is wanted."""

    symbol: str
    status: str
    stored_start: date | None = None
    stored_end: date | None = None
    rows: int = 0
    detail: str = ""

    @property
    def needs_fetch(self) -> bool:
        return self.status in NEEDS_FETCH


def inspect_symbol(
    symbol: str, start: date, end: date, *, cache_dir: Path
) -> SymbolState:
    """Classify one symbol without opening a network connection."""

    stem = cache_dir / _safe_name(symbol)
    data_path = stem.with_suffix(".csv")
    meta_path = stem.with_suffix(".json")
    if not data_path.exists() or not meta_path.exists():
        return SymbolState(symbol, MISSING, detail="no cache entry")

    try:
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        frame = pd.read_csv(data_path)
    except (OSError, ValueError, json.JSONDecodeError) as error:
        return SymbolState(symbol, CORRUPT, detail=type(error).__name__)

    if not set(BAR_COLUMNS).issubset(frame.columns):
        missing = sorted(set(BAR_COLUMNS) - set(frame.columns))
        return SymbolState(symbol, CORRUPT, detail=f"missing columns {missing}")

    try:
        stored_start = date.fromisoformat(str(meta["start"]))
        stored_end = date.fromisoformat(str(meta["end"]))
    except (KeyError, TypeError, ValueError) as error:
        return SymbolState(symbol, CORRUPT, detail=type(error).__name__)

    rows = len(frame)
    if stored_start > start:
        return SymbolState(symbol, PARTIAL, stored_start, stored_end, rows,
                           f"starts {stored_start}, needs {start}")
    if stored_end < end:
        return SymbolState(symbol, STALE, stored_start, stored_end, rows,
                           f"ends {stored_end}, needs {end}")
    return SymbolState(symbol, CACHED, stored_start, stored_end, rows)


def inspect_all(
    symbols: list[str], start: date, end: date, *, cache_dir: Path
) -> list[SymbolState]:
    """Classify every symbol, in the order given, touching no network."""

    return [inspect_symbol(s, start, end, cache_dir=cache_dir) for s in symbols]
