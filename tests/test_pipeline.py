"""End-to-end orchestration tests using only SQLite and local fakes."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import TYPE_CHECKING

import pandas as pd
import pytest
import yaml
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

import pipeline.open as open_pipeline
import services.email as email_service
from data.availability import prediction_cutoff
from data.config import AppConfig, load_app_config
from data.env import EnvironmentSettings
from data.market_calendar import (
    japan_session_close,
    japan_sessions_before,
)
from data.schemas import (
    AvailabilityMethod,
    DataInterval,
    DataQuality,
    MarketBar,
    SessionOpenRequest,
)
from database.models import (
    ActualResult,
    Base,
    DailyRun,
    EmailLog,
    FeatureInput,
    MarketData,
    SimulatedTrade,
    StockPrice,
)
from database.repository import MarketDataRepository, PredictionPipelineRepository
from notifications.contracts import EmailDelivery, RenderedEmail
from pipeline.close import ClosePipeline
from pipeline.morning import MorningPipeline
from pipeline.open import OpenPipeline
from services.dataset import ModelDataset, ModelSample, SourceReference
from services.email import load_morning_email_payload, send_persisted_morning_email
from services.persistence import persist_feature_set, prediction_set_versions
from services.versioning import config_hash

if TYPE_CHECKING:
    from collections.abc import Iterator


PREDICTION_DATE = date(2026, 8, 12)
HOLIDAY = date(2026, 8, 11)  # Mountain Day
DIGEST = "a" * 64


@pytest.fixture
def app_config() -> AppConfig:
    return load_app_config()


@pytest.fixture
def sqlite_factory() -> Iterator[sessionmaker[Session]]:
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(engine, expire_on_commit=False)
    try:
        yield factory
    finally:
        engine.dispose()


def _environment() -> EnvironmentSettings:
    return EnvironmentSettings(
        email_provider="dry_run",
        email_from="sender@example.com",
        email_to="owner@example.com",
        app_url="https://dashboard.example.com",
    )


def _seed_prediction_set(
    factory: sessionmaker[Session],
    config: AppConfig,
    *,
    prediction_date: date = PREDICTION_DATE,
    with_buy: bool,
) -> str:
    cutoff = prediction_cutoff(prediction_date)
    training_sessions = japan_sessions_before(
        prediction_date, config.model.training.window_jpx_sessions
    )
    feature_version, model_version, strategy_version = prediction_set_versions()
    with factory() as session:
        market_repository = MarketDataRepository(session)
        repository = PredictionPipelineRepository(session)
        run = market_repository.create_run(
            run_type="MORNING",
            prediction_date=prediction_date,
            cutoff_at=cutoff,
            data_version=config_hash(config),
        )
        feature_set = repository.create_feature_set(
            run_id=run.run_id,
            ticker="9101",
            prediction_date=prediction_date,
            cutoff_at=cutoff,
            feature_version=feature_version,
            set_kind="MORNING",
            training_start=training_sessions[0],
            training_end=training_sessions[-1],
            config_hash=DIGEST,
            required_feature_count=0,
            idempotency_key=f"feature/test/{run.run_id}/9101",
        )
        repository.finalize_feature_set(
            feature_set,
            status="READY" if with_buy else "INSUFFICIENT_DATA",
            input_manifest_hash=DIGEST if with_buy else None,
        )
        prediction_set = repository.create_prediction_set(
            run_id=run.run_id,
            prediction_date=prediction_date,
            cutoff_at=cutoff,
            feature_version=feature_version,
            model_version=model_version,
            strategy_version=strategy_version,
            training_start=training_sessions[0],
            training_end=training_sessions[-1],
            idempotency_key=f"prediction-set/test/{run.run_id}",
        )
        if with_buy:
            models: dict[str, str] = {}
            for task, algorithm in (
                ("REGRESSION", "ridge"),
                ("CLASSIFICATION", "logistic_regression"),
            ):
                model = repository.create_model_run(
                    run_id=run.run_id,
                    ticker="9101",
                    feature_set_id=feature_set.feature_set_id,
                    task=task,
                    algorithm=algorithm,
                    training_start=training_sessions[0],
                    training_end=training_sessions[-1],
                    cutoff_at=cutoff,
                    training_rows=120,
                    feature_version=feature_version,
                    model_version=model_version,
                    random_seed=42,
                    parameters={"selected": 1.0},
                    cv_results={"strategy": "time_series_split"},
                    idempotency_key=f"model/test/{run.run_id}/{task}",
                )
                repository.add_model_coefficient(
                    model_run_id=model.model_run_id,
                    feature_name="sp500__return_1d",
                    coefficient=Decimal("0.1" if task == "REGRESSION" else "0.2"),
                    scaler_mean=Decimal("0"),
                    scaler_scale=Decimal("1"),
                )
                repository.finish_model_run(
                    model, status="SUCCESS", intercept=Decimal("0")
                )
                models[task] = model.model_run_id
            repository.add_prediction(
                prediction_set_id=prediction_set.prediction_set_id,
                ticker="9101",
                feature_set_id=feature_set.feature_set_id,
                regression_model_run_id=models["REGRESSION"],
                classification_model_run_id=models["CLASSIFICATION"],
                status="SUCCESS",
                predicted_intraday_return=Decimal("0.01"),
                probability_up=Decimal("0.75"),
                reference_stock_price_id=None,
                reference_price=Decimal("6000"),
                reference_basis="PREVIOUS_CLOSE",
                predicted_price_difference=Decimal("60"),
                predicted_close=Decimal("6060"),
                signal="BUY",
                rank=1,
                return_threshold=Decimal("0.003"),
                probability_threshold=Decimal("0.60"),
                confidence_score=Decimal("80"),
                positive_factors=["sp500__return_1d"],
                negative_factors=[],
                feature_coverage=1.0,
                idempotency_key=f"prediction/test/{run.run_id}/9101",
            )
            expected_tickers = {"9101"}
            terminal_status = "READY"
        else:
            expected_tickers = set()
            terminal_status = "INSUFFICIENT_DATA"
        repository.finalize_prediction_set(
            prediction_set,
            status=terminal_status,
            expected_tickers=expected_tickers,
        )
        market_repository.finish_run(run, status="SUCCESS")
        session.commit()
        return prediction_set.prediction_set_id


def _stock_revision(
    *,
    close: str,
    raw_hash: str,
    first_observed_at: datetime,
) -> StockPrice:
    market_close = japan_session_close(PREDICTION_DATE)
    return StockPrice(
        canonical_symbol="9101",
        symbol="9101.T",
        provider="yahoo_finance",
        market="JP",
        market_timezone="Asia/Tokyo",
        market_date=PREDICTION_DATE,
        timestamp=market_close,
        source_timestamp=market_close,
        available_timestamp=first_observed_at,
        first_observed_at=first_observed_at,
        retrieved_at=first_observed_at,
        last_seen_at=first_observed_at,
        interval="eod",
        availability_method="first_observed",
        data_quality="FREE_UNVERIFIED",
        is_realtime=False,
        is_delayed=True,
        open=Decimal("6000"),
        high=Decimal(max(Decimal(close), Decimal("6000"))),
        low=Decimal(min(Decimal(close), Decimal("6000"))),
        close=Decimal(close),
        adjusted_close=Decimal(close),
        volume=1_000_000,
        currency="JPY",
        raw_hash=raw_hash,
        quality_flags=["test_fake_provider"],
    )


def test_morning_skips_holiday_and_reuses_existing_terminal_set(
    sqlite_factory: sessionmaker[Session], app_config: AppConfig
) -> None:
    pipeline = MorningPipeline(sqlite_factory, app_config, _environment())

    skipped = pipeline.run(HOLIDAY, perform_ingestion=False)
    prediction_set_id = _seed_prediction_set(sqlite_factory, app_config, with_buy=False)
    reused = pipeline.run(PREDICTION_DATE, perform_ingestion=False)

    assert skipped.status == "SKIPPED"
    assert skipped.prediction_set_id is None
    assert reused.reused is True
    assert reused.status == "INSUFFICIENT_DATA"
    assert reused.prediction_set_id == prediction_set_id
    with sqlite_factory() as session:
        assert (
            session.scalar(
                select(func.count())
                .select_from(DailyRun)
                .where(DailyRun.prediction_date == PREDICTION_DATE)
            )
            == 1
        )


def test_persistence_rejects_feature_lineage_after_prediction_cutoff(
    sqlite_factory: sessionmaker[Session], app_config: AppConfig
) -> None:
    cutoff = prediction_cutoff(PREDICTION_DATE)
    source_time = cutoff + timedelta(minutes=1)
    sessions = japan_sessions_before(
        PREDICTION_DATE, app_config.model.training.window_jpx_sessions
    )
    with sqlite_factory() as session:
        source = MarketData(
            canonical_symbol="future_indicator",
            symbol="FUTURE",
            provider="yahoo_finance",
            market="TEST",
            market_timezone="UTC",
            market_date=PREDICTION_DATE,
            timestamp=source_time,
            source_timestamp=source_time,
            available_timestamp=source_time,
            first_observed_at=source_time,
            retrieved_at=source_time,
            last_seen_at=source_time,
            interval="snapshot",
            availability_method="first_observed",
            data_quality="FREE_UNVERIFIED",
            is_realtime=False,
            is_delayed=True,
            close=Decimal("1"),
            raw_hash="f" * 64,
            quality_flags=["future_test"],
        )
        session.add(source)
        session.flush()
        reference = SourceReference(
            table_name="market_data",
            row_id=source.id,
            canonical_symbol=source.canonical_symbol,
            market_date=source.market_date,
            available_at=source_time,
            first_observed_at=source_time,
            retrieved_at=source_time,
            raw_hash=source.raw_hash,
            data_quality=source.data_quality,
        )
        current = ModelSample(
            ticker="9101",
            sample_date=PREDICTION_DATE,
            cutoff_at=cutoff,
            values={"future_indicator__level": 1.0},
            lineage={"future_indicator__level": (reference,)},
        )
        dataset = ModelDataset(
            ticker="9101",
            feature_names=("future_indicator__level",),
            training_frame=pd.DataFrame(columns=["future_indicator__level"]),
            training_target=pd.Series(dtype=float),
            current_frame=pd.DataFrame(
                [{"future_indicator__level": 1.0}], index=[PREDICTION_DATE]
            ),
            training_samples=(),
            current_sample=current,
            candidate_feature_count=1,
            feature_coverage=1.0,
        )
        run = MarketDataRepository(session).create_run(
            run_type="MORNING",
            prediction_date=PREDICTION_DATE,
            cutoff_at=cutoff,
            data_version="config-test",
        )

        with pytest.raises(ValueError, match="raw input was unavailable"):
            persist_feature_set(
                PredictionPipelineRepository(session),
                run_id=run.run_id,
                prediction_date=PREDICTION_DATE,
                config=app_config,
                dataset=dataset,
                terminal_status="READY",
            )

        assert sessions[-1] < PREDICTION_DATE
        assert session.scalar(select(func.count()).select_from(FeatureInput)) == 0


class _FakeYahooOpenProvider:
    """Deterministic provider fake for the optional post-open observation."""

    def __init__(self, **_: object) -> None:
        pass

    def fetch_session_open(self, request: SessionOpenRequest) -> MarketBar:
        source_at = datetime(2026, 8, 12, 0, 0, tzinfo=UTC)
        observed_at = datetime(2026, 8, 12, 0, 5, tzinfo=UTC)
        return MarketBar(
            canonical_symbol=request.canonical_symbol,
            provider_symbol=request.provider_symbol,
            provider="yahoo_finance",
            market="JP",
            market_timezone=request.market_timezone,
            market_date=request.session_date,
            timestamp=source_at,
            source_timestamp=source_at,
            available_timestamp=observed_at,
            first_observed_at=observed_at,
            retrieved_at=observed_at,
            interval=DataInterval.ONE_MINUTE,
            availability_method=AvailabilityMethod.FIRST_OBSERVED,
            data_quality=DataQuality.DELAYED,
            is_realtime=False,
            is_delayed=True,
            open=Decimal("6000"),
            high=Decimal("6005"),
            low=Decimal("5998"),
            close=Decimal("6002"),
            volume=1_000,
            currency="JPY",
            raw_hash=f"{int(request.canonical_symbol):064x}",
            quality_flags=("fake_session_open",),
        )

    def close(self) -> None:
        pass


def test_open_pipeline_uses_first_observed_one_minute_bar(
    sqlite_factory: sessionmaker[Session],
    app_config: AppConfig,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _seed_prediction_set(sqlite_factory, app_config, with_buy=True)
    monkeypatch.setattr(open_pipeline, "YahooFinanceProvider", _FakeYahooOpenProvider)

    result = OpenPipeline(sqlite_factory, app_config, _environment()).run(
        PREDICTION_DATE,
        observed_at=datetime(2026, 8, 12, 0, 5, tzinfo=UTC),
    )

    assert result.status == "SUCCESS"
    assert result.observed == 1
    with sqlite_factory() as session:
        actual = session.scalar(select(ActualResult))
        assert actual is not None
        assert actual.status == "PENDING"
        assert actual.actual_open == Decimal("6000")
        stock = session.get(StockPrice, actual.stock_price_id)
        assert stock is not None
        assert stock.interval == "1m"
        assert stock.available_timestamp == stock.first_observed_at
        assert stock.timestamp <= stock.available_timestamp


def test_close_retry_finalizes_costed_board_lot_and_is_revision_idempotent(
    sqlite_factory: sessionmaker[Session], app_config: AppConfig
) -> None:
    _seed_prediction_set(sqlite_factory, app_config, with_buy=True)
    at_1545 = datetime(2026, 8, 12, 6, 45, tzinfo=UTC)
    at_1555 = datetime(2026, 8, 12, 6, 55, tzinfo=UTC)
    with sqlite_factory() as session:
        session.add(
            _stock_revision(close="6060", raw_hash="1" * 64, first_observed_at=at_1545)
        )
        session.commit()

    pipeline = ClosePipeline(sqlite_factory, app_config, _environment())
    first = pipeline.run(PREDICTION_DATE, observed_at=at_1545, fetch_data=False)
    with sqlite_factory() as session:
        stock = session.scalar(
            select(StockPrice).where(StockPrice.raw_hash == "1" * 64)
        )
        assert stock is not None
        stock.last_seen_at = at_1555
        session.commit()
    second = pipeline.run(PREDICTION_DATE, observed_at=at_1555, fetch_data=False)

    assert first.status == "PARTIAL"
    assert first.pending == 1
    assert second.status == "SUCCESS"
    assert second.finalized == 1
    with sqlite_factory() as session:
        results = list(
            session.scalars(select(ActualResult).order_by(ActualResult.result_version))
        )
        trade = session.scalar(select(SimulatedTrade))
        assert [row.status for row in results] == ["PENDING", "FINAL"]
        assert trade is not None
        assert trade.shares == 100
        assert float(trade.gross_profit_jpy or 0) == pytest.approx(6_000.0)
        assert float(trade.commission_cost_jpy or 0) == pytest.approx(602.9985)
        assert float(trade.slippage_cost_jpy or 0) == pytest.approx(603.0)
        assert float(trade.net_profit_jpy or 0) == pytest.approx(4_794.0015)

    at_1610 = datetime(2026, 8, 12, 7, 10, tzinfo=UTC)
    with sqlite_factory() as session:
        session.add(
            _stock_revision(close="6120", raw_hash="2" * 64, first_observed_at=at_1610)
        )
        session.commit()
    corrected = pipeline.run(PREDICTION_DATE, observed_at=at_1610, fetch_data=False)
    repeated = pipeline.run(PREDICTION_DATE, observed_at=at_1610, fetch_data=False)

    assert corrected.corrected == 1
    assert repeated.corrected == 0
    with sqlite_factory() as session:
        results = list(
            session.scalars(select(ActualResult).order_by(ActualResult.result_version))
        )
        assert [row.status for row in results] == ["PENDING", "FINAL", "CORRECTED"]
        assert results[-1].supersedes_actual_result_id == results[-2].actual_result_id
        assert session.scalar(select(func.count()).select_from(ActualResult)) == 3
        assert session.scalar(select(func.count()).select_from(SimulatedTrade)) == 2


class _FakeSender:
    name = "fake"

    def __init__(self) -> None:
        self.messages: list[RenderedEmail] = []

    def send(self, message: RenderedEmail) -> EmailDelivery:
        self.messages.append(message)
        return EmailDelivery(self.name, "fake-message-1", datetime.now(UTC))


def test_email_payload_and_database_claim_allow_exactly_one_send(
    sqlite_factory: sessionmaker[Session],
    app_config: AppConfig,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    prediction_set_id = _seed_prediction_set(sqlite_factory, app_config, with_buy=True)
    environment = _environment()
    with sqlite_factory() as session:
        stored_set, payload = load_morning_email_payload(
            session,
            app_config,
            prediction_date=PREDICTION_DATE,
            dashboard_url=environment.app_url,
        )
    assert stored_set.prediction_set_id == prediction_set_id
    assert len(payload.candidates) == 1
    assert payload.candidates[0].ticker == "9101"
    assert payload.candidates[0].signal == "BUY"
    assert payload.candidates[0].positive_factors == ("sp500__return_1d",)
    assert payload.dashboard_url == "https://dashboard.example.com"

    sender = _FakeSender()
    monkeypatch.setattr(email_service, "_sender", lambda _: sender)
    first = send_persisted_morning_email(
        sqlite_factory,
        app_config,
        environment,
        prediction_date=PREDICTION_DATE,
    )
    second = send_persisted_morning_email(
        sqlite_factory,
        app_config,
        environment,
        prediction_date=PREDICTION_DATE,
    )

    assert first is not None
    assert second is None
    assert len(sender.messages) == 1
    assert "日本郵船" in sender.messages[0].text
    assert "Dashboard: https://dashboard.example.com" in sender.messages[0].text
    with sqlite_factory() as session:
        log = session.scalar(select(EmailLog))
        assert log is not None
        assert log.status == "SENT"
        assert log.attempt_count == 1
        assert log.provider_message_id == "fake-message-1"


def test_scheduled_workflows_use_jst_equivalent_utc_crons_and_safety_gate() -> None:
    workflow_dir = Path(__file__).parents[1] / ".github" / "workflows"
    expected = {
        "morning_prediction.yml": ["20 23 * * 0-4"],
        "morning_email.yml": ["45,50,55 23 * * 0-4"],
        "close_update.yml": ["45 6 * * 1-5", "55 6 * * 1-5", "10 7 * * 1-5"],
    }
    for name, crons in expected.items():
        text = (workflow_dir / name).read_text(encoding="utf-8")
        parsed = yaml.load(text, Loader=yaml.BaseLoader)
        assert [item["cron"] for item in parsed["on"]["schedule"]] == crons
        assert "vars.AUTOMATION_ENABLED == 'true'" in text
        assert 'python-version: "3.12"' in text
