"""How often does a day publish predictions with incomplete inputs?

A required indicator can be entirely absent and the morning still publishes:
the only gate is a global missing-ratio, so five missing series pass unnoticed
as long as the rest of the matrix is dense enough. Whether that should change
depends on how often it happens and on whether those days trade differently -
neither of which is knowable from the code.

Read-only, and it reads only what was already stored: no timestamp is rewritten
and nothing is refetched, so a day that cannot be judged is reported as such
rather than reconstructed.
"""

from __future__ import annotations

import json
import os
import sys

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from database.connection import create_database_engine

COMPLETE = 0.9999


def main() -> int:
    url = os.environ.get("DATABASE_URL", "").strip()
    if not url:
        print("DATABASE_URL is not set", file=sys.stderr)
        return 2
    engine = create_database_engine(url)
    try:
        with engine.connect() as connection:
            connection.execute(text("SET TRANSACTION READ ONLY"))
            rows = (
                connection.execute(
                    text(
                        """
                        SELECT ps.prediction_date AS d,
                               ps.status AS set_status,
                               COUNT(p.prediction_id) AS predictions,
                               COUNT(*) FILTER (WHERE p.signal = 'BUY') AS buys,
                               MIN(p.feature_coverage) AS min_cov,
                               AVG(p.feature_coverage) AS avg_cov,
                               COUNT(ar.actual_result_id) AS settled,
                               ps.warnings::text AS warnings
                        FROM prediction_sets AS ps
                        JOIN predictions AS p
                          ON p.prediction_set_id = ps.prediction_set_id
                        LEFT JOIN actual_results AS ar
                          ON ar.prediction_id = p.prediction_id
                        GROUP BY ps.prediction_date, ps.status, ps.warnings::text
                        ORDER BY ps.prediction_date
                        """
                    )
                )
                .mappings()
                .all()
            )
    except SQLAlchemyError:
        print("database read failed", file=sys.stderr)
        return 1

    header = (
        f"{'date':12}{'status':16}{'preds':>6}{'buys':>6}"
        f"{'minCov':>8}{'avgCov':>8}{'settled':>8}  partial"
    )
    print(header)
    print("-" * 78)
    complete_days = incomplete_days = 0
    buys_on_incomplete = 0
    for row in rows:
        min_cov = float(row["min_cov"] or 0.0)
        partial = "PARTIAL" in str(row["warnings"] or "")
        incomplete = min_cov < COMPLETE or partial
        if incomplete:
            incomplete_days += 1
            buys_on_incomplete += int(row["buys"] or 0)
        else:
            complete_days += 1
        day = row["d"]
        status = row["set_status"]
        predictions = int(row["predictions"])
        buys = int(row["buys"] or 0)
        settled = int(row["settled"])
        avg_cov = float(row["avg_cov"] or 0)
        print(
            f"{day!s:12}{status!s:16}{predictions:>6}{buys:>6}"
            f"{min_cov:>8.3f}{avg_cov:>8.3f}{settled:>8}  "
            f"{'yes' if partial else 'no'}"
        )

    total = complete_days + incomplete_days
    print("")
    print(json.dumps({
        "prediction_days": total,
        "days_with_incomplete_inputs": incomplete_days,
        "days_with_complete_inputs": complete_days,
        "incomplete_rate": round(incomplete_days / total, 4) if total else None,
        "buys_published_on_incomplete_days": buys_on_incomplete,
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
