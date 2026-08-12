"""Audit one morning: what each stock was owed, what arrived, and what it bought.

The judgement itself lives in ``dashboard.completeness`` so that this command,
the Today page and the morning mail cannot answer the same question
differently. This file is only the terminal view of it.

Read-only: it opens a read-only transaction and issues no statement that
writes.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import date, datetime
from zoneinfo import ZoneInfo

from sqlalchemy import text
from sqlalchemy.engine import Connection
from sqlalchemy.exc import SQLAlchemyError

from dashboard.completeness import (
    MorningCompletenessSummary,
    StockCompleteness,
    stock_from_details,
    summarise,
)
from database.connection import create_database_engine

JST = ZoneInfo("Asia/Tokyo")


def _load(connection: Connection, for_date: date) -> list[StockCompleteness]:
    result = connection.execute(
        text(
            """
            SELECT fs.ticker,
                   fs.details::text AS details,
                   p.signal,
                   p.feature_coverage
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
    return [
        stock_from_details(
            str(row["ticker"]),
            json.loads(str(row["details"] or "{}")),
            feature_coverage=row["feature_coverage"],
            signal=row["signal"],
        )
        for row in result.mappings().all()
    ]


def _percent(value: float | None) -> str:
    return "—" if value is None else f"{value * 100:5.1f}%"


def render(summary: MorningCompletenessSummary, for_date: date) -> str:
    lines = [f"prediction_date : {for_date}", ""]
    header = (
        f"{'code':6}{'indCov':>8}{'featCov':>9}{'status':16}"
        f"{'signal':8}  missing required"
    )
    lines += [header, "-" * len(header)]
    for stock in summary.stocks:
        missing = ", ".join(stock.missing_required) if stock.missing_required else "-"
        lines.append(
            f"{stock.ticker:6}{_percent(stock.indicator_coverage):>8}"
            f"{_percent(stock.feature_coverage):>9}{stock.status:16}"
            f"{(stock.signal or '—'):8}  {missing}"
        )

    degraded = [item.ticker for item in summary.degraded_buys]
    lines += [
        "",
        "=== summary ===",
        f"  stocks                        : {summary.stock_count}",
        f"  CLEAN                         : {summary.clean_count}",
        f"  DEGRADED (missing required)   : {summary.degraded_count}",
        f"  LEGACY_UNKNOWN (not recorded) : {summary.unknown_count}",
        f"  data status                   : {summary.data_status}",
        f"  BUY candidates                : {summary.buy_count}",
        f"    CLEAN_BUY                   : {summary.clean_buy_count}",
        f"    DEGRADED_BUY                : {summary.degraded_buy_count} {degraded}",
        f"  featCov=100% but indCov<100%  : "
        f"{len(summary.hidden_by_feature_coverage)} "
        f"{list(summary.hidden_by_feature_coverage)}",
        "",
        "=== missing required indicators, most affected first ===",
    ]
    if summary.missing_required_ranking:
        lines += [
            f"  {name:12} {count:3} stocks"
            for name, count in summary.missing_required_ranking
        ]
    else:
        lines.append("  (none recorded)")

    lines += ["", "=== watched series ==="]
    lines += [
        f"  {name:12} missing for {count:3} stocks"
        for name, count in summary.watched()
    ]
    return "\n".join(lines)


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
            stocks = _load(connection, for_date)
    except SQLAlchemyError:
        # The message can carry the host and the user name, so it is not shown.
        print("database read failed", file=sys.stderr)
        return 1

    if not stocks:
        print(f"{for_date}: no feature sets found")
        return 1
    print(render(summarise(stocks), for_date))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
