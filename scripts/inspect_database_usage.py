"""Measure where the hosted database's 512 MB actually goes.

Two mornings fill more than half of Neon's free ceiling, and the retention
that was meant to hold it back reports "pruned 0". Before anything is deleted
or any schema is changed, this establishes the facts: which tables and indexes
hold the bytes, how many rows each day adds, and how much of that is the same
row written again.

Read-only by construction - it opens a read-only transaction and issues no
statement that writes. It prints counts, sizes and column names only: this
repository is public, so its Actions logs are public, and no cell value from
the database is ever printed.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Sequence

from sqlalchemy import text
from sqlalchemy.engine import Connection
from sqlalchemy.exc import SQLAlchemyError

from database.connection import create_database_engine

NEON_LIMIT_BYTES = 512 * 1024 * 1024

# Column sets that ought to identify one row of market history. Whichever of
# these a table actually has is used to count how often the same logical row
# was stored more than once.
CANDIDATE_KEYS: tuple[tuple[str, ...], ...] = (
    ("provider", "canonical_symbol", "interval", "market_date"),
    ("provider", "canonical_symbol", "market_date"),
    ("canonical_symbol", "market_date"),
    ("provider", "canonical_symbol", "interval", "market_date", "raw_hash"),
)

DATE_COLUMNS = ("prediction_date", "market_date", "sample_date", "for_date")


def _megabytes(value: int | None) -> str:
    return f"{(value or 0) / 1024 / 1024:8.2f} MB"


def _tables(connection: Connection) -> list[dict[str, object]]:
    rows = (
        connection.execute(
            text(
                """
                SELECT c.relname AS name,
                       pg_total_relation_size(c.oid) AS total_bytes,
                       pg_relation_size(c.oid) AS table_bytes,
                       pg_indexes_size(c.oid) AS index_bytes
                FROM pg_class AS c
                JOIN pg_namespace AS n ON n.oid = c.relnamespace
                WHERE n.nspname = 'public' AND c.relkind = 'r'
                ORDER BY pg_total_relation_size(c.oid) DESC
                """
            )
        )
        .mappings()
        .all()
    )
    return [dict(row) for row in rows]


def _columns(connection: Connection, table: str) -> set[str]:
    rows = connection.execute(
        text(
            """
            SELECT column_name
            FROM information_schema.columns
            WHERE table_schema = 'public' AND table_name = :table
            """
        ),
        {"table": table},
    ).all()
    return {str(row[0]) for row in rows}


def _count(connection: Connection, table: str) -> int:
    statement = text(f'SELECT COUNT(*) FROM "{table}"')
    return int(connection.execute(statement).scalar() or 0)


def _rows_per_date(
    connection: Connection, table: str, column: str, limit: int = 8
) -> list[tuple[str, int]]:
    rows = connection.execute(
        text(
            f"""
            SELECT "{column}" AS d, COUNT(*) AS n
            FROM "{table}"
            GROUP BY 1
            ORDER BY 1 DESC
            LIMIT :limit
            """
        ),
        {"limit": limit},
    ).all()
    return [(str(row[0]), int(row[1])) for row in rows]


def _duplicate_ratio(
    connection: Connection, table: str, key: Sequence[str]
) -> tuple[int, int]:
    quoted = ", ".join(f'"{column}"' for column in key)
    row = connection.execute(
        text(
            f"""
            SELECT COUNT(*) AS total,
                   COUNT(*) FILTER (WHERE TRUE) - COUNT(DISTINCT ({quoted})) AS extra
            FROM "{table}"
            """
        )
    ).one()
    return int(row[0]), int(row[1])


def _indexes(connection: Connection, limit: int = 15) -> list[tuple[str, str, int]]:
    rows = connection.execute(
        text(
            """
            SELECT relname, indexrelname, pg_relation_size(indexrelid) AS bytes
            FROM pg_stat_user_indexes
            ORDER BY pg_relation_size(indexrelid) DESC
            LIMIT :limit
            """
        ),
        {"limit": limit},
    ).all()
    return [(str(row[0]), str(row[1]), int(row[2])) for row in rows]


def report(connection: Connection) -> dict[str, object]:
    total = int(
        connection.execute(text("SELECT pg_database_size(current_database())")).scalar()
        or 0
    )
    share = total / NEON_LIMIT_BYTES
    print(f"database size : {_megabytes(total)}  ({share:.1%} of 512 MB)")
    print("")

    tables = _tables(connection)
    print(f"{'table':26}{'rows':>12}{'total':>14}{'table':>14}{'index':>14}")
    print("-" * 80)
    summary: list[dict[str, object]] = []
    for entry in tables:
        name = str(entry["name"])
        rows = _count(connection, name)
        print(
            f"{name:26}{rows:>12,}"
            f"{_megabytes(int(entry['total_bytes'] or 0)):>14}"
            f"{_megabytes(int(entry['table_bytes'] or 0)):>14}"
            f"{_megabytes(int(entry['index_bytes'] or 0)):>14}"
        )
        summary.append(
            {
                "table": name,
                "rows": rows,
                "total_bytes": int(entry["total_bytes"] or 0),
                "index_bytes": int(entry["index_bytes"] or 0),
            }
        )

    print("")
    print("largest indexes")
    print("-" * 80)
    for table, index, size in _indexes(connection):
        print(f"  {_megabytes(size)}  {table}.{index}")

    print("")
    print("rows added per date (top tables with a date column)")
    print("-" * 80)
    for entry in summary[:10]:
        name = str(entry["table"])
        columns = _columns(connection, name)
        column = next((c for c in DATE_COLUMNS if c in columns), None)
        if column is None:
            continue
        pairs = _rows_per_date(connection, name, column)
        rendered = "  ".join(f"{day}:{count:,}" for day, count in pairs)
        print(f"  {name} ({column})  {rendered}")

    print("")
    print("same logical row stored more than once")
    print("-" * 80)
    duplicates: dict[str, object] = {}
    for entry in summary:
        name = str(entry["table"])
        if entry["rows"] == 0:
            continue
        columns = _columns(connection, name)
        key = next((k for k in CANDIDATE_KEYS if set(k) <= columns), None)
        if key is None:
            continue
        total_rows, extra = _duplicate_ratio(connection, name, key)
        share = extra / total_rows if total_rows else 0.0
        duplicates[name] = {"key": list(key), "rows": total_rows, "extra": extra}
        print(f"  {name:24} key={'+'.join(key)}")
        print(f"    rows={total_rows:,}  duplicate rows={extra:,}  ({share:.1%})")

    return {
        "database_bytes": total,
        "limit_bytes": NEON_LIMIT_BYTES,
        "tables": summary,
        "duplicates": duplicates,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="also emit the raw numbers")
    arguments = parser.parse_args(argv)

    database_url = os.environ.get("DATABASE_URL", "").strip()
    if not database_url:
        print("DATABASE_URL is not set", file=sys.stderr)
        return 2

    engine = create_database_engine(database_url)
    try:
        with engine.connect() as connection:
            # Nothing here writes; say so to the server as well as to the reader.
            connection.execute(text("SET TRANSACTION READ ONLY"))
            payload = report(connection)
    except SQLAlchemyError:
        # The message can carry the host and the user name, so it is not shown.
        print("database read failed", file=sys.stderr)
        return 1

    if arguments.json:
        print("")
        print(json.dumps(payload, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
