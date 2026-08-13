"""15:45+ JST outcome confirmation, paper P/L, and OOS metrics."""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from data.config import AppConfig
from data.env import EnvironmentSettings
from data.market_calendar import (
    is_japan_business_day,
    japan_session_close,
    japan_session_open,
)
from data.providers.base import ProviderError
from data.providers.yahoo import YahooFinanceProvider
from data.schemas import FetchRequest
from database.models import (
    ActualResult,
    DailyRun,
    ModelCoefficient,
    ModelRun,
    Prediction,
    PredictionSet,
    SimulatedTrade,
    StockPrice,
)
from database.repository import MarketDataRepository, PredictionPipelineRepository
from metrics.performance import calculate_performance_metrics
from scoring.readability import score_readability
from scoring.stability import (
    aggregate_coefficient_stability,
    calculate_coefficient_stability,
)
from services.versioning import (
    MODEL_VERSION,
    STRATEGY_VERSION,
    config_hash,
    sha256_json,
)
from trading.strategy import ExecutionConfig, simulate_intraday_trade


@dataclass(frozen=True, slots=True)
class ClosePipelineResult:
    prediction_date: date
    status: str
    run_id: str
    finalized: int = 0
    pending: int = 0
    corrected: int = 0
    failed_tickers: tuple[str, ...] = ()


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _decimal(value: float | None) -> Decimal | None:
    if value is None or not math.isfinite(value):
        return None
    return Decimal(str(value))


def _latest_stock_row(
    session: Session, ticker: str, prediction_date: date
) -> StockPrice | None:
    return session.scalar(
        select(StockPrice)
        .where(
            StockPrice.canonical_symbol == ticker,
            StockPrice.market_date == prediction_date,
            StockPrice.interval == "eod",
        )
        .order_by(
            StockPrice.available_timestamp.desc(),
            StockPrice.first_observed_at.desc(),
            StockPrice.last_seen_at.desc(),
            StockPrice.id.desc(),
        )
        .limit(1)
    )


def _latest_actual(session: Session, prediction_id: str) -> ActualResult | None:
    return session.scalar(
        select(ActualResult)
        .where(ActualResult.prediction_id == prediction_id)
        .order_by(ActualResult.result_version.desc())
        .limit(1)
    )


def _fetch_close_rows(
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
    failures: dict[str, str] = {}
    repository = MarketDataRepository(session)
    stock_symbols = {stock.ticker for stock in config.stocks.stocks if stock.enabled}
    try:
        for stock in config.stocks.stocks:
            if not stock.enabled:
                continue
            symbol = stock.provider_symbols.get("yahoo_finance")
            if symbol is None:
                failures[stock.ticker] = "Yahoo symbol is unresolved"
                continue
            try:
                rows = provider.fetch_eod(
                    FetchRequest(
                        canonical_symbol=stock.ticker,
                        provider_symbol=symbol,
                        market="JP",
                        market_timezone=stock.market_timezone,
                        market_close="15:30",
                        availability_lag_minutes=20,
                        start_date=prediction_date,
                        end_date=prediction_date,
                        currency="JPY",
                    )
                )
                matching = [row for row in rows if row.market_date == prediction_date]
                if not matching:
                    failures[stock.ticker] = "current JPX EOD row is not published"
                    continue
                repository.upsert_bars(
                    matching,
                    stock_symbols=stock_symbols,
                )
            except (ProviderError, ValueError) as exc:
                failures[stock.ticker] = str(exc)[:300]
        session.flush()
        return failures
    finally:
        provider.close()


def _latest_prediction_set(
    session: Session, prediction_date: date
) -> PredictionSet | None:
    # Only a live morning is scored. A reference prediction names a session
    # that never opened, so settling one would put a day the market was closed
    # into the track record.
    return session.scalar(
        select(PredictionSet)
        .join(DailyRun, DailyRun.run_id == PredictionSet.run_id)
        .where(
            PredictionSet.prediction_date == prediction_date,
            PredictionSet.status.in_(("READY", "INSUFFICIENT_DATA")),
            DailyRun.run_type == "MORNING",
        )
        .order_by(PredictionSet.generated_at.desc())
        .limit(1)
    )


def _coefficient_stability(session: Session, ticker: str) -> float:
    model_runs = list(
        session.scalars(
            select(ModelRun)
            .where(
                ModelRun.ticker == ticker,
                ModelRun.task == "REGRESSION",
                ModelRun.status == "SUCCESS",
            )
            .order_by(ModelRun.finished_at.desc())
            .limit(20)
        )
    )
    history: list[dict[str, float]] = []
    for model_run in reversed(model_runs):
        coefficients = list(
            session.scalars(
                select(ModelCoefficient).where(
                    ModelCoefficient.model_run_id == model_run.model_run_id
                )
            )
        )
        history.append(
            {row.feature_name: float(row.coefficient) for row in coefficients}
        )
    return aggregate_coefficient_stability(calculate_coefficient_stability(history))


def _update_metrics(
    session: Session,
    repository: PredictionPipelineRepository,
    config: AppConfig,
    ticker: str,
    as_of_date: date,
) -> None:
    predictions = list(
        session.scalars(
            select(Prediction)
            .join(PredictionSet)
            .where(
                Prediction.ticker == ticker,
                Prediction.status == "SUCCESS",
                PredictionSet.prediction_date <= as_of_date,
            )
            .order_by(PredictionSet.prediction_date)
        )
    )
    actuals: list[ActualResult] = []
    prediction_rows: list[Prediction] = []
    trades: list[SimulatedTrade] = []
    for prediction in predictions:
        actual = _latest_actual(session, prediction.prediction_id)
        if actual is None or actual.status not in {"FINAL", "CORRECTED"}:
            continue
        actuals.append(actual)
        prediction_rows.append(prediction)
        trade = session.scalar(
            select(SimulatedTrade)
            .where(
                SimulatedTrade.prediction_id == prediction.prediction_id,
                SimulatedTrade.actual_result_id == actual.actual_result_id,
                SimulatedTrade.status == "FINAL",
            )
            .order_by(SimulatedTrade.created_at.desc())
            .limit(1)
        )
        if trade is not None:
            trades.append(trade)

    net_profits = [float(item.net_profit_jpy or 0) for item in trades]
    trade_returns = [float(item.realized_return or 0) for item in trades]
    predicted_returns = [
        float(item.predicted_intraday_return or 0) for item in prediction_rows
    ]
    actual_returns = [float(item.actual_intraday_return or 0) for item in actuals]
    capital = float(config.trading.position.capital_per_stock_jpy)
    metrics = calculate_performance_metrics(
        net_profits,
        trade_returns=trade_returns,
        predicted_returns=predicted_returns,
        actual_returns=actual_returns,
        capital_per_trade=capital,
    )
    stability = _coefficient_stability(session, ticker)
    readability = score_readability(
        profit_factor=metrics.profit_factor,
        win_rate=metrics.win_rate,
        prediction_correlation=metrics.pearson_correlation,
        direction_accuracy=metrics.direction_accuracy,
        coefficient_stability=stability,
        number_of_trades=metrics.number_of_trades,
    )
    manifest = [
        {
            "actual_result_id": actual.actual_result_id,
            "version": actual.result_version,
            "raw_hash": actual.raw_hash,
        }
        for actual in actuals
    ]
    manifest_hash = sha256_json(manifest)
    metric_values = {
        "win_rate": _decimal(metrics.win_rate),
        "gross_profit_jpy": _decimal(metrics.gross_profit),
        "gross_loss_jpy": _decimal(metrics.gross_loss),
        "net_profit_jpy": _decimal(metrics.net_profit),
        "average_win_jpy": _decimal(metrics.average_win),
        "average_loss_jpy": _decimal(metrics.average_loss),
        "largest_win_jpy": _decimal(metrics.largest_win),
        "largest_loss_jpy": _decimal(metrics.largest_loss),
        "payoff_ratio": _decimal(metrics.payoff_ratio),
        "profit_factor": _decimal(metrics.profit_factor),
        "expectancy_jpy": _decimal(metrics.expectancy),
        "sharpe_ratio": _decimal(metrics.sharpe_ratio),
        "sortino_ratio": _decimal(metrics.sortino_ratio),
        "max_drawdown": _decimal(metrics.maximum_drawdown),
        "pearson_correlation": _decimal(metrics.pearson_correlation),
        "spearman_correlation": _decimal(metrics.spearman_correlation),
        "direction_accuracy": _decimal(metrics.direction_accuracy),
        "readability_score": _decimal(readability.score),
    }
    trade_count = metrics.number_of_trades
    sample_status = (
        "NO_TRADES"
        if trade_count == 0
        else ("LOW_SAMPLE" if trade_count < 20 else "SUFFICIENT")
    )
    repository.save_metric_snapshot(
        ticker=ticker,
        as_of_date=as_of_date,
        model_version=MODEL_VERSION,
        strategy_version=STRATEGY_VERSION,
        evaluation_window=f"live_oos_{manifest_hash[:12]}",
        status="READY" if actuals else "INSUFFICIENT_DATA",
        sample_status=sample_status,
        prediction_count=len(actuals),
        trade_count=trade_count,
        win_count=metrics.wins,
        loss_count=metrics.losses,
        metrics=metric_values,
        input_manifest_hash=manifest_hash,
        idempotency_key=f"metrics/{ticker}/{as_of_date}/{manifest_hash[:16]}",
        details={
            "coefficient_stability": stability,
            "readability_sample_penalty": readability.sample_penalty,
            "is_out_of_sample": True,
        },
    )


class ClosePipeline:
    """Fetch and finalize current raw Open/Close without placing any order."""

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
    ) -> ClosePipelineResult:
        now = (observed_at or datetime.now(UTC)).astimezone(UTC)
        session = self._factory()
        market_repository = MarketDataRepository(session)
        repository = PredictionPipelineRepository(session)
        run = market_repository.create_run(
            run_type="CLOSE",
            prediction_date=prediction_date,
            cutoff_at=None,
            data_version=config_hash(self._config),
        )
        if not is_japan_business_day(prediction_date):
            market_repository.finish_run(run, status="SKIPPED")
            session.commit()
            session.close()
            return ClosePipelineResult(prediction_date, "SKIPPED", run.run_id)

        prediction_set = _latest_prediction_set(session, prediction_date)
        if prediction_set is None:
            market_repository.finish_run(run, status="SKIPPED")
            session.commit()
            session.close()
            return ClosePipelineResult(prediction_date, "NO_PREDICTION_SET", run.run_id)
        failures: dict[str, str] = {}
        try:
            if fetch_data:
                failures.update(
                    _fetch_close_rows(
                        session,
                        self._config,
                        self._environment,
                        prediction_date,
                    )
                )
                session.commit()
            predictions = list(
                session.scalars(
                    select(Prediction).where(
                        Prediction.prediction_set_id == prediction_set.prediction_set_id
                    )
                )
            )
            confirmed_after = japan_session_close(prediction_date) + timedelta(
                minutes=20
            )
            session_open = japan_session_open(prediction_date)
            session_close = japan_session_close(prediction_date)
            final_count = 0
            pending_count = 0
            corrected_count = 0
            metric_tickers: set[str] = set()
            costs = self._config.trading.costs
            position = self._config.trading.position
            if (
                costs.commission_bps_per_side is None
                or costs.slippage_bps_per_side is None
                or position.lot_size is None
            ):
                raise ValueError("paper-trading assumptions are not confirmed")
            execution = ExecutionConfig(
                capital_per_stock=float(position.capital_per_stock_jpy),
                lot_size=position.lot_size,
                commission_bps=costs.commission_bps_per_side,
                slippage_bps=costs.slippage_bps_per_side,
            )
            for prediction in predictions:
                stock = _latest_stock_row(session, prediction.ticker, prediction_date)
                prior = _latest_actual(session, prediction.prediction_id)
                confirmed = (
                    stock is not None
                    and stock.open is not None
                    and stock.close is not None
                    and now >= confirmed_after
                    and _utc(stock.last_seen_at) >= confirmed_after
                )
                if not confirmed:
                    pending_count += 1
                    if prior is None:
                        repository.save_actual_result(
                            prediction_id=prediction.prediction_id,
                            stock_price_id=stock.id if stock is not None else None,
                            supersedes_actual_result_id=None,
                            result_version=1,
                            status="PENDING",
                            actual_open=stock.open if stock is not None else None,
                            actual_close=None,
                            observed_at=now,
                            finalized_at=None,
                            idempotency_key=f"actual/{prediction.prediction_id}/pending",
                        )
                    continue
                assert stock is not None and stock.open is not None
                if (
                    prior is not None
                    and prior.status in {"FINAL", "CORRECTED"}
                    and prior.raw_hash == stock.raw_hash
                ):
                    final_count += 1
                    metric_tickers.add(prediction.ticker)
                    continue
                status = (
                    "CORRECTED"
                    if prior is not None and prior.status in {"FINAL", "CORRECTED"}
                    else "FINAL"
                )
                actual = repository.save_actual_result(
                    prediction_id=prediction.prediction_id,
                    stock_price_id=stock.id,
                    supersedes_actual_result_id=(
                        prior.actual_result_id if prior is not None else None
                    ),
                    result_version=(prior.result_version + 1 if prior else 1),
                    status=status,
                    actual_open=stock.open,
                    actual_close=stock.close,
                    observed_at=now,
                    finalized_at=now,
                    idempotency_key=(
                        f"actual/{prediction.prediction_id}/{stock.raw_hash}"
                    ),
                )
                should_execute = (
                    prediction.status == "SUCCESS" and prediction.signal == "BUY"
                )
                trade = simulate_intraday_trade(
                    float(stock.open),
                    float(stock.close),
                    execute=should_execute,
                    config=execution,
                )
                trade_status = "FINAL" if trade.is_buy else "NOT_TRIGGERED"
                repository.save_simulated_trade(
                    prediction_id=prediction.prediction_id,
                    actual_result_id=actual.actual_result_id,
                    status=trade_status,
                    capital_jpy=Decimal(str(execution.capital_per_stock)),
                    shares=trade.shares,
                    entry_price=(
                        Decimal(str(trade.execution_open)) if trade.is_buy else None
                    ),
                    exit_price=(
                        Decimal(str(trade.execution_close)) if trade.is_buy else None
                    ),
                    gross_profit_jpy=(
                        Decimal(str(trade.gross_profit)) if trade.is_buy else Decimal(0)
                    ),
                    commission_cost_jpy=(
                        Decimal(str(trade.commission_cost))
                        if trade.is_buy
                        else Decimal(0)
                    ),
                    slippage_cost_jpy=(
                        Decimal(str(trade.slippage_cost))
                        if trade.is_buy
                        else Decimal(0)
                    ),
                    net_profit_jpy=(
                        Decimal(str(trade.net_profit)) if trade.is_buy else Decimal(0)
                    ),
                    realized_return=(
                        Decimal(str(trade.return_on_capital))
                        if trade.is_buy
                        else Decimal(0)
                    ),
                    opened_at=session_open if trade.is_buy else None,
                    closed_at=session_close if trade.is_buy else None,
                    strategy_version=STRATEGY_VERSION,
                    idempotency_key=(
                        f"trade/{prediction.prediction_id}/{actual.actual_result_id}/"
                        f"{STRATEGY_VERSION}"
                    ),
                )
                final_count += 1
                corrected_count += status == "CORRECTED"
                metric_tickers.add(prediction.ticker)

            for ticker in sorted(metric_tickers):
                _update_metrics(
                    session,
                    repository,
                    self._config,
                    ticker,
                    prediction_date,
                )
            status = "SUCCESS" if pending_count == 0 else "PARTIAL"
            market_repository.finish_run(
                run,
                status=status,
                failed_symbols=list(failures),
            )
            session.commit()
            return ClosePipelineResult(
                prediction_date,
                status,
                run.run_id,
                final_count,
                pending_count,
                corrected_count,
                tuple(sorted(failures)),
            )
        except Exception as exc:
            session.rollback()
            run = session.merge(run)
            market_repository.finish_run(
                run,
                status="FAILED",
                failed_symbols=list(failures),
                error_message=type(exc).__name__,
            )
            session.commit()
            raise
        finally:
            session.close()
