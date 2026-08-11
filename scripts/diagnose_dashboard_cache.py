"""Find what in a dashboard read cannot be cached, on the deployed interpreter.

Streamlit Community Cloud redacts the real error behind
``UnserializableReturnValueError``, so the browser only ever says that
something in the return value could not be pickled - never what. Every page
dies on its first ``@st.cache_data`` call, which makes the whole app unusable
while telling nobody why.

This runs the same reads against the same database and pickles each one, then
walks the value to name the exact field and type that failed.

This repository is public, so its Actions logs are public: the report prints
type names and field names only. No value from the database is ever printed.
"""

from __future__ import annotations

import argparse
import os
import pickle
import sys
from collections.abc import Mapping, Sequence
from typing import Any

from sqlalchemy.exc import SQLAlchemyError

from dashboard import DashboardQueryService
from dashboard.types import QueryResult
from database.connection import create_database_engine

# Every read the pages make, in the order a page would reach them.
QUERIES: tuple[str, ...] = (
    "database_health",
    "latest_run",
    "latest_prediction_set",
    "today_predictions",
    "prediction_history",
    "published_prediction_history",
    "actual_results",
    "latest_metrics",
    "model_coefficients",
    "applied_buy_thresholds",
    "simulated_trades",
    "provider_selections",
    "ingestion_batches",
    "run_steps",
    "raw_data_summary",
    "oos_scenario_rows",
)


def _type_name(value: object) -> str:
    kind = type(value)
    module = getattr(kind, "__module__", "?")
    return f"{module}.{kind.__qualname__}"


def _picklable(value: object) -> bool:
    try:
        pickle.dumps(value)
    except Exception:  # any failure at all means "not cacheable"
        return False
    return True


def _walk(value: object, path: str, found: list[str], depth: int = 0) -> None:
    """Record the deepest paths whose value cannot be pickled."""

    if depth > 6 or _picklable(value):
        return

    if isinstance(value, QueryResult):
        _walk(value.state, f"{path}.state", found, depth + 1)
        for index, row in enumerate(value.rows):
            _walk(row, f"{path}.rows[{index}]", found, depth + 1)
            if len(found) > 8:
                return
        return

    if isinstance(value, Mapping):
        for key, item in value.items():
            _walk(item, f"{path}[{key!s}]", found, depth + 1)
            if len(found) > 8:
                return
        return

    if isinstance(value, Sequence) and not isinstance(value, str | bytes):
        for index, item in enumerate(value):
            _walk(item, f"{path}[{index}]", found, depth + 1)
            if len(found) > 8:
                return
        return

    # A leaf that cannot be pickled: this is the answer.
    reason = ""
    try:
        pickle.dumps(value)
    except Exception as error:
        reason = f"{type(error).__name__}: {error}"
    found.append(f"{path}  type={_type_name(value)}  {reason}")


def diagnose(service: DashboardQueryService, names: Sequence[str]) -> int:
    failures = 0
    print(f"python              : {sys.version.split()[0]}")
    print(f"pickle protocol max : {pickle.HIGHEST_PROTOCOL}")
    print("")
    header = f"{'query':30} {'state':16} {'rows':>6}  cacheable"
    print(header)
    print("-" * len(header))

    for name in names:
        method = getattr(service, name, None)
        if method is None:
            print(f"{name:30} {'NO SUCH METHOD':16} {'-':>6}  -")
            continue
        try:
            result: Any = method()
        except SQLAlchemyError:
            print(f"{name:30} {'READ FAILED':16} {'-':>6}  -")
            failures += 1
            continue
        except TypeError:
            # Needs an argument; those are exercised through their callers.
            print(f"{name:30} {'NEEDS ARGS':16} {'-':>6}  skipped")
            continue

        rows = len(result.rows) if isinstance(result, QueryResult) else 0
        state = str(result.state) if isinstance(result, QueryResult) else "-"
        ok = _picklable(result)
        print(f"{name:30} {state:16} {rows:>6}  {'yes' if ok else 'NO'}")
        if not ok:
            failures += 1
            found: list[str] = []
            _walk(result, name, found)
            for line in found[:8] or ["(no unpicklable leaf found by the walk)"]:
                print(f"    {line}")
            # Which column names carry that type, so the fix has a target.
            if isinstance(result, QueryResult) and result.rows:
                offenders = {
                    key: _type_name(item)
                    for key, item in result.rows[0].items()
                    if not _picklable(item)
                }
                if offenders:
                    print(f"    columns: {offenders}")
    return failures


def through_streamlit(service: DashboardQueryService, names: Sequence[str]) -> int:
    """Reproduce the failure through the real decorator, not through pickle.

    Plain ``pickle.dumps`` succeeding does not clear Streamlit: its cache wraps
    the value in its own record before storing it. If the reads pickle but this
    fails, the fault is in the caching layer or its interpreter, not in the
    data, and that distinction decides the whole fix.
    """

    try:
        import streamlit as st
    except Exception as error:
        print(f"streamlit could not be imported: {type(error).__name__}: {error}")
        return 1

    print("")
    print(f"streamlit           : {getattr(st, '__version__', 'unknown')}")
    header = f"{'query':30} through st.cache_data"
    print(header)
    print("-" * len(header))

    failures = 0
    for name in names:
        method = getattr(service, name, None)
        if method is None:
            continue

        @st.cache_data(ttl=60, show_spinner=False)
        def _cached(_call: Any = method) -> Any:
            return _call()

        try:
            _cached()
        except Exception as error:
            failures += 1
            cause = error.__cause__
            print(f"{name:30} FAILED  {type(error).__name__}")
            print(f"    cause: {type(cause).__name__ if cause else 'none'}: {cause}")
        else:
            print(f"{name:30} ok")
    return failures


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--only", nargs="*", default=None, help="query names to run")
    parser.add_argument(
        "--streamlit",
        action="store_true",
        help="also exercise the real @st.cache_data decorator",
    )
    arguments = parser.parse_args(argv)

    database_url = os.environ.get("DATABASE_URL", "").strip()
    if not database_url:
        print("DATABASE_URL is not set", file=sys.stderr)
        return 2

    engine = create_database_engine(database_url)
    service = DashboardQueryService(engine)
    names = arguments.only or QUERIES
    failures = diagnose(service, names)
    print("")
    print(f"uncacheable reads (plain pickle): {failures}")
    if arguments.streamlit:
        through = through_streamlit(service, names)
        print("")
        print(f"uncacheable reads (st.cache_data): {through}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
