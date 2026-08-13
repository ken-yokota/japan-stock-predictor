"""Give the hosted project its space back, before a morning cannot run at all.

The database sits at 90% of Neon's 512 MB ceiling and one morning writes about
200 MB, so the next run would abort mid-transaction the way three consecutive
mornings did on 2026-08-11. This buys the room to fix the cause properly.

Two steps, in this order and for a reason. The prune is the existing retention
path rather than new delete statements - the safe set of rows to remove is
already decided and tested there. VACUUM FULL then returns the freed pages to
the filesystem, because a DELETE alone leaves them allocated and the ceiling
counts allocated space, not live rows.

VACUUM FULL takes an exclusive lock and rewrites each table, so it must not run
while a morning does. It also needs room for the rewritten copy, which is why
it runs after the prune rather than before.

Nothing here touches predictions, actuals, trades, metrics, coefficients or raw
market rows. Only derived feature cells are removed, and only outside the kept
dates - the same rows retention deletes on its own every morning.
"""

from __future__ import annotations

import argparse
import os
import sys

from sqlalchemy import create_engine, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import sessionmaker

from database.connection import create_database_engine, normalize_database_url
from services.retention import prune_feature_history

RECLAIMED_TABLES = ("feature_inputs", "feature_values")


def _size(connection: object) -> tuple[int, dict[str, int]]:
    total = int(
        connection.execute(  # type: ignore[attr-defined]
            text("SELECT pg_database_size(current_database())")
        ).scalar()
        or 0
    )
    rows = connection.execute(  # type: ignore[attr-defined]
        text(
            """
            SELECT c.relname, pg_total_relation_size(c.oid)
            FROM pg_class AS c
            JOIN pg_namespace AS n ON n.oid = c.relnamespace
            WHERE n.nspname = 'public' AND c.relkind = 'r'
            ORDER BY pg_total_relation_size(c.oid) DESC
            LIMIT 6
            """
        )
    ).all()
    return total, {str(row[0]): int(row[1]) for row in rows}


def _report(label: str, total: int, tables: dict[str, int]) -> None:
    print(f"--- {label} ---")
    share = total / (512 * 1024 * 1024)
    print(f"  database : {total / 1024 / 1024:8.2f} MB  ({share:.1%})")
    for name, size in tables.items():
        print(f"  {name:22} {size / 1024 / 1024:8.2f} MB")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--keep-dates",
        type=int,
        default=1,
        help="prediction dates of feature history to keep (default 1)",
    )
    parser.add_argument(
        "--skip-vacuum",
        action="store_true",
        help="prune only; leave the freed pages allocated",
    )
    arguments = parser.parse_args(argv)
    if arguments.keep_dates < 1:
        print("--keep-dates must be at least 1", file=sys.stderr)
        return 2

    url = os.environ.get("DATABASE_URL", "").strip()
    if not url:
        print("DATABASE_URL is not set", file=sys.stderr)
        return 2

    engine = create_database_engine(url)
    try:
        with engine.connect() as connection:
            before_total, before_tables = _size(connection)
        _report("before", before_total, before_tables)

        factory = sessionmaker(bind=engine, expire_on_commit=False)
        report = prune_feature_history(factory, keep_dates=arguments.keep_dates)
        print("")
        print(f"pruned feature values : {report.feature_values:,}")
        print(f"pruned feature inputs : {report.feature_inputs:,}")
        print(f"kept dates            : {[str(day) for day in report.kept_dates]}")
        print(f"removed dates         : {[str(day) for day in report.pruned_dates]}")

        if not arguments.skip_vacuum:
            # VACUUM FULL cannot run inside a transaction, and SQLAlchemy opens
            # one for every connection unless told otherwise.
            vacuum_engine = create_engine(
                normalize_database_url(url), isolation_level="AUTOCOMMIT"
            )
            with vacuum_engine.connect() as connection:
                for table in RECLAIMED_TABLES:
                    print(f"vacuuming {table} ...", flush=True)
                    connection.execute(text(f"VACUUM (FULL, ANALYZE) {table}"))
            vacuum_engine.dispose()

        with engine.connect() as connection:
            after_total, after_tables = _size(connection)
    except SQLAlchemyError:
        # The message can carry the host and the user name, so it is not shown.
        print("database operation failed", file=sys.stderr)
        return 1

    print("")
    _report("after", after_total, after_tables)
    freed = before_total - after_total
    print("")
    print(f"freed : {freed / 1024 / 1024:.2f} MB")
    print(f"headroom now : {(512 * 1024 * 1024 - after_total) / 1024 / 1024:.2f} MB")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
