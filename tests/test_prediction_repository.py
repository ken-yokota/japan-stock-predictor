"""Prediction-pipeline persistence, point-in-time, and retry tests."""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal

import pytest
from sqlalchemy import create_engine, func, inspect, select
from sqlalchemy.orm import Session

from database.models import (
    Base,
    EmailLog,
    FeatureInput,
    MetricSnapshot,
    ModelRun,
    Prediction,
    SimulatedTrade,
)
from database.repository import MarketDataRepository, PredictionPipelineRepository

PREDICTION_DATE = date(2026, 8, 11)
CUTOFF = datetime(2026, 8, 10, 23, 30, tzinfo=UTC)
TRAINING_START = date(2026, 2, 18)
TRAINING_END = date(2026, 8, 7)
DIGEST = "a" * 64


def test_prediction_schema_contains_all_required_pipeline_tables() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)

    names = set(inspect(engine).get_table_names())

    assert {
        "run_steps",
        "feature_sets",
        "feature_values",
        "feature_inputs",
        "model_runs",
        "model_coefficients",
        "prediction_sets",
        "predictions",
        "actual_results",
        "simulated_trades",
        "metric_snapshots",
        "email_logs",
    } <= names


def test_run_step_retry_identity_and_status_transition_are_idempotent() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        market_repository = MarketDataRepository(session)
        repository = PredictionPipelineRepository(session)
        run = market_repository.create_run(
            run_type="MORNING_PREDICTION",
            prediction_date=PREDICTION_DATE,
            cutoff_at=CUTOFF,
            data_version="config-v1",
        )

        first = repository.start_run_step(
            run_id=run.run_id,
            step_name="BUILD_FEATURES",
            attempt_number=1,
        )
        same = repository.start_run_step(
            run_id=run.run_id,
            step_name="BUILD_FEATURES",
            attempt_number=1,
        )
        repository.finish_run_step(first, status="SUCCESS")
        same_terminal = repository.finish_run_step(same, status="SUCCESS")

        assert same.step_id == first.step_id
        assert same_terminal.status == "SUCCESS"
        with pytest.raises(ValueError, match="already terminal"):
            repository.finish_run_step(first, status="FAILED", error_message="late")


def test_feature_lineage_rejects_rows_first_observed_after_cutoff(make_bar) -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    observed_late = datetime(2026, 8, 11, 0, 0, tzinfo=UTC)
    with Session(engine) as session:
        market_repository = MarketDataRepository(session)
        repository = PredictionPipelineRepository(session)
        run = market_repository.create_run(
            run_type="MORNING_PREDICTION",
            prediction_date=PREDICTION_DATE,
            cutoff_at=CUTOFF,
            data_version="config-v1",
        )
        market_repository.upsert_bars(
            [
                make_bar(
                    first_observed_at=observed_late,
                    retrieved_at=observed_late,
                )
            ]
        )
        raw_row_id = session.scalar(select(func.min(FeatureInput.market_data_id)))
        assert raw_row_id is None
        market_data_id = session.scalar(
            select(Base.metadata.tables["market_data"].c.id)
        )
        assert isinstance(market_data_id, int)
        feature_set = repository.create_feature_set(
            run_id=run.run_id,
            ticker="9101",
            prediction_date=PREDICTION_DATE,
            cutoff_at=CUTOFF,
            feature_version="features-v1",
            set_kind="MORNING",
            training_start=TRAINING_START,
            training_end=TRAINING_END,
            config_hash=DIGEST,
            required_feature_count=1,
            idempotency_key="feature-set:late-input",
        )
        value = repository.add_feature_value(
            feature_set_id=feature_set.feature_set_id,
            sample_date=PREDICTION_DATE,
            sample_cutoff_at=CUTOFF,
            row_role="SCORE",
            value_kind="FEATURE",
            feature_name="sp500_return_1d",
            value=Decimal("0.01"),
            is_missing=False,
        )

        with pytest.raises(ValueError, match="not observed by the prediction cutoff"):
            repository.add_feature_input(
                feature_value_id=value.feature_value_id,
                input_role="sp500_close",
                source_type="MARKET_DATA",
                source_row_id=market_data_id,
            )


def test_zero_feature_set_can_publish_insufficient_prediction_and_email() -> None:
    """No usable features is an auditable result, not a crashed morning run."""

    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        market_repository = MarketDataRepository(session)
        repository = PredictionPipelineRepository(session)
        run = market_repository.create_run(
            run_type="MORNING_PREDICTION",
            prediction_date=PREDICTION_DATE,
            cutoff_at=CUTOFF,
            data_version="config-v1",
        )
        feature_set = repository.create_feature_set(
            run_id=run.run_id,
            ticker="9101",
            prediction_date=PREDICTION_DATE,
            cutoff_at=CUTOFF,
            feature_version="features-v1",
            set_kind="MORNING",
            training_start=TRAINING_START,
            training_end=TRAINING_END,
            config_hash=DIGEST,
            required_feature_count=0,
            idempotency_key="feature-set:9101:empty",
        )
        repository.finalize_feature_set(
            feature_set,
            status="INSUFFICIENT_DATA",
            input_manifest_hash=None,
        )
        prediction_set = repository.create_prediction_set(
            run_id=run.run_id,
            prediction_date=PREDICTION_DATE,
            cutoff_at=CUTOFF,
            feature_version="features-v1",
            model_version="models-v1",
            strategy_version="strategy-v1",
            training_start=TRAINING_START,
            training_end=TRAINING_END,
            idempotency_key="predictions:empty:2026-08-11",
        )
        prediction = repository.add_prediction(
            prediction_set_id=prediction_set.prediction_set_id,
            ticker="9101",
            feature_set_id=feature_set.feature_set_id,
            regression_model_run_id=None,
            classification_model_run_id=None,
            status="INSUFFICIENT_DATA",
            predicted_intraday_return=None,
            probability_up=None,
            reference_stock_price_id=None,
            reference_price=None,
            reference_basis="UNAVAILABLE",
            predicted_price_difference=None,
            predicted_close=None,
            signal="NONE",
            rank=None,
            return_threshold=Decimal("0.003"),
            probability_threshold=Decimal("0.60"),
            confidence_score=None,
            feature_coverage=0.0,
            idempotency_key="prediction:9101:empty:2026-08-11",
            warnings=["no usable point-in-time features"],
        )
        repository.finalize_prediction_set(
            prediction_set,
            status="INSUFFICIENT_DATA",
            expected_tickers={"9101"},
        )
        email = repository.create_email_log(
            prediction_set_id=prediction_set.prediction_set_id,
            recipient="owner@example.com",
            template_version="morning-v1",
            subject="Morning predictions unavailable",
            idempotency_key="email:empty:2026-08-11:owner",
        )

        assert feature_set.missing_ratio == 0.0
        assert prediction.status == "INSUFFICIENT_DATA"
        assert email.status == "PENDING"


def test_pipeline_artifacts_are_pit_linked_and_idempotent(make_bar) -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        market_repository = MarketDataRepository(session)
        repository = PredictionPipelineRepository(session)
        run = market_repository.create_run(
            run_type="MORNING_PREDICTION",
            prediction_date=PREDICTION_DATE,
            cutoff_at=CUTOFF,
            data_version="config-v1",
        )
        market_repository.upsert_bars(
            [make_bar(), make_bar(canonical_symbol="9101", raw_hash="b" * 64)],
            stock_symbols={"9101"},
        )
        market_data_id = session.scalar(
            select(Base.metadata.tables["market_data"].c.id)
        )
        reference_stock_price_id = session.scalar(
            select(Base.metadata.tables["stock_prices"].c.id)
        )
        assert isinstance(market_data_id, int)
        assert isinstance(reference_stock_price_id, int)

        feature_set = repository.create_feature_set(
            run_id=run.run_id,
            ticker="9101",
            prediction_date=PREDICTION_DATE,
            cutoff_at=CUTOFF,
            feature_version="features-v1",
            set_kind="MORNING",
            training_start=TRAINING_START,
            training_end=TRAINING_END,
            config_hash=DIGEST,
            required_feature_count=2,
            idempotency_key="feature-set:9101:2026-08-11",
        )
        same_feature_set = repository.create_feature_set(
            run_id=run.run_id,
            ticker="9101",
            prediction_date=PREDICTION_DATE,
            cutoff_at=CUTOFF,
            feature_version="features-v1",
            set_kind="MORNING",
            training_start=TRAINING_START,
            training_end=TRAINING_END,
            config_hash=DIGEST,
            required_feature_count=2,
            idempotency_key="feature-set:9101:2026-08-11",
        )
        assert same_feature_set.feature_set_id == feature_set.feature_set_id

        training_value = repository.add_feature_value(
            feature_set_id=feature_set.feature_set_id,
            sample_date=TRAINING_END,
            sample_cutoff_at=datetime(2026, 8, 8, 0, 0, tzinfo=UTC),
            row_role="TRAIN",
            value_kind="FEATURE",
            feature_name="sp500_return_1d",
            value=Decimal("0.01"),
            is_missing=False,
        )
        score_value = repository.add_feature_value(
            feature_set_id=feature_set.feature_set_id,
            sample_date=PREDICTION_DATE,
            sample_cutoff_at=CUTOFF,
            row_role="SCORE",
            value_kind="FEATURE",
            feature_name="sp500_return_1d",
            value=Decimal("0.02"),
            is_missing=False,
        )
        repository.add_feature_input(
            feature_value_id=training_value.feature_value_id,
            input_role="sp500_close",
            source_type="MARKET_DATA",
            source_row_id=market_data_id,
        )
        first_input = repository.add_feature_input(
            feature_value_id=score_value.feature_value_id,
            input_role="sp500_close",
            source_type="MARKET_DATA",
            source_row_id=market_data_id,
        )
        same_input = repository.add_feature_input(
            feature_value_id=score_value.feature_value_id,
            input_role="sp500_close",
            source_type="MARKET_DATA",
            source_row_id=market_data_id,
        )
        assert same_input.feature_input_id == first_input.feature_input_id
        repository.finalize_feature_set(
            feature_set,
            status="READY",
            input_manifest_hash="c" * 64,
        )

        model_runs: dict[str, ModelRun] = {}
        for task, algorithm in (
            ("REGRESSION", "ridge"),
            ("CLASSIFICATION", "logistic_regression"),
        ):
            model_run = repository.create_model_run(
                run_id=run.run_id,
                ticker="9101",
                feature_set_id=feature_set.feature_set_id,
                task=task,
                algorithm=algorithm,
                training_start=TRAINING_START,
                training_end=TRAINING_END,
                cutoff_at=CUTOFF,
                training_rows=120,
                feature_version="features-v1",
                model_version="models-v1",
                random_seed=42,
                parameters={"alpha": 1.0},
                cv_results={"strategy": "time_series_split"},
                idempotency_key=f"model:9101:{task}",
            )
            repository.add_model_coefficient(
                model_run_id=model_run.model_run_id,
                feature_name="sp500_return_1d",
                coefficient=Decimal("0.1"),
                scaler_mean=Decimal("0.0"),
                scaler_scale=Decimal("1.0"),
            )
            repository.finish_model_run(
                model_run,
                status="SUCCESS",
                intercept=Decimal("0.0"),
            )
            model_runs[task] = model_run

        prediction_set = repository.create_prediction_set(
            run_id=run.run_id,
            prediction_date=PREDICTION_DATE,
            cutoff_at=CUTOFF,
            feature_version="features-v1",
            model_version="models-v1",
            strategy_version="strategy-v1",
            training_start=TRAINING_START,
            training_end=TRAINING_END,
            idempotency_key="predictions:2026-08-11",
        )
        prediction = repository.add_prediction(
            prediction_set_id=prediction_set.prediction_set_id,
            ticker="9101",
            feature_set_id=feature_set.feature_set_id,
            regression_model_run_id=model_runs["REGRESSION"].model_run_id,
            classification_model_run_id=model_runs["CLASSIFICATION"].model_run_id,
            status="SUCCESS",
            predicted_intraday_return=Decimal("0.004"),
            probability_up=Decimal("0.65"),
            reference_stock_price_id=reference_stock_price_id,
            reference_price=Decimal("102"),
            reference_basis="PREVIOUS_CLOSE",
            predicted_price_difference=Decimal("0.408"),
            predicted_close=Decimal("102.408"),
            signal="BUY",
            rank=1,
            return_threshold=Decimal("0.003"),
            probability_threshold=Decimal("0.60"),
            confidence_score=Decimal("75"),
            prediction_interval_low=Decimal("-0.002"),
            prediction_interval_high=Decimal("0.010"),
            positive_factors=["sp500_return_1d"],
            negative_factors=[],
            feature_coverage=1.0,
            idempotency_key="prediction:9101:2026-08-11",
        )
        repository.finalize_prediction_set(
            prediction_set,
            status="READY",
            expected_tickers={"9101"},
        )

        email = repository.create_email_log(
            prediction_set_id=prediction_set.prediction_set_id,
            recipient="owner@example.com",
            template_version="morning-v1",
            subject="Morning predictions",
            idempotency_key="email:2026-08-11:owner",
        )
        assert repository.claim_email(email.idempotency_key)
        assert not repository.claim_email(email.idempotency_key)
        repository.mark_email_sent(
            email.idempotency_key,
            provider_message_id="resend-message-1",
            sent_at=datetime.now(UTC),
        )

        actual_row_bar = make_bar(
            canonical_symbol="9101",
            market_date=PREDICTION_DATE,
            timestamp=datetime(2026, 8, 11, 6, 30, tzinfo=UTC),
            available_timestamp=datetime(2026, 8, 11, 6, 45, tzinfo=UTC),
            first_observed_at=datetime(2026, 8, 11, 7, 0, tzinfo=UTC),
            retrieved_at=datetime(2026, 8, 11, 7, 0, tzinfo=UTC),
            raw_hash="d" * 64,
        )
        market_repository.upsert_bars([actual_row_bar], stock_symbols={"9101"})
        actual_stock_price_id = session.scalar(
            select(Base.metadata.tables["stock_prices"].c.id).where(
                Base.metadata.tables["stock_prices"].c.raw_hash == "d" * 64
            )
        )
        assert isinstance(actual_stock_price_id, int)
        actual = repository.save_actual_result(
            prediction_id=prediction.prediction_id,
            stock_price_id=actual_stock_price_id,
            supersedes_actual_result_id=None,
            result_version=1,
            status="FINAL",
            actual_open=Decimal("100"),
            actual_close=Decimal("102"),
            observed_at=datetime(2026, 8, 11, 7, 0, tzinfo=UTC),
            finalized_at=datetime(2026, 8, 11, 7, 0, tzinfo=UTC),
            idempotency_key="actual:9101:2026-08-11:v1",
        )
        repository.save_simulated_trade(
            prediction_id=prediction.prediction_id,
            actual_result_id=actual.actual_result_id,
            status="FINAL",
            capital_jpy=Decimal("1000000"),
            shares=10000,
            entry_price=Decimal("100"),
            exit_price=Decimal("102"),
            gross_profit_jpy=Decimal("20000"),
            commission_cost_jpy=Decimal("1000"),
            slippage_cost_jpy=Decimal("1000"),
            net_profit_jpy=Decimal("18000"),
            realized_return=Decimal("0.018"),
            opened_at=datetime(2026, 8, 11, 0, 0, tzinfo=UTC),
            closed_at=datetime(2026, 8, 11, 6, 30, tzinfo=UTC),
            strategy_version="strategy-v1",
            idempotency_key="trade:9101:2026-08-11:v1",
        )
        repository.save_metric_snapshot(
            ticker="9101",
            as_of_date=PREDICTION_DATE,
            model_version="models-v1",
            strategy_version="strategy-v1",
            evaluation_window="ALL_OOS",
            status="READY",
            sample_status="LOW_SAMPLE",
            prediction_count=1,
            trade_count=1,
            win_count=1,
            loss_count=0,
            metrics={
                "win_rate": Decimal("1"),
                "profit_factor": None,
                "expectancy_jpy": Decimal("18000"),
            },
            input_manifest_hash="e" * 64,
            idempotency_key="metrics:9101:2026-08-11",
        )
        session.commit()

        assert feature_set.max_retrieved_at is not None
        assert prediction.prediction_interval_low == Decimal("-0.002")
        assert prediction.positive_factors == ["sp500_return_1d"]
        assert prediction.feature_coverage == pytest.approx(1.0)
        assert session.scalar(select(func.count()).select_from(Prediction)) == 1
        assert session.scalar(select(func.count()).select_from(SimulatedTrade)) == 1
        assert session.scalar(select(func.count()).select_from(MetricSnapshot)) == 1
        sent_email = session.scalar(select(EmailLog))
        assert sent_email is not None
        assert sent_email.status == "SENT"
