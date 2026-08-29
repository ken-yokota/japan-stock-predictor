"""End-to-end orchestration tests using only SQLite and local fakes."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import TYPE_CHECKING

import pandas as pd
import pytest
import yaml
from sqlalchemy import create_engine, event, func, select
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
    FeatureValue,
    MarketData,
    SimulatedTrade,
    StockPrice,
)
from database.repository import MarketDataRepository, PredictionPipelineRepository
from notifications.contracts import EmailDelivery, RenderedEmail
from pipeline.close import ClosePipeline
from pipeline.morning import (
    BACKFILL_WARNING,
    NON_TRADING_DAY_WARNING,
    MorningPipeline,
    MorningPipelineResult,
)
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


def test_feature_persistence_selects_scale_by_set_not_by_cell(
    sqlite_factory: sessionmaker[Session], app_config: AppConfig, make_bar
) -> None:
    """The hosted DB must not receive a SELECT for every persisted cell."""

    feature_names = tuple(f"factor_{index}" for index in range(5))
    sessions = japan_sessions_before(
        PREDICTION_DATE, app_config.model.training.window_jpx_sessions
    )
    raw_at = datetime(2026, 1, 5, 20, 0, tzinfo=UTC)
    with sqlite_factory() as session:
        market = MarketDataRepository(session)
        market.upsert_bars(
            [
                make_bar(
                    market_date=raw_at.date(),
                    timestamp=raw_at,
                    available_timestamp=raw_at,
                    first_observed_at=raw_at,
                    retrieved_at=raw_at,
                )
            ]
        )
        raw = session.scalar(select(MarketData))
        assert raw is not None
        reference = SourceReference(
            table_name="market_data",
            row_id=raw.id,
            canonical_symbol=raw.canonical_symbol,
            market_date=raw.market_date,
            available_at=raw.available_timestamp,
            first_observed_at=raw.first_observed_at,
            retrieved_at=raw.retrieved_at,
            raw_hash=raw.raw_hash,
            data_quality=raw.data_quality,
        )
        training_samples = tuple(
            ModelSample(
                ticker="9101",
                sample_date=session_date,
                cutoff_at=prediction_cutoff(session_date),
                values={
                    name: float(index + 1) for index, name in enumerate(feature_names)
                },
                lineage={name: (reference,) for name in feature_names},
                target_return=0.001,
                target_lineage=(reference,),
            )
            for session_date in sessions
        )
        current = ModelSample(
            ticker="9101",
            sample_date=PREDICTION_DATE,
            cutoff_at=prediction_cutoff(PREDICTION_DATE),
            values={name: float(index + 1) for index, name in enumerate(feature_names)},
            lineage={name: (reference,) for name in feature_names},
        )
        dataset = ModelDataset(
            ticker="9101",
            feature_names=feature_names,
            training_frame=pd.DataFrame(
                [sample.values for sample in training_samples], index=sessions
            ),
            training_target=pd.Series(
                [sample.target_return for sample in training_samples], index=sessions
            ),
            current_frame=pd.DataFrame([current.values], index=[PREDICTION_DATE]),
            training_samples=training_samples,
            current_sample=current,
            candidate_feature_count=len(feature_names),
            feature_coverage=1.0,
        )
        run = market.create_run(
            run_type="MORNING",
            prediction_date=PREDICTION_DATE,
            cutoff_at=prediction_cutoff(PREDICTION_DATE),
            data_version="config-test",
        )
        engine = session.get_bind()
        select_count = 0

        def count_selects(
            _connection, _cursor, statement, _parameters, _context, _executemany
        ) -> None:
            nonlocal select_count
            if statement.lstrip().upper().startswith("SELECT"):
                select_count += 1

        event.listen(engine, "before_cursor_execute", count_selects)
        try:
            feature_set = persist_feature_set(
                PredictionPipelineRepository(session),
                run_id=run.run_id,
                prediction_date=PREDICTION_DATE,
                config=app_config,
                dataset=dataset,
                terminal_status="READY",
            )
        finally:
            event.remove(engine, "before_cursor_execute", count_selects)

        # Only the scored row is stored now. The training cells are still
        # built, checked and folded into the manifest hash, but writing them
        # cost 400 MB of a 512 MB ceiling for rows nothing ever read.
        training_cells = len(sessions) * (len(feature_names) + 1)
        assert feature_set.status == "READY"
        assert feature_set.required_feature_count == len(feature_names)
        assert (
            feature_set.details["training_cells_validated_not_stored"] == training_cells
        )
        assert select_count <= 10


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


def _with_costs(config: AppConfig, *, commission: float, slippage: float) -> AppConfig:
    """The same config with explicit costs, whatever the file currently says.

    Production charges nothing since 2026-08-29 because no orders are placed.
    The cost arithmetic still has to be right -- it is one edit away from being
    live again, and it was four fifths of the recorded loss when it was on -- so
    the path is exercised here with costs stated in the test rather than read
    from a file that has since been zeroed.
    """

    return config.model_copy(
        update={
            "trading": config.trading.model_copy(
                update={
                    "costs": config.trading.costs.model_copy(
                        update={
                            "commission_bps_per_side": commission,
                            "slippage_bps_per_side": slippage,
                        }
                    )
                }
            )
        }
    )


def test_close_retry_finalizes_costed_board_lot_and_is_revision_idempotent(
    sqlite_factory: sessionmaker[Session], app_config: AppConfig
) -> None:
    app_config = _with_costs(app_config, commission=5.0, slippage=5.0)
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


def _pinned_python() -> str:
    """The interpreter the repository has declared, as one source of truth."""

    return (
        (Path(__file__).resolve().parents[1] / ".python-version")
        .read_text(encoding="utf-8")
        .strip()
    )


def test_scheduled_workflows_use_jst_equivalent_utc_crons_and_safety_gate() -> None:
    workflow_dir = Path(__file__).parents[1] / ".github" / "workflows"
    # UTC, with the JST time each one lands at. Prediction retries are
    # idempotent and the first one waits for the 08:20 snapshot window.
    expected = {
        "morning_prefetch.yml": ["10,25 22 * * 0-4"],  # 07:10/25 JST
        "morning_prediction.yml": ["10,20,30 23 * * 0-4"],  # 08:10/20/30 JST
        "morning_email.yml": [
            "45 23 * * 0-4",
            "50 23 * * 0-4",
            "55 23 * * 0-4",
        ],  # 08:45/50/55 JST
        "close_update.yml": [
            "45 6 * * 1-5",  # 15:45 JST
            "55 6 * * 1-5",  # 15:55 JST
            "10 7 * * 1-5",  # 16:10 JST
        ],
    }
    for name, crons in expected.items():
        text = (workflow_dir / name).read_text(encoding="utf-8")
        parsed = yaml.load(text, Loader=yaml.BaseLoader)
        assert [item["cron"] for item in parsed["on"]["schedule"]] == crons
        assert "vars.AUTOMATION_ENABLED == 'true'" in text
        # Read from .python-version rather than repeated here. Pinning the
        # literal meant every interpreter bump had to be made in two places,
        # and the workflows are the half that decides what production runs on.
        assert f'python-version: "{_pinned_python()}"' in text


def test_morning_command_exposes_partial_or_empty_publication_as_failure() -> None:
    from scripts.run_morning_prediction import _exit_code

    ready = MorningPipelineResult(
        PREDICTION_DATE,
        "READY",
        "run-ready",
        "set-ready",
        successful_tickers=("9101",),
    )
    partial = MorningPipelineResult(
        PREDICTION_DATE,
        "READY",
        "run-partial",
        "set-partial",
        successful_tickers=("9101",),
        insufficient_tickers=("9104",),
    )
    empty = MorningPipelineResult(
        PREDICTION_DATE,
        "INSUFFICIENT_DATA",
        "run-empty",
        "set-empty",
        insufficient_tickers=("9101", "9104"),
    )
    skipped = MorningPipelineResult(
        HOLIDAY,
        "SKIPPED",
        "run-holiday",
        None,
    )

    assert _exit_code(ready) == 0
    assert _exit_code(partial) == 2
    assert _exit_code(empty) == 2
    assert _exit_code(skipped) == 0


def test_forced_holiday_run_publishes_and_labels_the_set_as_reference_only(
    sqlite_factory: sessionmaker[Session], app_config: AppConfig
) -> None:
    """A holiday prediction must be produced, and must say it is not tradeable."""

    pipeline = MorningPipeline(sqlite_factory, app_config, _environment())

    forced = pipeline.run(HOLIDAY, perform_ingestion=False, allow_non_business_day=True)

    assert forced.status != "SKIPPED"
    assert NON_TRADING_DAY_WARNING in forced.warnings
    with sqlite_factory() as session:
        run = session.scalar(
            select(DailyRun).where(DailyRun.prediction_date == HOLIDAY)
        )
        assert run is not None
        assert run.status != "SKIPPED"


def test_holiday_still_skips_without_the_explicit_override(
    sqlite_factory: sessionmaker[Session], app_config: AppConfig
) -> None:
    """The override must be opt-in, so a scheduled run never invents a session."""

    pipeline = MorningPipeline(sqlite_factory, app_config, _environment())

    result = pipeline.run(HOLIDAY, perform_ingestion=False)

    assert result.status == "SKIPPED"
    assert result.prediction_set_id is None
    assert NON_TRADING_DAY_WARNING not in result.warnings


def test_only_the_scored_row_keeps_lineage_rows(
    sqlite_factory: sessionmaker[Session], app_config: AppConfig, make_bar
) -> None:
    """Training lineage must live in the hash, not in 340,000 rows."""

    feature_names = tuple(f"factor_{index}" for index in range(3))
    sessions = japan_sessions_before(
        PREDICTION_DATE, app_config.model.training.window_jpx_sessions
    )
    raw_at = datetime(2026, 1, 5, 20, 0, tzinfo=UTC)
    with sqlite_factory() as session:
        market = MarketDataRepository(session)
        market.upsert_bars(
            [
                make_bar(
                    market_date=raw_at.date(),
                    timestamp=raw_at,
                    available_timestamp=raw_at,
                    first_observed_at=raw_at,
                    retrieved_at=raw_at,
                )
            ]
        )
        raw = session.scalar(select(MarketData))
        assert raw is not None
        reference = SourceReference(
            table_name="market_data",
            row_id=raw.id,
            canonical_symbol=raw.canonical_symbol,
            market_date=raw.market_date,
            available_at=raw.available_timestamp,
            first_observed_at=raw.first_observed_at,
            retrieved_at=raw.retrieved_at,
            raw_hash=raw.raw_hash,
            data_quality=raw.data_quality,
        )
        training_samples = tuple(
            ModelSample(
                ticker="9101",
                sample_date=session_date,
                cutoff_at=prediction_cutoff(session_date),
                values={name: 1.0 for name in feature_names},
                lineage={name: (reference,) for name in feature_names},
                target_return=0.001,
                target_lineage=(reference,),
            )
            for session_date in sessions
        )
        current = ModelSample(
            ticker="9101",
            sample_date=PREDICTION_DATE,
            cutoff_at=prediction_cutoff(PREDICTION_DATE),
            values={name: 1.0 for name in feature_names},
            lineage={name: (reference,) for name in feature_names},
        )
        dataset = ModelDataset(
            ticker="9101",
            feature_names=feature_names,
            training_frame=pd.DataFrame(
                [sample.values for sample in training_samples], index=sessions
            ),
            training_target=pd.Series(
                [sample.target_return for sample in training_samples], index=sessions
            ),
            current_frame=pd.DataFrame([current.values], index=[PREDICTION_DATE]),
            training_samples=training_samples,
            current_sample=current,
            candidate_feature_count=len(feature_names),
            feature_coverage=1.0,
        )
        run = market.create_run(
            run_type="MORNING",
            prediction_date=PREDICTION_DATE,
            cutoff_at=prediction_cutoff(PREDICTION_DATE),
            data_version="config-test",
        )

        feature_set = persist_feature_set(
            PredictionPipelineRepository(session),
            run_id=run.run_id,
            prediction_date=PREDICTION_DATE,
            config=app_config,
            dataset=dataset,
            terminal_status="READY",
        )

        # One lineage row per scored feature, and nothing for the 120 training
        # sessions that used to dominate the table.
        assert session.scalar(select(func.count()).select_from(FeatureInput)) == len(
            feature_names
        )
        scored_ids = set(
            session.scalars(
                select(FeatureValue.feature_value_id).where(
                    FeatureValue.row_role == "SCORE"
                )
            )
        )
        written_for = set(session.scalars(select(FeatureInput.feature_value_id)))
        assert written_for == scored_ids
        # The hash still covers every training reference, so a substituted
        # training input remains detectable.
        assert feature_set.input_manifest_hash is not None
        assert feature_set.status == "READY"


def test_backfill_relaxes_only_the_observation_guard_and_says_so(
    sqlite_factory: sessionmaker[Session], app_config: AppConfig
) -> None:
    """A replay must be labelled, and must keep the look-ahead guard intact."""

    from services.dataset import SourceReference

    cutoff = prediction_cutoff(PREDICTION_DATE)
    after_cutoff = cutoff + timedelta(hours=6)
    reference = SourceReference(
        table_name="market_data",
        row_id=1,
        canonical_symbol="sp500",
        market_date=PREDICTION_DATE - timedelta(days=1),
        # Available in the market before the cutoff, but fetched afterwards --
        # exactly the shape every backfilled row has.
        available_at=cutoff - timedelta(hours=1),
        first_observed_at=after_cutoff,
        retrieved_at=after_cutoff,
        raw_hash="a" * 64,
        data_quality="FREE_UNVERIFIED",
    )

    with pytest.raises(ValueError, match="observed after cutoff"):
        reference.assert_visible(cutoff, operational=True)
    reference.assert_visible(cutoff, operational=False)

    # A value that only became available after the cutoff stays rejected in
    # both modes: that guard is look-ahead and is never relaxed.
    future = SourceReference(
        table_name="market_data",
        row_id=2,
        canonical_symbol="sp500",
        market_date=PREDICTION_DATE,
        available_at=after_cutoff,
        first_observed_at=after_cutoff,
        retrieved_at=after_cutoff,
        raw_hash="b" * 64,
        data_quality="FREE_UNVERIFIED",
    )
    for operational in (True, False):
        with pytest.raises(ValueError, match="available after cutoff"):
            future.assert_visible(cutoff, operational=operational)

    result = MorningPipeline(sqlite_factory, app_config, _environment()).run(
        PREDICTION_DATE, perform_ingestion=False, backfill=True
    )
    assert BACKFILL_WARNING in result.warnings


def test_backfill_persists_lineage_the_operational_path_would_reject(
    sqlite_factory: sessionmaker[Session], app_config: AppConfig, make_bar
) -> None:
    """The liveness guard exists in two places; a replay must clear both.

    Relaxing it only in the dataset builder let five replayed sessions run for
    seven minutes and then die in persistence, which has its own copy of the
    same check.
    """

    cutoff = prediction_cutoff(PREDICTION_DATE)
    # SQLite stores these naive, so keep the two sides a whole day apart: the
    # point of the test is which guard fires, not timezone arithmetic.
    available = cutoff - timedelta(days=2)
    fetched_later = cutoff + timedelta(days=2)
    with sqlite_factory() as session:
        market = MarketDataRepository(session)
        market.upsert_bars(
            [
                make_bar(
                    market_date=available.date(),
                    timestamp=available,
                    available_timestamp=available,
                    # Fetched after the cutoff, exactly like every backfilled row.
                    first_observed_at=fetched_later,
                    retrieved_at=fetched_later,
                )
            ]
        )
        raw = session.scalar(select(MarketData))
        assert raw is not None
        run = market.create_run(
            run_type="MORNING",
            prediction_date=PREDICTION_DATE,
            cutoff_at=cutoff,
            data_version="config-test",
        )
        repository = PredictionPipelineRepository(session)
        sessions = japan_sessions_before(
            PREDICTION_DATE, app_config.model.training.window_jpx_sessions
        )
        feature_set = repository.create_feature_set(
            run_id=run.run_id,
            ticker="9101",
            prediction_date=PREDICTION_DATE,
            cutoff_at=cutoff,
            feature_version="v1",
            set_kind="MORNING",
            training_start=sessions[0],
            training_end=sessions[-1],
            config_hash=DIGEST,
            required_feature_count=1,
            idempotency_key=f"feature/backfill/{run.run_id}",
        )
        value = repository.add_feature_value(
            feature_set_id=feature_set.feature_set_id,
            sample_date=PREDICTION_DATE,
            sample_cutoff_at=cutoff,
            row_role="SCORE",
            value_kind="FEATURE",
            feature_name="factor",
            value=Decimal("1"),
            is_missing=False,
            data_quality="FREE_UNVERIFIED",
        )

        with pytest.raises(ValueError, match="not observed by the prediction cutoff"):
            repository.add_feature_input(
                feature_value_id=value.feature_value_id,
                input_role="source_001",
                source_type="MARKET_DATA",
                source_row_id=raw.id,
            )

        written = repository.add_feature_input(
            feature_value_id=value.feature_value_id,
            input_role="source_001",
            source_type="MARKET_DATA",
            source_row_id=raw.id,
            observed_by_cutoff=False,
        )
        assert written.market_data_id == raw.id

        # The look-ahead guard stays absolute in both modes: a row that only
        # became available after the sample cutoff is refused either way.
        late = repository.add_feature_value(
            feature_set_id=feature_set.feature_set_id,
            sample_date=PREDICTION_DATE,
            sample_cutoff_at=available - timedelta(days=1),
            row_role="SCORE",
            value_kind="FEATURE",
            feature_name="late_factor",
            value=Decimal("1"),
            is_missing=False,
            data_quality="FREE_UNVERIFIED",
        )
        with pytest.raises(ValueError, match="unavailable at the sample cutoff"):
            repository.add_feature_input(
                feature_value_id=late.feature_value_id,
                input_role="source_001",
                source_type="MARKET_DATA",
                source_row_id=raw.id,
                observed_by_cutoff=False,
            )


def test_the_zero_cost_configuration_charges_nothing(
    sqlite_factory: sessionmaker[Session], app_config: AppConfig
) -> None:
    """What production does now: gross equals net, because nothing is charged.

    The companion to the costed test above. Both have to hold -- one describes
    the configuration in force, the other the arithmetic that comes back the day
    real orders are placed.
    """

    app_config = _with_costs(app_config, commission=0.0, slippage=0.0)
    _seed_prediction_set(sqlite_factory, app_config, with_buy=True)
    at_1545 = datetime(2026, 8, 12, 6, 45, tzinfo=UTC)
    at_1555 = datetime(2026, 8, 12, 6, 55, tzinfo=UTC)
    with sqlite_factory() as session:
        session.add(
            _stock_revision(close="6060", raw_hash="1" * 64, first_observed_at=at_1545)
        )
        session.commit()

    pipeline = ClosePipeline(sqlite_factory, app_config, _environment())
    pipeline.run(PREDICTION_DATE, observed_at=at_1545, fetch_data=False)
    with sqlite_factory() as session:
        stock = session.scalar(
            select(StockPrice).where(StockPrice.raw_hash == "1" * 64)
        )
        assert stock is not None
        stock.last_seen_at = at_1555
        session.commit()
    pipeline.run(PREDICTION_DATE, observed_at=at_1555, fetch_data=False)

    with sqlite_factory() as session:
        trade = session.scalar(select(SimulatedTrade))
        assert trade is not None
        assert float(trade.commission_cost_jpy or 0) == pytest.approx(0.0)
        assert float(trade.slippage_cost_jpy or 0) == pytest.approx(0.0)
        assert float(trade.net_profit_jpy or 0) == pytest.approx(
            float(trade.gross_profit_jpy or 0)
        )


def test_every_workflow_runs_the_interpreter_the_repository_declares() -> None:
    """All fourteen, not just the scheduled ones.

    Before 2026-08-29 this repository ran four interpreters at once: 3.11 in the
    local venv, 3.12 in the dashboard venv and in CI, and 3.14 on Streamlit
    Community Cloud -- so the deployed dashboard was the one build the suite
    never touched, and a test failed locally that passed in CI purely on syntax
    the older interpreter could not parse.
    """

    import re

    pinned = _pinned_python()
    root = Path(__file__).resolve().parents[1]
    workflows = sorted((root / ".github" / "workflows").glob("*.yml"))
    assert workflows

    for path in workflows:
        text = path.read_text(encoding="utf-8")
        for version in re.findall(r'python-version:\s*"([0-9.]+)"', text):
            assert version == pinned, f"{path.name} は Python {version}"


def test_the_declared_interpreter_matches_the_packaging_metadata() -> None:
    import tomllib

    root = Path(__file__).resolve().parents[1]
    pinned = _pinned_python()
    config = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))

    assert config["project"]["requires-python"] == f">={pinned}"
    assert config["tool"]["mypy"]["python_version"] == pinned
