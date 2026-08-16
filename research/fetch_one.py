"""Fetch exactly one symbol, in a process the parent is willing to kill.

`yfinance` reaches `curl_cffi`, which blocks inside C. A `SIGALRM` handler
cannot interrupt that: measured here, a single `^GSPC` request sat for four
minutes and forty seconds with the alarm set to twenty seconds, using 1.1
seconds of CPU. Nothing inside the Python process can end that wait, so the
timeout has to live outside it - in a parent that can send SIGKILL.

This module is that child. It writes its own cache entry on success, so a batch
that dies halfway keeps every symbol that already landed, and it prints one
JSON line the parent reads for status. It never invents a value: an empty
response is reported as empty and written nowhere.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

EMPTY = "EMPTY"
OK = "OK"
ERROR = "ERROR"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("symbol")
    parser.add_argument("start")
    parser.add_argument("end")
    parser.add_argument("--cache-dir", default=None)
    arguments = parser.parse_args(argv)

    from research.history import DEFAULT_CACHE_DIR, download_daily

    cache_dir = (
        Path(arguments.cache_dir) if arguments.cache_dir else DEFAULT_CACHE_DIR
    )
    try:
        frame = download_daily(
            arguments.symbol,
            date.fromisoformat(arguments.start),
            date.fromisoformat(arguments.end),
            cache_dir=cache_dir,
        )
    except Exception as error:
        print(
            json.dumps(
                {
                    "symbol": arguments.symbol,
                    "status": ERROR,
                    "detail": f"{type(error).__name__}: {str(error)[:200]}",
                }
            )
        )
        return 1

    status = OK if not frame.empty else EMPTY
    print(
        json.dumps(
            {"symbol": arguments.symbol, "status": status, "rows": len(frame)}
        )
    )
    return 0 if status == OK else 2


if __name__ == "__main__":
    sys.exit(main())
