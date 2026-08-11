"""Recovery of audit rows left in-progress by an external timeout."""

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from data.availability import prediction_cutoff
from database.models import Base, FeatureValue, PredictionSet
from database.repository import MarketDataRepository, PredictionPipelineRepository
from services.recovery import reconcile_stale_runs
from services.retention import prune_feature_history


def test_reconcile_stale_runs_fails_old_rows_and_preserves_active_work() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    factory = sessionmaker(engine, expire_on_commit=False, autoflush=False)
    now = datetime(2026, 8, 10, 13, 0, tzinfo=UTC)
    old = now - timedelta(hours=3)
    fresh = now - timedelta(minutes=20)
    prediction_date = date(2026, 8, 10)
    cutoff = datetime(2026, 8, 9, 23, 30, tzinfo=UTC)

    with factory() as session:
        market = MarketDataRepository(session)
        prediction = PredictionPipelineRepository(session)
        old_run = market.create_run(
            run_type="MORNING",
            prediction_date=prediction_date,
            cutoff_at=cutoff,
            data_version="test",
        )
        old_run.started_at = old
        batch = market.create_ingestion_batch(
            run_id=old_run.run_id,
            provider="test",
            requested_symbols=1,
        )
        batch.started_at = old
        step = prediction.start_run_step(
            run_id=old_run.run_id,
            step_name="BUILD_TRAIN_PREDICT",
            attempt_number=1,
            started_at=old,
        )
        building_features = prediction.create_feature_set(
            run_id=old_run.run_id,
            ticker="9101",
            prediction_date=prediction_date,
            cutoff_at=cutoff,
            feature_version="features-v1",
            set_kind="MORNING",
            training_start=date(2026, 2, 18),
            training_end=date(2026, 8, 7),
            config_hash="a" * 64,
            required_feature_count=0,
            idempotency_key="stale/building/features",
        )
        building_features.created_at = old
        prediction_set = prediction.create_prediction_set(
            run_id=old_run.run_id,
            prediction_date=prediction_date,
            cutoff_at=cutoff,
            feature_version="features-v1",
            model_version="model-v1",
            strategy_version="strategy-v1",
            training_start=date(2026, 2, 18),
            training_end=date(2026, 8, 7),
            idempotency_key="stale/building/predictions",
        )
        prediction_set.generated_at = old

        model_run_parent = market.create_run(
            run_type="MORNING",
            prediction_date=prediction_date,
            cutoff_at=cutoff,
            data_version="test-model",
        )
        model_run_parent.started_at = old
        ready_features = prediction.create_feature_set(
            run_id=model_run_parent.run_id,
            ticker="9102",
            prediction_date=prediction_date,
            cutoff_at=cutoff,
            feature_version="features-v1",
            set_kind="MORNING",
            training_start=date(2026, 2, 18),
            training_end=date(2026, 8, 7),
            config_hash="b" * 64,
            required_feature_count=0,
            idempotency_key="stale/ready/features",
        )
        prediction.finalize_feature_set(
            ready_features,
            status="READY",
            input_manifest_hash="c" * 64,
        )
        model = prediction.create_model_run(
            run_id=model_run_parent.run_id,
            ticker="9102",
            feature_set_id=ready_features.feature_set_id,
            task="REGRESSION",
            algorithm="ridge",
            training_start=ready_features.training_start,
            training_end=ready_features.training_end,
            cutoff_at=cutoff,
            training_rows=120,
            feature_version="features-v1",
            model_version="model-v1",
            random_seed=42,
            parameters={},
            cv_results={},
            idempotency_key="stale/model",
            started_at=old,
        )

        active_run = market.create_run(
            run_type="MORNING",
            prediction_date=prediction_date,
            cutoff_at=cutoff,
            data_version="active",
        )
        active_run.started_at = fresh
        active_step = prediction.start_run_step(
            run_id=active_run.run_id,
            step_name="BUILD_TRAIN_PREDICT",
            attempt_number=1,
            started_at=fresh,
        )
        session.commit()

    report = reconcile_stale_runs(factory, now=now, stale_after=timedelta(hours=2))

    assert report.daily_runs == 2
    assert report.ingestion_batches == 1
    assert report.run_steps == 1
    assert report.feature_sets == 1
    assert report.model_runs == 1
    assert report.prediction_sets == 1
    with factory() as session:
        assert session.get(type(old_run), old_run.run_id).status == "FAILED"
        assert session.get(type(batch), batch.batch_id).status == "FAILED"
        assert session.get(type(step), step.step_id).status == "FAILED"
        assert (
            session.get(
                type(building_features), building_features.feature_set_id
            ).status
            == "FAILED"
        )
        assert session.get(type(model), model.model_run_id).status == "FAILED"
        assert (
            session.get(type(prediction_set), prediction_set.prediction_set_id).status
            == "FAILED"
        )
        assert session.get(type(active_run), active_run.run_id).status == "RUNNING"
        assert session.get(type(active_step), active_step.step_id).status == "RUNNING"


def _prune_factory() -> sessionmaker[Session]:
    engine = create_engine("sqlite+pysqlite:///:memory:", poolclass=StaticPool)
    Base.metadata.create_all(engine)
    return sessionmaker(engine, expire_on_commit=False)


def _seed_day(factory: sessionmaker[Session], day: date, index: int) -> None:
    with factory() as session:
        market = MarketDataRepository(session)
        repository = PredictionPipelineRepository(session)
        run = market.create_run(
            run_type="MORNING",
            prediction_date=day,
            cutoff_at=prediction_cutoff(day),
            data_version="prune-test",
        )
        feature_set = repository.create_feature_set(
            run_id=run.run_id,
            ticker="9101",
            prediction_date=day,
            cutoff_at=prediction_cutoff(day),
            feature_version="v1",
            set_kind="MORNING",
            training_start=day - timedelta(days=10),
            training_end=day - timedelta(days=1),
            config_hash="c" * 64,
            required_feature_count=1,
            idempotency_key=f"feature/prune/{index}",
        )
        sample_day = day - timedelta(days=1)
        repository.add_feature_value(
            feature_set_id=feature_set.feature_set_id,
            sample_date=sample_day,
            sample_cutoff_at=prediction_cutoff(sample_day),
            row_role="TRAIN",
            value_kind="FEATURE",
            feature_name="factor",
            value=Decimal("1"),
            is_missing=False,
            data_quality="FREE_UNVERIFIED",
        )
        repository.create_prediction_set(
            run_id=run.run_id,
            prediction_date=day,
            cutoff_at=prediction_cutoff(day),
            feature_version="v1",
            model_version="m1",
            strategy_version="s1",
            training_start=day - timedelta(days=10),
            training_end=day - timedelta(days=1),
            idempotency_key=f"set/prune/{index}",
        )
        session.commit()


def test_prune_keeps_the_newest_dates_and_drops_the_rest() -> None:
    """Feature history is bounded; the track record is not touched."""

    factory = _prune_factory()
    for index, day in enumerate(
        [date(2026, 8, 6), date(2026, 8, 7), date(2026, 8, 10)]
    ):
        _seed_day(factory, day, index)

    report = prune_feature_history(factory, keep_dates=2)

    assert report.pruned_dates == (date(2026, 8, 6),)
    assert report.kept_dates == (date(2026, 8, 10), date(2026, 8, 7))
    assert report.feature_values == 1
    with factory() as session:
        remaining = sorted(session.scalars(select(FeatureValue.sample_date)))
        assert remaining == [date(2026, 8, 6), date(2026, 8, 9)]
        assert session.scalar(select(func.count()).select_from(PredictionSet)) == 3


def test_prune_does_nothing_when_history_is_short() -> None:
    """A young database must not lose the only day it has."""

    factory = _prune_factory()
    _seed_day(factory, date(2026, 8, 10), 0)

    report = prune_feature_history(factory, keep_dates=2)

    assert report.pruned is False
    assert report.feature_values == 0
