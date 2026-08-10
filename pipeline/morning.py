"""08:20 JST ingestion, PIT feature build, training, and publication."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from data.availability import prediction_cutoff
from data.config import AppConfig
from data.env import EnvironmentSettings
from data.market_calendar import is_japan_business_day, japan_sessions_before
from database.models import DailyRun, PredictionSet
from database.repository import MarketDataRepository, PredictionPipelineRepository
from services.dataset import PointInTimeDatasetBuilder
from services.ingestion import IngestionOutcome, ingest_free_morning_data
from services.persistence import (
    persist_failed_prediction,
    persist_prediction_computation,
    prediction_set_versions,
)
from services.prediction import PredictionComputation, PredictionService
from services.recovery import reconcile_stale_runs
from services.versioning import config_hash

# Marks a prediction whose named session never opens. Consumers key off this
# exact string, so evaluation can exclude the set from the live record and the
# email and dashboard can label it, without any of them consulting a calendar.
NON_TRADING_DAY_WARNING = (
    "JPX休場日のため参考予測です。この日は取引が成立せず、実績は発生しません。"
)


@dataclass(frozen=True, slots=True)
class MorningPipelineResult:
    prediction_date: date
    status: str
    run_id: str
    prediction_set_id: str | None
    successful_tickers: tuple[str, ...] = ()
    insufficient_tickers: tuple[str, ...] = ()
    failed_tickers: tuple[str, ...] = ()
    reused: bool = False
    ingestion_run_id: str | None = None
    warnings: tuple[str, ...] = ()


def _safe_error(exc: Exception) -> str:
    if isinstance(exc, ValueError):
        return str(exc)[:500]
    return type(exc).__name__


class MorningPipeline:
    """Coordinate free ingestion and atomically publish one prediction set."""

    def __init__(
        self,
        factory: sessionmaker[Session],
        config: AppConfig,
        environment: EnvironmentSettings,
    ) -> None:
        self._factory = factory
        self._config = config
        self._environment = environment

    def _existing(self, prediction_date: date) -> PredictionSet | None:
        feature_version, model_version, strategy_version = prediction_set_versions()
        with self._factory() as session:
            return session.scalar(
                select(PredictionSet)
                .join(DailyRun, DailyRun.run_id == PredictionSet.run_id)
                .where(
                    PredictionSet.prediction_date == prediction_date,
                    PredictionSet.feature_version == feature_version,
                    PredictionSet.model_version == model_version,
                    PredictionSet.strategy_version == strategy_version,
                    PredictionSet.status.in_(("READY", "INSUFFICIENT_DATA")),
                    DailyRun.data_version == config_hash(self._config),
                )
                .order_by(PredictionSet.generated_at.desc())
                .limit(1)
            )

    def _skip(self, prediction_date: date) -> MorningPipelineResult:
        with self._factory() as session:
            repository = MarketDataRepository(session)
            run = repository.create_run(
                run_type="MORNING",
                prediction_date=prediction_date,
                cutoff_at=prediction_cutoff(prediction_date),
                data_version=config_hash(self._config),
            )
            repository.finish_run(run, status="SKIPPED")
            session.commit()
            return MorningPipelineResult(
                prediction_date,
                "SKIPPED",
                run.run_id,
                None,
                warnings=("JPX休場日のため処理をスキップしました。",),
            )

    def run(
        self,
        prediction_date: date,
        *,
        perform_ingestion: bool = True,
        history_days: int = 550,
        allow_non_business_day: bool = False,
    ) -> MorningPipelineResult:
        if history_days < 250:
            raise ValueError("history_days must be at least 250")
        is_business_day = is_japan_business_day(prediction_date)
        if not is_business_day and not allow_non_business_day:
            return self._skip(prediction_date)

        recovery = reconcile_stale_runs(self._factory)
        recovery_warning = (
            f"recovered {recovery.recovered} stale in-progress audit rows"
            if recovery.recovered
            else None
        )
        # A forced run names a session that never opens, so no actual can ever
        # settle against it. The warning rides on the prediction set itself so
        # the evaluation, the email, and the dashboard all read the same fact
        # rather than each re-deriving it from a calendar.
        holiday_warning = NON_TRADING_DAY_WARNING if not is_business_day else None
        preamble = tuple(
            warning for warning in (holiday_warning, recovery_warning) if warning
        )
        existing = self._existing(prediction_date)
        if existing is not None:
            return MorningPipelineResult(
                prediction_date,
                existing.status,
                existing.run_id,
                existing.prediction_set_id,
                reused=True,
                warnings=preamble,
            )

        warnings: list[str] = list(preamble)
        ingestion: IngestionOutcome | None = None
        if perform_ingestion:
            try:
                ingestion = ingest_free_morning_data(
                    self._factory,
                    self._config,
                    self._environment,
                    prediction_date=prediction_date,
                    start_date=prediction_date - timedelta(days=history_days),
                    # The current JPX session has not completed at 08:20.
                    end_date=prediction_date - timedelta(days=1),
                )
            except Exception as exc:
                warnings.append(f"free ingestion failed: {_safe_error(exc)}")
            else:
                if ingestion.report.status != "SUCCESS":
                    warnings.append(f"free ingestion status: {ingestion.report.status}")
                if ingestion.report.unresolved_required:
                    warnings.append(
                        "unresolved indicators: "
                        + ", ".join(ingestion.report.unresolved_required)
                    )

        feature_version, model_version, strategy_version = prediction_set_versions()
        sessions = japan_sessions_before(
            prediction_date, self._config.model.training.window_jpx_sessions
        )
        session = self._factory()
        market_repository = MarketDataRepository(session)
        repository = PredictionPipelineRepository(session)
        run = market_repository.create_run(
            run_type="MORNING",
            prediction_date=prediction_date,
            cutoff_at=prediction_cutoff(prediction_date),
            data_version=config_hash(self._config),
        )
        run.model_version = model_version
        step = repository.start_run_step(
            run_id=run.run_id,
            step_name="BUILD_TRAIN_PREDICT",
            attempt_number=1,
        )
        prediction_set = repository.create_prediction_set(
            run_id=run.run_id,
            prediction_date=prediction_date,
            cutoff_at=prediction_cutoff(prediction_date),
            feature_version=feature_version,
            model_version=model_version,
            strategy_version=strategy_version,
            training_start=sessions[0],
            training_end=sessions[-1],
            warnings=warnings,
            idempotency_key=f"prediction-set/{prediction_date}/{run.run_id}",
        )
        session.commit()

        tickers = tuple(
            stock.ticker for stock in self._config.stocks.stocks if stock.enabled
        )
        computations: dict[str, PredictionComputation] = {}
        failures: dict[str, str] = {}
        try:
            prediction_service = PredictionService(
                PointInTimeDatasetBuilder(session, self._config), self._config
            )
            for ticker in tickers:
                try:
                    computations[ticker] = prediction_service.compute(
                        ticker, prediction_date
                    )
                except Exception as exc:
                    failures[ticker] = _safe_error(exc)

            ranked = sorted(
                (
                    computation.result
                    for computation in computations.values()
                    if computation.result.status == "READY"
                    and computation.result.signal == "BUY"
                    and computation.result.predicted_return is not None
                    and computation.result.probability_up is not None
                ),
                key=lambda item: (
                    -float(item.predicted_return or 0.0),
                    -float(item.probability_up or 0.0),
                    item.ticker,
                ),
            )
            rank_by_ticker = {
                item.ticker: rank for rank, item in enumerate(ranked, start=1)
            }
            for ticker, computation in computations.items():
                persist_prediction_computation(
                    repository,
                    run_id=run.run_id,
                    prediction_set=prediction_set,
                    computation=computation,
                    config=self._config,
                    rank=rank_by_ticker.get(ticker),
                )
            for ticker, reason in failures.items():
                persist_failed_prediction(
                    repository,
                    run_id=run.run_id,
                    prediction_set=prediction_set,
                    ticker=ticker,
                    prediction_date=prediction_date,
                    config=self._config,
                    reason=reason,
                )

            successful = tuple(
                sorted(
                    ticker
                    for ticker, computation in computations.items()
                    if computation.result.status == "READY"
                )
            )
            insufficient = tuple(
                sorted(
                    ticker
                    for ticker, computation in computations.items()
                    if computation.result.status != "READY"
                )
            )
            failed = tuple(sorted(failures))
            set_status = "READY" if successful else "INSUFFICIENT_DATA"
            repository.finalize_prediction_set(
                prediction_set,
                status=set_status,
                expected_tickers=set(tickers),
            )
            repository.finish_run_step(
                step,
                status="SUCCESS",
                details={
                    "successful": len(successful),
                    "insufficient": len(insufficient),
                    "failed": len(failed),
                },
            )
            run_status = "SUCCESS" if len(successful) == len(tickers) else "PARTIAL"
            market_repository.finish_run(
                run,
                status=run_status,
                failed_symbols=[*insufficient, *failed],
            )
            session.commit()
            return MorningPipelineResult(
                prediction_date,
                set_status,
                run.run_id,
                prediction_set.prediction_set_id,
                successful,
                insufficient,
                failed,
                ingestion_run_id=ingestion.run_id if ingestion else None,
                warnings=tuple(warnings),
            )
        except Exception as exc:
            session.rollback()
            run = session.merge(run)
            step = session.merge(step)
            prediction_set = session.merge(prediction_set)
            if step.status == "RUNNING":
                repository.finish_run_step(
                    step,
                    status="FAILED",
                    error_message=_safe_error(exc),
                )
            if prediction_set.status == "BUILDING":
                repository.finalize_prediction_set(
                    prediction_set,
                    status="FAILED",
                )
            market_repository.finish_run(
                run,
                status="FAILED",
                error_message=_safe_error(exc),
            )
            session.commit()
            raise
        finally:
            session.close()
