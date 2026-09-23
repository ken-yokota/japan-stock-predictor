"""Recompute descriptive arm metrics from the hosted, pre-cutoff record.

This command reads the production database in a read-only transaction. It
prints only cohort counts and writes aggregate metrics; prediction-level rows
exist only in a temporary file and are never uploaded as a CI artifact.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from tempfile import TemporaryDirectory

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from database.connection import create_database_engine
from research.live_audit import audit


def _json_value(value: object) -> object:
    if isinstance(value, date | datetime):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    raise TypeError(f"unsupported audit value: {type(value).__name__}")


def recompute(database_url: str, output: Path) -> dict[str, object]:
    engine = create_database_engine(database_url)
    try:
        with engine.connect() as connection:
            connection.execute(text("SET TRANSACTION READ ONLY"))
            rows = (
                connection.execute(
                    text(
                        """
                    SELECT ps.prediction_date, ps.generated_at,
                           ps.published_at, ps.cutoff_at,
                           ps.status AS prediction_set_status, r.run_type,
                           p.ticker, p.status,
                           p.predicted_intraday_return, p.probability_up,
                           p.return_distribution, p.arm_predictions,
                           p.return_threshold, p.probability_threshold,
                           fs.config_hash, a.actual_intraday_return,
                           a.created_at
                    FROM predictions p
                    JOIN prediction_sets ps
                      ON ps.prediction_set_id = p.prediction_set_id
                    JOIN daily_runs r ON r.run_id = ps.run_id
                    JOIN feature_sets fs ON fs.feature_set_id = p.feature_set_id
                    LEFT JOIN actual_results a
                      ON a.prediction_id = p.prediction_id
                     AND a.status IN ('FINAL', 'CORRECTED')
                     AND a.result_version = (
                         SELECT MAX(newer.result_version)
                         FROM actual_results newer
                         WHERE newer.prediction_id = p.prediction_id
                           AND newer.status IN ('FINAL', 'CORRECTED')
                     )
                    WHERE ps.status = 'READY'
                    ORDER BY ps.prediction_date, p.ticker
                    """
                    )
                )
                .mappings()
                .all()
            )
    finally:
        engine.dispose()

    with TemporaryDirectory(prefix="jsp-live-audit-") as directory:
        source = Path(directory) / "prediction-level.json"
        source.write_text(
            json.dumps([dict(row) for row in rows], default=_json_value),
            encoding="utf-8",
        )
        return audit(source, output)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    database_url = os.environ.get("DATABASE_URL", "").strip()
    if not database_url:
        print("DATABASE_URL is not configured", file=sys.stderr)
        return 2
    try:
        report = recompute(database_url, args.output)
    except (SQLAlchemyError, ValueError, KeyError, TypeError):
        print("read-only live model audit failed", file=sys.stderr)
        return 1
    print(
        json.dumps(
            {
                key: report[key]
                for key in ("source_rows", "eligible_live_rows", "eligible_sessions")
            }
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
