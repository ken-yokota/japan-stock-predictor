"""Share the scheduled and external trigger's 08:20 JST collection window."""

from __future__ import annotations

import argparse
import json
import math
import time
from datetime import date, datetime
from zoneinfo import ZoneInfo

from data.availability import prediction_cutoff
from data.config import load_app_config
from data.market_calendar import is_japan_business_day


def wait_seconds(now: datetime, prediction_date: date) -> int:
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("now must be timezone-aware")
    local = now.astimezone(ZoneInfo("Asia/Tokyo"))
    if local.date() != prediction_date or not is_japan_business_day(prediction_date):
        return 0
    target = local.replace(hour=8, minute=20, second=0, microsecond=0)
    delay = max(0, math.ceil((target - local).total_seconds()))
    # Both production entry points start at/after 08:05. Refuse an unexpectedly
    # early job instead of a capped sleep that would still publish too early.
    if delay > 15 * 60:
        raise ValueError("morning collection job started before 08:05 JST")
    return delay


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prediction-date", type=date.fromisoformat)
    args = parser.parse_args()
    config = load_app_config()
    now = datetime.now(ZoneInfo("Asia/Tokyo"))
    day = args.prediction_date or now.date()
    delay = wait_seconds(now, day)
    print(
        json.dumps(
            {
                "wait_seconds": delay,
                "prediction_date": str(day),
                "prediction_cutoff": prediction_cutoff(
                    day,
                    cutoff_time=config.settings.schedule.prediction_cutoff,
                    timezone_name=config.settings.application.timezone,
                ).isoformat(),
            }
        ),
        flush=True,
    )
    if delay:
        time.sleep(delay)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
