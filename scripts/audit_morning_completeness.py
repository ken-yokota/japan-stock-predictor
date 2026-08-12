"""Audit one morning: what each stock was owed, what arrived, and what it bought.

``feature_coverage`` cannot answer whether a day had the data it needed - its
denominator is built from the features that materialised, so an indicator
absent from every session never enters it. Indicator completeness is recorded
separately, and this reads it back.

The distinction that matters here is between "recorded as complete" and "not
recorded at all". Feature sets written before the completeness fields existed
carry no misses, and an empty list there means nothing was written rather than
nothing was missing. Those days are reported as LEGACY_UNKNOWN. Reading them
as COMPLETE would manufacture exactly the reassurance this audit exists to
withdraw.

Read-only: it opens a read-only transaction and issues no statement that
writes.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter
from datetime import date, datetime
from zoneinfo import ZoneInfo

from sqlalchemy import text
from sqlalchemy.engine import Connection
from sqlalchemy.exc import SQLAlchemyError

from database.connection import create_database_engine

JST = ZoneInfo("Asia/Tokyo")

# The five series that have been failing in the morning prefetch.
WATCHED = ("usdjpy", "eurjpy", "audjpy", "oih", "kre")

CLEAN, DEGRADED, UNKNOWN = "CLEAN", "DEGRADED", "LEGACY_UNKNOWN"


def _rows(connection: Connection, for_date: date) -> list[dict[str, object]]:
    result = connection.execute(
        text(
            """
            SELECT fs.ticker,
                   fs.feature_version,
                   fs.details::text AS details,
                   p.status AS prediction_status,
                   p.signal,
                   p.rank,
                   p.predicted_intraday_return,
                   p.probability_up,
                   p.feature_coverage,
                   ps.model_version,
                   ps.cutoff_at
            FROM feature_sets AS fs
            LEFT JOIN prediction_sets AS ps
              ON ps.run_id = fs.run_id
            LEFT JOIN predictions AS p
              ON p.prediction_set_id = ps.prediction_set_id
             AND p.ticker = fs.ticker
            WHERE fs.prediction_date = :for_date
            ORDER BY fs.ticker
            """
        ),
        {"for_date": for_date},
    )
    return [dict(row) for row in result.mappings().all()]


def classify(
    details: dict[str, object],
) -> tuple[str, list[str], list[str], float | None]:
    """COMPLETE only when the run actually recorded that it had everything."""

    if "missing_required_indicators" not in details:
        return UNKNOWN, [], [], None
    required = [str(item) for item in details.get("missing_required_indicators") or []]
    optional = [str(item) for item in details.get("missing_optional_indicators") or []]
    raw_coverage = details.get("indicator_coverage")
    coverage = float(raw_coverage) if isinstance(raw_coverage, int | float) else None
    return (DEGRADED if required else CLEAN), required, optional, coverage


def _percent(value: float | None) -> str:
    return "—" if value is None else f"{value * 100:5.1f}%"


def audit(connection: Connection, for_date: date) -> int:
    rows = _rows(connection, for_date)
    if not rows:
        print(f"{for_date}: no feature sets found")
        return 1

    header = (
        f"{'code':6}{'indCov':>8}{'featCov':>9}{'status':16}"
        f"{'signal':8}{'ret':>9}{'p_up':>7}  missing required"
    )
    print(f"prediction_date : {for_date}")
    print("")
    print(header)
    print("-" * len(header))

    degraded_buys: list[str] = []
    clean_buys: list[str] = []
    unknown_stocks: list[str] = []
    degraded_stocks: list[str] = []
    hidden_by_feature_coverage: list[str] = []
    missing_tally: Counter[str] = Counter()

    for row in rows:
        details = json.loads(str(row["details"] or "{}"))
        status, required, _optional, indicator_coverage = classify(details)
        ticker = str(row["ticker"])
        signal = str(row["signal"] or "—")
        feature_coverage = row["feature_coverage"]
        feature_value = (
            float(feature_coverage) if feature_coverage is not None else None
        )
        predicted = row["predicted_intraday_return"]
        probability = row["probability_up"]

        missing_tally.update(required)
        if status == UNKNOWN:
            unknown_stocks.append(ticker)
        elif status == DEGRADED:
            degraded_stocks.append(ticker)
        if signal == "BUY":
            (degraded_buys if status == DEGRADED else clean_buys).append(ticker)
        if (
            feature_value is not None
            and feature_value >= 0.9999
            and indicator_coverage is not None
            and indicator_coverage < 0.9999
        ):
            hidden_by_feature_coverage.append(ticker)

        print(
            f"{ticker:6}{_percent(indicator_coverage):>8}{_percent(feature_value):>9}"
            f"{status:16}{signal:8}"
            f"{(f'{float(predicted):+.3%}' if predicted is not None else '—'):>9}"
            f"{(f'{float(probability):.2f}' if probability is not None else '—'):>7}"
            f"  {', '.join(required) if required else '-'}"
        )

    print("")
    print("=== summary ===")
    print(f"  stocks with feature sets       : {len(rows)}")
    print(f"  missing required (DEGRADED)    : {len(degraded_stocks)}")
    print(f"  not recorded (LEGACY_UNKNOWN)  : {len(unknown_stocks)}")
    print(f"  BUY candidates                 : {len(clean_buys) + len(degraded_buys)}")
    print(f"    CLEAN_BUY                    : {len(clean_buys)} {clean_buys}")
    print(f"    DEGRADED_BUY                 : {len(degraded_buys)} {degraded_buys}")
    print(
        "  featCov=100% but indCov<100%   : "
        f"{len(hidden_by_feature_coverage)} {hidden_by_feature_coverage}"
    )
    print("")
    print("=== missing required indicators, most affected first ===")
    for indicator, count in missing_tally.most_common():
        print(f"  {indicator:12} {count:3} stocks")
    if not missing_tally:
        print("  (none recorded)")

    print("")
    print("=== the five watched series ===")
    for indicator in WATCHED:
        count = missing_tally.get(indicator, 0)
        affected = [t for t in degraded_buys if count] if count else []
        state = "not recorded" if unknown_stocks and not missing_tally else f"{count}"
        print(
            f"  {indicator:12} missing for {state:>12} stocks"
            f"   BUY affected: {len(affected)}"
        )

    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--date", default=None, help="prediction date (YYYY-MM-DD); default today JST"
    )
    arguments = parser.parse_args(argv)

    url = os.environ.get("DATABASE_URL", "").strip()
    if not url:
        print("DATABASE_URL is not set", file=sys.stderr)
        return 2
    for_date = (
        date.fromisoformat(arguments.date)
        if arguments.date
        else datetime.now(JST).date()
    )

    engine = create_database_engine(url)
    try:
        with engine.connect() as connection:
            connection.execute(text("SET TRANSACTION READ ONLY"))
            return audit(connection, for_date)
    except SQLAlchemyError:
        # The message can carry the host and the user name, so it is not shown.
        print("database read failed", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
