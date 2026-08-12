"""Name the sessions a failing series is missing, and say why each one is missing.

Morning prefetch rejects a series with "provider does not cover the complete
required window" and stops there. That message says a date is absent; it never
says which, and without that the choice between "the provider really has no
data" and "we asked for a day this market never trades" cannot be made. Relaxing
the coverage threshold to make the message go away would hide the second case
behind the first.

So this prints, per series: the sessions the fetch window requires, the sessions
the provider actually returns, the difference, and a classification of each
missing day.

Read-only. It fetches from the provider and writes nothing to the database, and
prints dates and counts only - never a price.
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import UTC, date, datetime, timedelta

from data.config import load_app_config
from data.availability import prediction_cutoff
from data.fetch import _eod_fetch_window, _sessions, _source_request, build_fetch_plan
from data.providers.yahoo import YahooFinanceProvider

DEFAULT_SERIES = ("usdjpy", "eurjpy", "audjpy", "oih", "kre")


def _classify(missing: date, *, weekday_only: set[date], nyse: set[date]) -> str:
    """Why is this date absent from what the provider returned?"""

    if missing.weekday() >= 5:
        return "WEEKEND_REQUIRED (calendar bug)"
    if missing not in nyse:
        return "WRONG_CALENDAR (not an NYSE session)"
    if missing in weekday_only:
        return "PROVIDER_MISSING (weekday, NYSE session, no bar returned)"
    return "UNCLASSIFIED"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--series", nargs="*", default=list(DEFAULT_SERIES))
    parser.add_argument("--prediction-date", default=None)
    parser.add_argument("--history-days", type=int, default=550)
    arguments = parser.parse_args(argv)

    config = load_app_config()
    plan = build_fetch_plan(config)

    prediction_date = (
        date.fromisoformat(arguments.prediction_date)
        if arguments.prediction_date
        else datetime.now(UTC).date()
    )
    cutoff_at = prediction_cutoff(prediction_date)
    start_date = prediction_date - timedelta(days=arguments.history_days)
    end_date = prediction_date - timedelta(days=1)

    print(f"prediction_date : {prediction_date}")
    print(f"cutoff_at       : {cutoff_at.isoformat()}")
    print(f"window          : {start_date} .. {end_date}")
    print("")

    settings = config.settings.provider
    providers = {
        "yahoo_finance": YahooFinanceProvider(
            timeout_seconds=int(os.environ.get("HTTP_TIMEOUT_SECONDS", "30")),
            max_retries=settings.max_retries,
            backoff_seconds=settings.backoff_initial_seconds,
        )
    }

    for canonical in arguments.series:
        target = next(
            (item for item in plan.eod if item.canonical_symbol == canonical), None
        )
        if target is None:
            print(f"{canonical}: not in the fetch plan")
            continue

        request = _source_request(
            target, target.primary, start_date=start_date, end_date=end_date
        )
        window = _eod_fetch_window(
            canonical_symbol=canonical,
            start_date=start_date,
            end_date=end_date,
            market=request.market,
            market_timezone=request.market_timezone,
            market_close=request.market_close,
            availability_lag_minutes=request.availability_lag_minutes,
            cutoff_at=cutoff_at,
            coverage={},
        )
        required = set(window.required_sessions)
        provider = providers.get(target.primary.provider)
        if provider is None:
            print(f"{canonical}: provider {target.primary.provider} unavailable")
            continue

        try:
            rows = provider.fetch_eod(
                _source_request(
                    target,
                    target.primary,
                    start_date=window.request_start or start_date,
                    end_date=window.target_session or end_date,
                )
            )
        except Exception as error:
            print(f"{canonical}: fetch raised {type(error).__name__}: {error}")
            continue

        available = {row.market_date for row in rows}
        visible = {
            row.market_date for row in rows if row.available_timestamp <= cutoff_at
        }
        missing = sorted(required - visible)
        nyse = set(_sessions(start_date, end_date, market="US"))
        weekday_only = {
            start_date + timedelta(days=offset)
            for offset in range((end_date - start_date).days + 1)
            if (start_date + timedelta(days=offset)).weekday() < 5
        }

        coverage = (len(required & visible) / len(required)) if required else 1.0
        print(f"=== {canonical}  ({target.primary.provider} {request.market}) ===")
        print(f"  market_timezone : {request.market_timezone}")
        print(f"  required        : {len(required)}")
        print(f"  returned        : {len(available)}")
        print(f"  visible@cutoff  : {len(visible)}")
        print(f"  coverage        : {coverage:.4f}")
        print(f"  missing         : {len(missing)}")
        for day in missing[:20]:
            print(f"    {day}  {_classify(day, weekday_only=weekday_only, nyse=nyse)}")
        if len(missing) > 20:
            print(f"    ... and {len(missing) - 20} more")
        print("")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
