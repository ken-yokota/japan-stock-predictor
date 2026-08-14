"""What the predictions claimed, and what the sessions actually did.

"It never hits" and "the market moved against everything that day" produce the
same experience and need different responses, so this separates them: per day,
how many predictions were directionally right, and how the realised returns
were distributed across all 22 stocks whether or not a BUY was issued.

A day where every stock fell is not a day the model got 22 things wrong.

Read-only, in a read-only transaction.
"""

from __future__ import annotations

import argparse
import os
import statistics
import sys
from decimal import Decimal
from typing import Any

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from database.connection import create_database_engine


def _num(value: object) -> float | None:
    """Row values arrive typed as object; narrow them once.

    Numeric columns come back as ``Decimal``, which is neither int nor float.
    An isinstance check that forgets that turns every price in the report into
    a silent zero - which is exactly how the first run of this audit claimed
    every prediction and every outcome was 0.00%.
    """

    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, Decimal | int | float):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return None
    return None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--from-date", default="2026-08-01")
    arguments = parser.parse_args(argv)

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
                        SELECT ps.prediction_date AS d, p.ticker, p.signal,
                               p.predicted_intraday_return AS pred,
                               p.probability_up AS prob,
                               a.actual_intraday_return AS actual,
                               t.net_profit_jpy AS pnl,
                               r.run_type
                        FROM predictions AS p
                        JOIN prediction_sets AS ps
                          ON ps.prediction_set_id = p.prediction_set_id
                        JOIN daily_runs AS r ON r.run_id = ps.run_id
                        LEFT JOIN actual_results AS a
                          ON a.prediction_id = p.prediction_id
                        LEFT JOIN simulated_trades AS t
                          ON t.prediction_id = p.prediction_id
                        WHERE ps.prediction_date >= :since
                          AND r.run_type = 'MORNING'
                        ORDER BY ps.prediction_date, p.ticker
                        """
                    ),
                    {"since": arguments.from_date},
                )
                .mappings()
                .all()
            )
    except SQLAlchemyError:
        print("database read failed", file=sys.stderr)
        return 1

    by_day: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        by_day.setdefault(str(row["d"]), []).append(dict(row))

    header = (
        f"{'date':12}{'n':>4}{'settled':>8}{'up':>5}{'down':>6}"
        f"{'median':>9}{'buys':>6}{'hit':>6}  予測と実績"
    )
    print(header)
    print("-" * len(header))
    for day, items in sorted(by_day.items()):
        settled = [i for i in items if i["actual"] is not None]
        actuals = [x for x in (_num(i["actual"]) for i in settled) if x is not None]
        up = sum(1 for a in actuals if a > 0)
        down = sum(1 for a in actuals if a < 0)
        median = statistics.median(actuals) if actuals else 0.0
        buys = [i for i in items if str(i["signal"]) == "BUY"]
        hits = 0
        for item in buys:
            realised = _num(item["actual"])
            if realised is not None and realised > 0:
                hits += 1
        preds = [x for x in (_num(i["pred"]) for i in items) if x is not None]
        predicted_median = statistics.median(preds) if preds else 0.0
        hit_text = f"{hits}/{len(buys)}" if buys else "—"
        print(
            f"{day:12}{len(items):>4}{len(settled):>8}{up:>5}{down:>6}"
            f"{median:>9.2%}{len(buys):>6}{hit_text:>6}"
            f"  予測中央値 {predicted_median:+.2%}"
        )

    print("")
    print("=== BUYの明細 ===")
    for day, items in sorted(by_day.items()):
        for item in items:
            if str(item["signal"]) != "BUY":
                continue
            realised = _num(item["actual"])
            outcome = "—" if realised is None else f"{realised:+.2%}"
            predicted = _num(item["pred"]) or 0.0
            probability = _num(item["prob"]) or 0.0
            print(
                f"  {day}  {item['ticker']}  "
                f"予測 {predicted:+.2%}  確率 {probability:.0%}  実績 {outcome}"
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
