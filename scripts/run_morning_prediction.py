#!/usr/bin/env python3
"""Run the idempotent 08:20 JST free-data prediction pipeline."""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from datetime import date
from pathlib import Path

from pipeline.morning import MorningPipeline
from scripts.runtime import load_runtime
from services.ingestion import today_in_application_timezone


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config-dir", type=Path, default=Path("config"))
    parser.add_argument("--prediction-date", type=date.fromisoformat)
    parser.add_argument("--skip-ingestion", action="store_true")
    parser.add_argument("--history-days", type=int, default=550)
    parser.add_argument("--dry-run", action="store_true")
    return parser


def main() -> int:
    args = _parser().parse_args()
    config, environment, engine, factory = load_runtime(args.config_dir)
    prediction_date = args.prediction_date or today_in_application_timezone(config)
    try:
        if args.dry_run:
            print(
                json.dumps(
                    {
                        "status": "DRY_RUN",
                        "prediction_date": prediction_date.isoformat(),
                        "configured_stocks": len(config.stocks.stocks),
                        "database_configured": bool(environment.database_url),
                    },
                    ensure_ascii=False,
                )
            )
            return 0
        result = MorningPipeline(factory, config, environment).run(
            prediction_date,
            perform_ingestion=not args.skip_ingestion,
            history_days=args.history_days,
        )
        print(json.dumps(asdict(result), ensure_ascii=False, default=str))
        return 0 if result.status in {"READY", "INSUFFICIENT_DATA", "SKIPPED"} else 2
    finally:
        engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
