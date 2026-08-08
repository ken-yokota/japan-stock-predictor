"""Optional post-open observation capture for the dashboard."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from data.config import AppConfig
from data.env import EnvironmentSettings
from data.providers.base import ProviderError
from data.providers.yahoo import YahooFinanceProvider
from data.schemas import SessionOpenRequest
from database.models import Prediction, StockPrice
from database.repository import MarketDataRepository, PredictionPipelineRepository
from pipeline.close import (
    _latest_actual,
    _latest_prediction_set,
)
from services.versioning import config_hash


@dataclass(frozen=True, slots=True)
class OpenPipelineResult:
    prediction_date: date
    status: str
    run_id: str
    observed: int
    missing: int


def _latest_open_row(
    session: Session, ticker: str, prediction_date: date
) -> StockPrice | None:
    return session.scalar(
        select(StockPrice)
        .where(
            StockPrice.canonical_symbol == ticker,
            StockPrice.market_date == prediction_date,
            StockPrice.interval == "1m",
        )
        .order_by(
            StockPrice.available_timestamp.desc(),
            StockPrice.first_observed_at.desc(),
            StockPrice.id.desc(),
        )
        .limit(1)
    )


def _fetch_open_rows(
    session: Session,
    config: AppConfig,
    environment: EnvironmentSettings,
    prediction_date: date,
) -> dict[str, str]:
    settings = config.settings.provider
    provider = YahooFinanceProvider(
        timeout_seconds=environment.http_timeout_seconds,
        max_retries=settings.max_retries,
        backoff_seconds=settings.backoff_initial_seconds,
    )
    repository = MarketDataRepository(session)
    stock_symbols = {stock.ticker for stock in config.stocks.stocks if stock.enabled}
    failures: dict[str, str] = {}
    try:
        for stock in config.stocks.stocks:
            if not stock.enabled:
                continue
            symbol = stock.provider_symbols.get("yahoo_finance")
            if symbol is None:
                failures[stock.ticker] = "Yahoo symbol is unresolved"
                continue
            try:
                bar = provider.fetch_session_open(
                    SessionOpenRequest(
                        canonical_symbol=stock.ticker,
                        provider_symbol=symbol,
                        market="JP",
                        market_timezone=stock.market_timezone,
                        session_date=prediction_date,
                        session_open="09:00",
                        currency="JPY",
                    )
                )
                repository.upsert_bars([bar], stock_symbols=stock_symbols)
            except (ProviderError, ValueError) as exc:
                failures[stock.ticker] = str(exc)[:300]
        session.flush()
        return failures
    finally:
        provider.close()


class OpenPipeline:
    """Store raw actual Open as PENDING; never overwrite a prediction."""

    def __init__(
        self,
        factory: sessionmaker[Session],
        config: AppConfig,
        environment: EnvironmentSettings,
    ) -> None:
        self._factory = factory
        self._config = config
        self._environment = environment

    def run(
        self,
        prediction_date: date,
        *,
        observed_at: datetime | None = None,
        fetch_data: bool = True,
    ) -> OpenPipelineResult:
        now = (observed_at or datetime.now(UTC)).astimezone(UTC)
        with self._factory() as session:
            market_repository = MarketDataRepository(session)
            repository = PredictionPipelineRepository(session)
            run = market_repository.create_run(
                run_type="OPEN",
                prediction_date=prediction_date,
                data_version=config_hash(self._config),
            )
            prediction_set = _latest_prediction_set(session, prediction_date)
            if prediction_set is None:
                market_repository.finish_run(run, status="SKIPPED")
                session.commit()
                return OpenPipelineResult(
                    prediction_date, "NO_PREDICTION_SET", run.run_id, 0, 0
                )
            failures = (
                _fetch_open_rows(
                    session,
                    self._config,
                    self._environment,
                    prediction_date,
                )
                if fetch_data
                else {}
            )
            predictions = list(
                session.scalars(
                    select(Prediction).where(
                        Prediction.prediction_set_id == prediction_set.prediction_set_id
                    )
                )
            )
            observed = 0
            missing = 0
            for prediction in predictions:
                if _latest_actual(session, prediction.prediction_id) is not None:
                    continue
                stock = _latest_open_row(session, prediction.ticker, prediction_date)
                if stock is None or stock.open is None:
                    missing += 1
                    continue
                repository.save_actual_result(
                    prediction_id=prediction.prediction_id,
                    stock_price_id=stock.id,
                    supersedes_actual_result_id=None,
                    result_version=1,
                    status="PENDING",
                    actual_open=stock.open,
                    actual_close=None,
                    observed_at=now,
                    finalized_at=None,
                    idempotency_key=f"actual/{prediction.prediction_id}/pending",
                )
                observed += 1
            status = "SUCCESS" if missing == 0 and not failures else "PARTIAL"
            market_repository.finish_run(
                run,
                status=status,
                failed_symbols=[
                    *failures,
                    *(["OPEN_MISSING"] if missing else []),
                ],
            )
            session.commit()
            return OpenPipelineResult(
                prediction_date, status, run.run_id, observed, missing
            )
