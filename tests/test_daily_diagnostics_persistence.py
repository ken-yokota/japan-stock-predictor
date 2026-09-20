"""Exercise actual fit + SQL persistence, including retries and future fits."""

from datetime import date, timedelta
from decimal import Decimal
from types import SimpleNamespace

import numpy as np
import pandas as pd
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from data.availability import prediction_cutoff
from database.models import Base, ModelCoefficient
from database.repository import MarketDataRepository, PredictionPipelineRepository
from models import ModelTrainingConfig, train_ticker_model
from services.persistence import _persist_model


def test_daily_coefficients_saved_with_version_safe_past_stability():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    rng = np.random.default_rng(7)
    x = pd.DataFrame(
        rng.normal(size=(120, 2)), columns=["stock__return_1d", "usdjpy__return_1d"]
    )
    y = x.iloc[:, 0] * 0.01 + rng.normal(0, 0.01, 120)
    fitted = train_ticker_model(
        "7203",
        x,
        y,
        feature_names=tuple(x.columns),
        config=ModelTrainingConfig(
            minimum_training_sessions=120,
            time_series_splits=2,
            ridge_alphas=(10.0,),
            logistic_cs=(1.0,),
            distribution_quantiles=(),
        ),
    )
    with Session(engine) as session:
        repo = PredictionPipelineRepository(session)
        market = MarketDataRepository(session)

        def save(day, config_hash="a" * 64):
            cutoff = prediction_cutoff(day)
            run = market.create_run(
                run_type="MORNING",
                prediction_date=day,
                cutoff_at=cutoff,
                data_version=config_hash,
            )
            feature = repo.create_feature_set(
                run_id=run.run_id,
                ticker="7203",
                prediction_date=day,
                cutoff_at=cutoff,
                feature_version="pit-features-v1",
                set_kind="MORNING",
                training_start=date(2026, 1, 1),
                training_end=day - timedelta(days=1),
                config_hash=config_hash,
                required_feature_count=2,
                idempotency_key="f/" + run.run_id,
            )
            for name in x.columns:
                repo.add_feature_value(
                    feature_set_id=feature.feature_set_id,
                    sample_date=day,
                    sample_cutoff_at=cutoff,
                    row_role="SCORE",
                    value_kind="FEATURE",
                    feature_name=name,
                    value=Decimal(str(x.iloc[-1][name])),
                    is_missing=False,
                    data_quality="FREE_UNVERIFIED",
                )
            repo.finalize_feature_set(
                feature, status="READY", input_manifest_hash="b" * 64
            )
            computation = SimpleNamespace(
                model=fitted,
                dataset=SimpleNamespace(current_frame=x.iloc[[-1]]),
                result=SimpleNamespace(ticker="7203", training_sessions=120),
            )
            stored = _persist_model(
                repo,
                run_id=run.run_id,
                feature_set=feature,
                computation=computation,
                task="REGRESSION",
            )
            session.flush()
            return stored

        save(date(2026, 9, 15))
        save(date(2026, 9, 15))  # retry is one trading session in stability
        save(date(2026, 9, 16), config_hash="c" * 64)  # different version
        save(date(2026, 9, 19))  # unavailable future fit
        current = save(date(2026, 9, 18))
        assert current.diagnostics["history_fits"] == 1
        assert len(current.diagnostics["features"]) == 2
        assert all(r["rolling_fits"] == 2 for r in current.diagnostics["features"])
        stored_coefficients = list(
            session.scalars(
                select(ModelCoefficient).where(
                    ModelCoefficient.model_run_id == current.model_run_id
                )
            )
        )
        assert len(stored_coefficients) == 2
        assert current.diagnostics["kind"] == "LINEAR_COEFFICIENTS"
    engine.dispose()
