#!/usr/bin/env python3
"""Replay a window with every model family, and score what each would have bought.

The operator asked which of the ten families would actually have made money,
so this rebuilds each session's point-in-time dataset, fits all of them on
exactly the rows that morning had, and records the whole quantile curve for
each. Scoring happens later and separately, in
``scripts/report_all_method_backtest.py``, so the thresholds can be changed
without refitting anything -- which also stops a threshold being chosen by
running the fit repeatedly until a number looks good.

Three things keep this from being a look-ahead machine:

* the dataset builder is the production one, asked for the same window the
  morning would have had, so no row after the cutoff enters a fit
* the outcome is read from the raw price table afterwards and never touches
  the features
* hyperparameters are still selected inside each window, per session, which is
  slow and is the point -- selecting them once over the whole period and then
  scoring on it would flatter every arm at once

It is still a replay. It shows what these models would have said, not what
this system did, and it can never be reported as live performance.

Usage:
    python -m scripts.run_all_method_backtest --from-date 2026-08-07 \
        --to-date 2026-08-28 --workers 4 --out artifacts/all_methods/raw.json
"""

from __future__ import annotations

import argparse
import json
import os
import time
from collections.abc import Sequence
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import asdict, dataclass
from datetime import date
from pathlib import Path
from typing import Any

from sqlalchemy import text

from data.config import load_app_config
from data.market_calendar import is_japan_business_day
from database.connection import create_database_engine, create_session_factory
from models.arms import run_arms
from services.dataset import PointInTimeDatasetBuilder


@dataclass(frozen=True, slots=True)
class ArmRow:
    """One family's answer for one ticker-session, plus what happened."""

    date: str
    ticker: str
    arm: str
    label: str
    status: str
    predicted_return: float | None
    probability_up: float | None
    spread_kind: str | None
    quantiles: dict[str, float]
    actual_return: float | None


def _business_days(start: date, end: date) -> list[date]:
    days, cursor = [], start
    while cursor <= end:
        if is_japan_business_day(cursor):
            days.append(cursor)
        cursor = date.fromordinal(cursor.toordinal() + 1)
    return days


def _actuals(database_url: str, days: Sequence[date]) -> dict[tuple[str, str], float]:
    """Open-to-close return per ticker-session, read from the raw price table.

    Deliberately not from ``actual_results``: that table only covers sessions
    this system actually predicted, and the operator asked for a window that
    starts before it did.
    """

    engine = create_database_engine(database_url)
    out: dict[tuple[str, str], float] = {}
    with engine.connect() as connection:
        rows = connection.execute(
            text(
                """
                SELECT canonical_symbol, market_date, open, close
                FROM stock_prices
                WHERE market_date = ANY(:days)
                  AND open IS NOT NULL AND open > 0 AND close IS NOT NULL
                """
            ),
            {"days": list(days)},
        )
        for symbol, market_date, open_price, close in rows:
            ticker = str(symbol).split(".")[0]
            out[(market_date.isoformat(), ticker)] = float(
                (float(close) - float(open_price)) / float(open_price)
            )
    engine.dispose()
    return out


_STATE: dict[str, Any] = {}


def _init(config_dir: str, database_url: str) -> None:
    """One engine per worker process, built once rather than per task."""

    _STATE["config"] = load_app_config(Path(config_dir))
    engine = create_database_engine(database_url)
    _STATE["factory"] = create_session_factory(engine)


def _one(day: str, ticker: str) -> list[dict[str, Any]]:
    config = _STATE["config"]
    factory = _STATE["factory"]
    target_day = date.fromisoformat(day)
    with factory() as session:
        builder = PointInTimeDatasetBuilder(session, config)
        try:
            dataset = builder.build(
                ticker,
                target_day,
                training_sessions=config.model.training.window_jpx_sessions,
                minimum_feature_coverage=1.0
                - (config.model.features.max_missing_ratio or 0.2),
                operational=False,
            )
        except Exception as error:
            return [
                {
                    "date": day,
                    "ticker": ticker,
                    "arm": "_dataset",
                    "label": "データ構築",
                    "status": "FAILED",
                    "predicted_return": None,
                    "probability_up": None,
                    "spread_kind": None,
                    "quantiles": {},
                    "actual_return": None,
                    "detail": type(error).__name__,
                }
            ]
        names = list(dataset.feature_names)
        forecasts = run_arms(
            dataset.training_frame.loc[:, names],
            dataset.training_target.to_numpy(dtype=float),
            dataset.current_frame.loc[:, names],
            levels=tuple(config.model.hyperparameters.quantile_levels),
            n_splits=config.model.cross_validation.n_splits,
            include_sequence=config.model.models.include_sequence_arms,
        )
    rows: list[dict[str, Any]] = []
    for forecast in forecasts:
        quantiles = (
            {f"q{level:g}": value for level, value in forecast.distribution.pairs()}
            if forecast.distribution is not None
            else {}
        )
        rows.append(
            asdict(
                ArmRow(
                    date=day,
                    ticker=ticker,
                    arm=forecast.name,
                    label=forecast.label,
                    status=forecast.status,
                    predicted_return=forecast.predicted_return,
                    probability_up=forecast.probability_up,
                    spread_kind=forecast.spread_kind,
                    quantiles=quantiles,
                    actual_return=None,
                )
            )
        )
    return rows


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config-dir", type=Path, default=Path("config"))
    parser.add_argument("--from-date", type=date.fromisoformat, required=True)
    parser.add_argument("--to-date", type=date.fromisoformat, required=True)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--tickers", nargs="*", default=None)
    parser.add_argument(
        "--out", type=Path, default=Path("artifacts/all_methods/raw.json")
    )
    args = parser.parse_args(argv)

    database_url = os.environ.get("DATABASE_URL", "").strip()
    if not database_url:
        raise SystemExit("DATABASE_URL is required")
    config = load_app_config(args.config_dir)
    tickers = args.tickers or [
        stock.ticker for stock in config.stocks.stocks if stock.enabled
    ]
    days = _business_days(args.from_date, args.to_date)
    tasks = [(day.isoformat(), ticker) for day in days for ticker in tickers]
    print(
        f"{len(days)}営業日 x {len(tickers)}銘柄 = {len(tasks)}件 / 並列{args.workers}",
        flush=True,
    )

    started = time.perf_counter()
    collected: list[dict[str, Any]] = []
    with ProcessPoolExecutor(
        max_workers=args.workers,
        initializer=_init,
        initargs=(str(args.config_dir), database_url),
    ) as pool:
        futures = {
            pool.submit(_one, day, ticker): (day, ticker) for day, ticker in tasks
        }
        for index, future in enumerate(as_completed(futures), 1):
            day, ticker = futures[future]
            try:
                collected.extend(future.result())
            except Exception as error:  # a worker dying must not lose the rest
                print(f"  !! {day} {ticker}: {type(error).__name__}", flush=True)
            if index % 10 == 0 or index == len(tasks):
                elapsed = time.perf_counter() - started
                rate = elapsed / index
                print(
                    f"  {index}/{len(tasks)} 完了 "
                    f"経過{elapsed / 60:.1f}分 "
                    f"残り約{rate * (len(tasks) - index) / 60:.1f}分",
                    flush=True,
                )

    actuals = _actuals(database_url, days)
    for row in collected:
        row["actual_return"] = actuals.get((row["date"], row["ticker"]))

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(
        json.dumps(
            {
                "from": args.from_date.isoformat(),
                "to": args.to_date.isoformat(),
                "sessions": [day.isoformat() for day in days],
                "tickers": tickers,
                "levels": list(config.model.hyperparameters.quantile_levels),
                "rows": collected,
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    print(
        f"保存しました: {args.out} "
        f"({len(collected)}行 / 所要{(time.perf_counter() - started) / 60:.1f}分)",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
