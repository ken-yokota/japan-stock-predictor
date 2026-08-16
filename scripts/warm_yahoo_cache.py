"""Audit the Yahoo cache, then request only what it cannot already satisfy.

Two modes, and the default is the harmless one. `--audit` reports and asks for
nothing; adding `--fetch` sends the minimum number of requests, one killable
child per symbol.

Written after a run stalled: 69 downloads in an hour, then a single `^GSPC`
request that sat for four minutes and forty seconds while a twenty-second
`SIGALRM` failed to fire. The lesson kept here is that the cheapest request is
the one not made, so the audit runs first and prints what it intends to ask for
before it asks.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

from research.cache_state import CACHED, inspect_all
from research.feature_sets import FEATURE_SETS, resolve
from research.history import DEFAULT_CACHE_DIR
from research.isolated_fetch import (
    DEFAULT_TIMEOUT_SECONDS,
    SKIPPED_CACHED,
    fetch_missing,
)


def _symbols_for(set_names: list[str]) -> list[str]:
    seen: dict[str, None] = {}
    for name in set_names:
        feature_set = resolve(name)
        for spec in feature_set.indicators:
            seen.setdefault(spec.symbol, None)
        for symbol in feature_set.adr_symbols.values():
            seen.setdefault(symbol, None)
    return list(seen)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sets", default=",".join(FEATURE_SETS))
    parser.add_argument("--symbols", default=None, help="explicit list, overrides sets")
    parser.add_argument("--start", required=True)
    parser.add_argument("--end", required=True)
    parser.add_argument("--cache-dir", default=str(DEFAULT_CACHE_DIR))
    parser.add_argument("--fetch", action="store_true", help="actually request")
    parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT_SECONDS)
    parser.add_argument("--attempts", type=int, default=1)
    parser.add_argument("--output", type=Path, default=None)
    arguments = parser.parse_args(argv)

    start = date.fromisoformat(arguments.start)
    end = date.fromisoformat(arguments.end)
    cache_dir = Path(arguments.cache_dir)
    symbols = (
        [s.strip() for s in str(arguments.symbols).split(",") if s.strip()]
        if arguments.symbols
        else _symbols_for(
            [s.strip() for s in str(arguments.sets).split(",") if s.strip()]
        )
    )

    states = inspect_all(symbols, start, end, cache_dir=cache_dir)
    print(f"window    : {start} .. {end}")
    print(f"cache dir : {cache_dir}")
    print(f"symbols   : {len(symbols)}")
    print("")
    for state in states:
        mark = " " if state.status == CACHED else "*"
        print(f" {mark} {state.symbol:12} {state.status:8} {state.detail}")

    wanted = [state.symbol for state in states if state.needs_fetch]
    print("")
    print(f"already usable : {len(states) - len(wanted)}")
    print(f"would request  : {len(wanted)}  {wanted}")

    if not arguments.fetch:
        print("")
        print("（--fetch を付けるまで、1本も取得しません）")
        return 0
    if not wanted:
        print("")
        print("取得の必要はありません。")
        return 0

    print("")
    print(
        f"requesting {len(wanted)} symbols, "
        f"{arguments.timeout:.0f}s hard timeout ..."
    )
    outcomes, _ = fetch_missing(
        symbols,
        start,
        end,
        cache_dir=cache_dir,
        timeout_seconds=arguments.timeout,
        attempts=arguments.attempts,
    )

    requested = [o for o in outcomes if o.status != SKIPPED_CACHED]
    failed = [o for o in requested if not o.succeeded]
    print("")
    for outcome in requested:
        print(
            f"  {outcome.symbol:12} {outcome.status:8} "
            f"{outcome.seconds:6.1f}s rows={outcome.rows:5} {outcome.detail[:60]}"
        )
    print("")
    print(f"requested : {len(requested)}")
    print(f"succeeded : {len(requested) - len(failed)}")
    print(f"failed    : {len(failed)}  {[o.symbol for o in failed]}")

    if arguments.output:
        arguments.output.write_text(
            json.dumps(
                {
                    "window": [start.isoformat(), end.isoformat()],
                    "requested": [o.symbol for o in requested],
                    "failed": [
                        {"symbol": o.symbol, "status": o.status, "detail": o.detail}
                        for o in failed
                    ],
                    "outcomes": [
                        {
                            "symbol": o.symbol,
                            "status": o.status,
                            "seconds": round(o.seconds, 2),
                            "rows": o.rows,
                        }
                        for o in outcomes
                    ],
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
    # Fail closed: an incomplete warm-up must not read as a successful one.
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
