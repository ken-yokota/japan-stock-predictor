#!/usr/bin/env python3
"""Prefetch PIT-safe EOD history before the morning prediction window."""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta
from pathlib import Path

from sqlalchemy import Engine
from sqlalchemy.orm import Session, sessionmaker

from data.availability import prediction_cutoff
from data.config import AppConfig
from data.env import EnvironmentSettings
from data.fetch import IngestionReport
from data.market_calendar import is_japan_business_day
from scripts.runtime import load_runtime
from services.ingestion import ingest_free_morning_data, today_in_application_timezone

HISTORY_DAYS = 550
SNAPSHOT_SKIP_REASON = "08:30 snapshot fetch not requested"


@dataclass(frozen=True, slots=True)
class MorningPrefetchResult:
    """Sanitized JSON result for one scheduled prefetch invocation."""

    prediction_date: date
    cutoff_at: datetime
    start_date: date
    end_date: date
    status: str
    run_id: str | None = None
    ingestion_status: str | None = None
    requested_sources: int = 0
    succeeded_sources: int = 0
    inserted_rows: int = 0
    reused_rows: int = 0
    failed_sources: dict[str, str] | None = None
    skipped_sources: dict[str, str] | None = None
    covered_sources: dict[str, str] | None = None
    unresolved_required: tuple[str, ...] = ()
    unexpected_skips: tuple[str, ...] = ()
    message: str | None = None
    error_class: str | None = None


def _unexpected_skips(report: IngestionReport) -> tuple[str, ...]:
    """Return skips that are not the prefetch's intentional snapshot exclusion."""

    return tuple(
        sorted(
            symbol
            for symbol, reason in report.skipped_sources.items()
            if reason != SNAPSHOT_SKIP_REASON
        )
    )


def _prefetch_status(report: IngestionReport) -> tuple[str, tuple[str, ...]]:
    unexpected = _unexpected_skips(report)
    if report.failed_sources or report.unresolved_required or unexpected:
        return "FAILED", unexpected
    return "SUCCESS", unexpected


def run_prefetch(
    factory: sessionmaker[Session],
    config: AppConfig,
    environment: EnvironmentSettings,
    *,
    prediction_date: date,
    allow_non_business_day: bool = False,
) -> MorningPrefetchResult:
    """Fetch only history visible by the immutable prediction-date cutoff."""

    cutoff_at = prediction_cutoff(prediction_date)
    start_date = prediction_date - timedelta(days=HISTORY_DAYS)
    end_date = prediction_date - timedelta(days=1)
    if not allow_non_business_day and not is_japan_business_day(prediction_date):
        return MorningPrefetchResult(
            prediction_date=prediction_date,
            cutoff_at=cutoff_at,
            start_date=start_date,
            end_date=end_date,
            status="SKIPPED",
            message="JPX休場日のため履歴prefetchをスキップしました。",
        )

    outcome = ingest_free_morning_data(
        factory,
        config,
        environment,
        prediction_date=prediction_date,
        start_date=start_date,
        end_date=end_date,
        include_snapshots=False,
    )
    report = outcome.report
    status, unexpected = _prefetch_status(report)
    return MorningPrefetchResult(
        prediction_date=prediction_date,
        cutoff_at=cutoff_at,
        start_date=start_date,
        end_date=end_date,
        status=status,
        run_id=outcome.run_id,
        ingestion_status=report.status,
        requested_sources=report.requested_sources,
        succeeded_sources=report.succeeded_sources,
        inserted_rows=report.inserted_rows,
        reused_rows=report.reused_rows,
        failed_sources=dict(report.failed_sources),
        skipped_sources=dict(report.skipped_sources),
        covered_sources=dict(report.covered_sources),
        unresolved_required=tuple(sorted(report.unresolved_required)),
        unexpected_skips=unexpected,
    )


def _exit_code(result: MorningPrefetchResult) -> int:
    return 0 if result.status in {"SUCCESS", "SKIPPED"} else 2


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config-dir", type=Path, default=Path("config"))
    parser.add_argument("--prediction-date", type=date.fromisoformat)
    parser.add_argument(
        "--force-non-business-day",
        action="store_true",
        help=(
            "Prefetch history for a JPX holiday. The resulting prediction is a "
            "reference figure only: the session it names never opens."
        ),
    )
    return parser


def main() -> int:
    args = _parser().parse_args()
    engine: Engine | None = None
    prediction_date = args.prediction_date
    try:
        config, environment, engine, factory = load_runtime(args.config_dir)
        prediction_date = prediction_date or today_in_application_timezone(config)
        result = run_prefetch(
            factory,
            config,
            environment,
            prediction_date=prediction_date,
            allow_non_business_day=args.force_non_business_day,
        )
        print(json.dumps(asdict(result), ensure_ascii=False, default=str))
        return _exit_code(result)
    except Exception as exc:
        # Never print exception text: provider and database exceptions can embed
        # credentials or connection URLs. The class is enough for the audit log.
        print(
            json.dumps(
                {
                    "status": "FAILED",
                    "prediction_date": (
                        prediction_date.isoformat()
                        if prediction_date is not None
                        else None
                    ),
                    "error_class": type(exc).__name__,
                },
                ensure_ascii=False,
            )
        )
        return 2
    finally:
        if engine is not None:
            engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
