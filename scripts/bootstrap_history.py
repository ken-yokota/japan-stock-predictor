#!/usr/bin/env python3
"""Bootstrap up to three years of free EOD history into PostgreSQL."""

from __future__ import annotations

import argparse
from datetime import date, timedelta
from pathlib import Path

from data.fetch import main as fetch_main


def _years_before(value: date, years: int) -> date:
    try:
        return value.replace(year=value.year - years)
    except ValueError:
        return value.replace(year=value.year - years, day=28)


def _parser() -> argparse.ArgumentParser:
    today = date.today()
    last_completed_calendar_date = today - timedelta(days=1)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config-dir", type=Path, default=Path("config"))
    parser.add_argument(
        "--from-date", type=date.fromisoformat, default=_years_before(today, 3)
    )
    parser.add_argument(
        "--to-date", type=date.fromisoformat, default=last_completed_calendar_date
    )
    return parser


def main() -> int:
    args = _parser().parse_args()
    if args.from_date > args.to_date:
        raise ValueError("from-date must not be after to-date")
    return fetch_main(
        [
            "--config-dir",
            str(args.config_dir),
            "fetch-free",
            "--from-date",
            args.from_date.isoformat(),
            "--to-date",
            args.to_date.isoformat(),
        ]
    )


if __name__ == "__main__":
    raise SystemExit(main())
