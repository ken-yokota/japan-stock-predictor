#!/usr/bin/env python3
"""Optionally capture Actual Open after the JPX market has opened."""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from datetime import date, datetime
from pathlib import Path

from pipeline.open import OpenPipeline
from scripts.runtime import load_runtime
from services.ingestion import today_in_application_timezone


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config-dir", type=Path, default=Path("config"))
    parser.add_argument("--prediction-date", type=date.fromisoformat)
    parser.add_argument("--observed-at", type=datetime.fromisoformat)
    args = parser.parse_args()
    config, environment, engine, factory = load_runtime(args.config_dir)
    prediction_date = args.prediction_date or today_in_application_timezone(config)
    try:
        result = OpenPipeline(factory, config, environment).run(
            prediction_date, observed_at=args.observed_at
        )
        print(json.dumps(asdict(result), ensure_ascii=False, default=str))
        return 0
    finally:
        engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
