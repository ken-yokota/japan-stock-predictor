"""Free-provider ingestion used by the scheduled morning pipeline."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session, sessionmaker

from data.availability import prediction_cutoff
from data.config import AppConfig
from data.env import EnvironmentSettings
from data.fetch import IngestionReport, build_fetch_plan, execute_fetch_plan
from data.providers.eodhd import EODHDFreeProvider
from data.providers.treasury import TreasuryProvider
from data.providers.yahoo import YahooFinanceProvider
from database.repository import MarketDataRepository
from services.versioning import config_hash


@dataclass(frozen=True, slots=True)
class IngestionOutcome:
    run_id: str
    report: IngestionReport


def ingest_free_morning_data(
    factory: sessionmaker[Session],
    config: AppConfig,
    environment: EnvironmentSettings,
    *,
    prediction_date: date,
    start_date: date,
    end_date: date,
    include_snapshots: bool = True,
) -> IngestionOutcome:
    """Fetch Yahoo/Treasury and optional quota-capped EODHD before cutoff."""

    if start_date > end_date:
        raise ValueError("start_date must not be after end_date")
    settings = config.settings.provider
    yahoo = YahooFinanceProvider(
        timeout_seconds=environment.http_timeout_seconds,
        max_retries=settings.max_retries,
        backoff_seconds=settings.backoff_initial_seconds,
    )
    treasury = TreasuryProvider(
        timeout_seconds=environment.http_timeout_seconds,
        max_retries=settings.max_retries,
        backoff_seconds=settings.backoff_initial_seconds,
    )
    eodhd_secret = (
        environment.eodhd_api_key.get_secret_value()
        if environment.eodhd_api_key is not None
        else ""
    )
    eodhd = (
        EODHDFreeProvider(
            eodhd_secret,
            base_url=environment.eodhd_base_url,
            timeout_seconds=environment.http_timeout_seconds,
            max_retries=settings.max_retries,
            backoff_seconds=settings.backoff_initial_seconds,
            max_calls_per_run=settings.eodhd_free_max_calls_per_run,
        )
        if eodhd_secret
        else None
    )
    providers = {
        "yahoo_finance": yahoo,
        **({"eodhd_free": eodhd} if eodhd is not None else {}),
    }
    cutoff_at = prediction_cutoff(prediction_date)
    plan = build_fetch_plan(config)
    session = factory()
    repository = MarketDataRepository(session)
    run = repository.create_run(
        run_type="INGESTION",
        prediction_date=prediction_date,
        cutoff_at=cutoff_at,
        data_version=config_hash(config),
    )
    batch = repository.create_ingestion_batch(
        run_id=run.run_id,
        provider="free_provider_stack",
        requested_symbols=plan.external_target_count,
    )
    session.commit()
    try:
        report = execute_fetch_plan(
            plan,
            market_providers=providers,
            treasury_provider=treasury,
            repository=repository,
            start_date=start_date,
            end_date=end_date,
            cutoff_at=cutoff_at,
            include_snapshots=include_snapshots,
            # The morning run only needs what is not already stored. A backfill
            # calls this with the flag off and still fetches everything.
            skip_covered=True,
            operational_run=True,
            run_id=run.run_id,
        )
        repository.finish_ingestion_batch(
            batch,
            status=report.status,
            succeeded_symbols=report.succeeded_sources,
            failed_symbols=list(report.failed_sources),
            inserted_rows=report.inserted_rows,
            reused_rows=report.reused_rows,
        )
        repository.finish_run(
            run,
            status=report.status,
            failed_symbols=list(report.failed_sources),
        )
        session.commit()
        return IngestionOutcome(run.run_id, report)
    except Exception as exc:
        session.rollback()
        run = session.merge(run)
        batch = session.merge(batch)
        repository.finish_ingestion_batch(
            batch,
            status="FAILED",
            succeeded_symbols=0,
            failed_symbols=[],
            inserted_rows=0,
            reused_rows=0,
        )
        repository.finish_run(
            run,
            status="FAILED",
            error_message=type(exc).__name__,
        )
        session.commit()
        raise
    finally:
        session.close()
        yahoo.close()
        treasury.close()
        if eodhd is not None:
            eodhd.close()


def today_in_application_timezone(config: AppConfig) -> date:
    """Return today's date using the configured business timezone."""

    return datetime.now(ZoneInfo(config.settings.application.timezone)).date()
