"""Disk-cached daily bars for the research code path.

One series is one HTTP call no matter how short the requested range, so the
only way to cut Yahoo traffic is to not ask again. A comparison run fits the
same 22 stocks under two feature sets over three windows; without a cache that
is the same download six times over.

The cache is deliberately unusable for anything live: a range whose end is not
strictly in the past is refetched every time, because that day's bar is still
forming.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

import pandas as pd

DEFAULT_CACHE_DIR = Path("artifacts/cache/yahoo")
BAR_COLUMNS: tuple[str, ...] = ("market_date", "open", "high", "low", "close", "volume")


def _safe_name(symbol: str) -> str:
    return "".join(character if character.isalnum() else "_" for character in symbol)


@dataclass(frozen=True, slots=True)
class _CacheEntry:
    frame: pd.DataFrame
    start: date
    end: date

    def covers(self, start: date, end: date) -> bool:
        return self.start <= start and self.end >= end


def _read_cache(directory: Path, symbol: str) -> _CacheEntry | None:
    stem = directory / _safe_name(symbol)
    data_path = stem.with_suffix(".csv")
    meta_path = stem.with_suffix(".json")
    if not data_path.exists() or not meta_path.exists():
        return None
    try:
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        frame = pd.read_csv(data_path)
    except (OSError, ValueError, json.JSONDecodeError):
        return None
    if not set(BAR_COLUMNS).issubset(frame.columns):
        return None
    frame["market_date"] = pd.to_datetime(frame["market_date"]).dt.date
    try:
        return _CacheEntry(
            frame=frame.loc[:, list(BAR_COLUMNS)],
            start=date.fromisoformat(str(meta["start"])),
            end=date.fromisoformat(str(meta["end"])),
        )
    except (KeyError, TypeError, ValueError):
        return None


def _write_cache(
    directory: Path, symbol: str, frame: pd.DataFrame, start: date, end: date
) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    stem = directory / _safe_name(symbol)
    frame.to_csv(stem.with_suffix(".csv"), index=False)
    stem.with_suffix(".json").write_text(
        json.dumps(
            {"symbol": symbol, "start": start.isoformat(), "end": end.isoformat()}
        ),
        encoding="utf-8",
    )


def _fetch(symbol: str, start: date, end: date) -> pd.DataFrame:
    import yfinance as yf

    frame = yf.Ticker(symbol).history(
        start=start.isoformat(),
        end=(end + timedelta(days=1)).isoformat(),
        interval="1d",
        auto_adjust=False,
    )
    if frame.empty:
        return pd.DataFrame(columns=list(BAR_COLUMNS))
    frame = frame.rename(
        columns={
            "Open": "open",
            "High": "high",
            "Low": "low",
            "Close": "close",
            "Volume": "volume",
        }
    )
    if "volume" not in frame.columns:
        frame["volume"] = pd.NA
    frame["market_date"] = [index.date() for index in frame.index]
    return frame.loc[:, list(BAR_COLUMNS)].reset_index(drop=True)


def download_daily(
    symbol: str,
    start: date,
    end: date,
    *,
    cache_dir: Path | None = DEFAULT_CACHE_DIR,
    today: date | None = None,
) -> pd.DataFrame:
    """Return daily bars for ``symbol`` between ``start`` and ``end`` inclusive.

    Reads from ``cache_dir`` when a previous download already covered the
    requested range. A range extending to today or later always refetches, and
    is never written back, so a partial bar cannot be frozen into the cache.
    """

    now = today or date.today()
    is_final = end < now
    if cache_dir is not None and is_final:
        cached = _read_cache(cache_dir, symbol)
        if cached is not None and cached.covers(start, end):
            frame = cached.frame
            window = frame.loc[
                (frame["market_date"] >= start) & (frame["market_date"] <= end)
            ]
            return window.reset_index(drop=True)

    frame = _fetch(symbol, start, end)
    if cache_dir is not None and is_final and not frame.empty:
        _write_cache(cache_dir, symbol, frame, start, end)
    return frame
