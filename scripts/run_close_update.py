#!/usr/bin/env python3
"""Run the retryable JPX close confirmation and OOS scoring pipeline."""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from data.config import AppConfig
from data.market_calendar import latest_settled_session
from pipeline.close import ClosePipeline, unsettled_close_dates
from scripts.runtime import load_runtime


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config-dir", type=Path, default=Path("config"))
    parser.add_argument("--prediction-date", type=date.fromisoformat)
    parser.add_argument("--observed-at", type=datetime.fromisoformat)
    parser.add_argument("--skip-fetch", action="store_true")
    parser.add_argument(
        "--catch-up-sessions",
        type=int,
        choices=range(1, 21),
        default=1,
        help="Also settle missing outcomes in this many recent JPX sessions (1-20)",
    )
    parser.add_argument("--dry-run", action="store_true")
    return parser


def _settled_session(config: AppConfig) -> date:
    """Resolve the last closed JPX session; never substitute an unverified date."""

    return latest_settled_session(
        datetime.now(ZoneInfo(config.settings.application.timezone))
    )


def main() -> int:
    args = _parser().parse_args()
    config, environment, engine, factory = load_runtime(args.config_dir)
    # Not "today": a delayed run reads a different date from the same clock.
    # On 2026-08-28 three close updates fired eleven hours late, at 02:53, 02:55
    # and 03:16 JST, took the date to be 08-28, found nothing to settle for a
    # session that had not opened, and returned SKIPPED. 08-27 went unsettled
    # until it was run by hand.
    try:
        prediction_date = args.prediction_date or _settled_session(config)
        dates = [prediction_date]
        if args.catch_up_sessions > 1:
            with factory() as session:
                missing = unsettled_close_dates(
                    session, prediction_date, session_count=args.catch_up_sessions
                )
            dates = [d for d in missing if d != prediction_date] + [prediction_date]
        if args.dry_run:
            print(
                json.dumps(
                    {
                        "status": "DRY_RUN",
                        "prediction_date": prediction_date.isoformat(),
                        "catch_up_dates": [d.isoformat() for d in dates[:-1]],
                        "database_configured": bool(environment.database_url),
                    },
                    ensure_ascii=False,
                )
            )
            return 0
        pipeline = ClosePipeline(factory, config, environment)
        results = [
            pipeline.run(
                day,
                observed_at=args.observed_at,
                fetch_data=not args.skip_fetch,
            )
            for day in dates
        ]
        payload = asdict(results[-1])
        if len(results) > 1:
            payload["catch_up"] = [asdict(result) for result in results[:-1]]
        if any(result.status == "FAILED" for result in results):
            payload["status"] = "FAILED"
        elif any(
            result.status not in {"SUCCESS", "SKIPPED"} for result in results[:-1]
        ):
            payload["status"] = "PARTIAL"
        print(json.dumps(payload, ensure_ascii=False, default=str))
        return 2 if payload["status"] == "FAILED" else 0
    finally:
        engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
