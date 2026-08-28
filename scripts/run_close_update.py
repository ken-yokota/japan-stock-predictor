#!/usr/bin/env python3
"""Run the retryable JPX close confirmation and OOS scoring pipeline."""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from datetime import date, datetime
from pathlib import Path
from typing import Any

from pipeline.close import ClosePipeline
from scripts.runtime import load_runtime
from services.ingestion import today_in_application_timezone


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config-dir", type=Path, default=Path("config"))
    parser.add_argument("--prediction-date", type=date.fromisoformat)
    parser.add_argument("--observed-at", type=datetime.fromisoformat)
    parser.add_argument("--skip-fetch", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    return parser


def _settled_session(config: Any) -> date:
    """The last JPX session whose close has passed, or today as a last resort."""

    from datetime import datetime
    from zoneinfo import ZoneInfo

    try:
        from data.market_calendar import latest_settled_session

        return latest_settled_session(
            datetime.now(ZoneInfo(config.application.timezone))
        )
    except Exception:
        return today_in_application_timezone(config)


def main() -> int:
    args = _parser().parse_args()
    config, environment, engine, factory = load_runtime(args.config_dir)
    # Not "today": a delayed run reads a different date from the same clock.
    # On 2026-08-28 three close updates fired eleven hours late, at 02:53, 02:55
    # and 03:16 JST, took the date to be 08-28, found nothing to settle for a
    # session that had not opened, and returned SKIPPED. 08-27 went unsettled
    # until it was run by hand.
    prediction_date = args.prediction_date or _settled_session(config)
    try:
        if args.dry_run:
            print(
                json.dumps(
                    {
                        "status": "DRY_RUN",
                        "prediction_date": prediction_date.isoformat(),
                        "database_configured": bool(environment.database_url),
                    },
                    ensure_ascii=False,
                )
            )
            return 0
        result = ClosePipeline(factory, config, environment).run(
            prediction_date,
            observed_at=args.observed_at,
            fetch_data=not args.skip_fetch,
        )
        print(json.dumps(asdict(result), ensure_ascii=False, default=str))
        return 0 if result.status != "FAILED" else 2
    finally:
        engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
