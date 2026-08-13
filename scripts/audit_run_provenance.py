"""Which runs produced a day's predictions, and were they live mornings?

A published set says what it predicted; it does not say on whose authority.
2026-08-11 was a JPX holiday and still carries three BUYs, and the difference
between a scheduled morning having run on a closed market and a replay being
labelled correctly is the difference between a P0 defect and a non-event.

Read-only, in a read-only transaction.
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import date

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from data.market_calendar import is_japan_business_day
from database.connection import create_database_engine


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--from-date", default="2026-08-07")
    parser.add_argument("--to-date", default="2026-08-14")
    arguments = parser.parse_args(argv)

    url = os.environ.get("DATABASE_URL", "").strip()
    if not url:
        print("DATABASE_URL is not set", file=sys.stderr)
        return 2

    engine = create_database_engine(url)
    try:
        with engine.connect() as connection:
            connection.execute(text("SET TRANSACTION READ ONLY"))
            runs = connection.execute(
                text(
                    """
                    SELECT r.prediction_date, r.run_type, r.status,
                           r.started_at, r.finished_at, r.current_step,
                           r.run_id
                    FROM daily_runs AS r
                    WHERE r.prediction_date BETWEEN :a AND :b
                    ORDER BY r.prediction_date, r.started_at
                    """
                ),
                {"a": arguments.from_date, "b": arguments.to_date},
            ).mappings().all()

            sets = connection.execute(
                text(
                    """
                    SELECT ps.prediction_date, ps.status, ps.generated_at,
                           ps.published_at, r.run_type, ps.warnings::text AS w,
                           COUNT(p.prediction_id) AS preds,
                           COUNT(*) FILTER (WHERE p.signal = 'BUY') AS buys
                    FROM prediction_sets AS ps
                    JOIN daily_runs AS r ON r.run_id = ps.run_id
                    LEFT JOIN predictions AS p
                      ON p.prediction_set_id = ps.prediction_set_id
                    WHERE ps.prediction_date BETWEEN :a AND :b
                    GROUP BY ps.prediction_date, ps.status, ps.generated_at,
                             ps.published_at, r.run_type, ps.warnings::text
                    ORDER BY ps.prediction_date, ps.generated_at
                    """
                ),
                {"a": arguments.from_date, "b": arguments.to_date},
            ).mappings().all()
    except SQLAlchemyError:
        print("database read failed", file=sys.stderr)
        return 1

    print("=== daily_runs ===")
    header = f"{'date':12}{'JPX':7}{'run_type':12}{'status':14}{'started(UTC)':22}step"
    print(header)
    print("-" * len(header))
    for row in runs:
        day = row["prediction_date"]
        open_ = "OPEN" if is_japan_business_day(day) else "CLOSED"
        started = str(row["started_at"])[:19]
        print(
            f"{day!s:12}{open_:7}{row['run_type']!s:12}{row['status']!s:14}"
            f"{started:22}{row['current_step'] or '—'}"
        )

    print("")
    print("=== prediction_sets ===")
    header = (
        f"{'date':12}{'JPX':7}{'run_type':10}{'status':10}"
        f"{'preds':>6}{'buys':>6}  generated(UTC)"
    )
    print(header)
    print("-" * len(header))
    for row in sets:
        day = row["prediction_date"]
        open_ = "OPEN" if is_japan_business_day(day) else "CLOSED"
        print(
            f"{day!s:12}{open_:7}{row['run_type']!s:10}{row['status']!s:10}"
            f"{int(row['preds']):>6}{int(row['buys'] or 0):>6}  "
            f"{str(row['generated_at'])[:19]}"
        )
        warnings = str(row["w"] or "")
        if warnings and warnings != "[]":
            print(f"             warnings: {warnings[:150]}")

    print("")
    print("=== JPX calendar check ===")
    for iso in (
        "2026-08-07", "2026-08-10", "2026-08-11", "2026-08-12", "2026-08-13",
    ):
        day = date.fromisoformat(iso)
        state = "OPEN" if is_japan_business_day(day) else "CLOSED"
        print(f"  {iso}  {state}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
