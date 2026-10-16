"""Company profiles keep all required factors, with auditable daily fits."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, date, timedelta
from decimal import Decimal

import numpy as np
import pandas as pd
import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

import services.prediction as prediction_service
from data.availability import prediction_cutoff
from data.company_profiles import CompanyFactor, CompanyProfile, CompanyProfilesConfig
from data.config import AppConfig, load_app_config
from data.market_calendar import japan_session_close, japan_sessions_before
from database.models import Base, FeatureValue, MarketData
from database.repository import MarketDataRepository, PredictionPipelineRepository
from models.arms import ArmForecast
from models.base import ModelTrainingConfig
from models.distribution import ReturnDistribution
from services.dataset import ModelDataset, ModelSample, SourceReference
from services.persistence import persist_prediction_computation, prediction_set_versions
from services.prediction import PredictionService
from services.versioning import MODEL_VERSION, config_hash

TODAY = date(2026, 10, 15)
FACTOR_IDS = (
    "usdjpy",
    "eurjpy",
    "us_2y_yield",
    "nikkei225_futures",
    "fxi",
    "wti",
    "copper",
)


class NoDatasetIO:
    def build(self, *args, **kwargs):
        raise AssertionError("supplied datasets should not trigger database reads")


@pytest.fixture
def profile() -> CompanyProfile:
    return CompanyProfile(
        ticker="7203",
        name="Toyota test profile",
        valid_from=TODAY,
        thesis="Distinct business exposures with a coefficient for every group",
        evidence="Official company disclosures, fixture only",
        factors=[
            CompanyFactor(
                id=name,
                label=name,
                series_id=name,
                transform="return_1d",
                frequency="daily",
                max_age_days=7,
                mechanism="A distinct daily business exposure",
                acquisition="Fixture values available",
                source_urls=["https://global.toyota/en/ir/"],
            )
            for name in FACTOR_IDS
        ],
    )


@pytest.fixture
def config(profile: CompanyProfile) -> AppConfig:
    base = load_app_config()
    model = base.model.model_copy(
        update={
            "models": base.model.models.model_copy(update={"run_all_arms": False}),
            "cross_validation": base.model.cross_validation.model_copy(
                update={"n_splits": 2}
            ),
            "hyperparameters": base.model.hyperparameters.model_copy(
                update={
                    "ridge_alpha": [1.0],
                    "logistic_c": [1.0],
                    "quantile_alpha": [0.01],
                    "quantile_levels": [0.05, 0.5, 0.95],
                }
            ),
        }
    )
    return base.model_copy(
        update={
            "model": model,
            "company_profiles": CompanyProfilesConfig(profiles=[profile]),
        }
    )


@pytest.fixture
def dataset(profile: CompanyProfile) -> ModelDataset:
    rng = np.random.default_rng(306)
    names = tuple(factor.feature_name for factor in profile.factors)
    days = japan_sessions_before(TODAY, 120)
    values = rng.normal(scale=0.01, size=(120, len(names)))
    frame = pd.DataFrame(values, index=days, columns=names)
    target = pd.Series(
        values[:, 0] * 0.6 - values[:, 1] * 0.3 + rng.normal(0.0, 0.003, 120),
        index=days,
    )
    current = pd.DataFrame(
        rng.normal(scale=0.01, size=(1, len(names))), index=[TODAY], columns=names
    )
    lineage = {}
    for index, factor in enumerate(profile.factors):
        references = []
        for offset, day in enumerate(days[-2:]):
            row_id = index * 2 + offset + 1
            available_at = japan_session_close(day) + timedelta(minutes=20)
            references.append(
                SourceReference(
                    table_name="market_data",
                    row_id=row_id,
                    canonical_symbol=factor.series_id,
                    market_date=day,
                    available_at=available_at,
                    first_observed_at=available_at,
                    retrieved_at=available_at,
                    raw_hash=f"{row_id:064x}",
                    data_quality="FREE_UNVERIFIED",
                )
            )
        lineage[factor.feature_name] = tuple(references)
    sample = ModelSample(
        ticker="7203",
        sample_date=TODAY,
        cutoff_at=prediction_cutoff(TODAY),
        values=current.iloc[0].to_dict(),
        lineage=lineage,
        reference_price=100.0,
    )
    return ModelDataset(
        ticker="7203",
        feature_names=names,
        training_frame=frame,
        training_target=target,
        current_frame=current,
        training_samples=(),
        current_sample=sample,
        candidate_feature_count=len(names),
        feature_coverage=1.0,
        expected_indicators=FACTOR_IDS,
        observed_indicators=FACTOR_IDS,
        company_profile=profile,
    )


@pytest.mark.parametrize(
    "problem,reason",
    [
        ("missing_flag", "missing required factors"),
        ("missing_column", "schema mismatch"),
        ("extra_macro", "schema mismatch"),
        ("missing_current", "missing current factors"),
        ("poor_history", "insufficient training coverage"),
        ("missing_profile", "metadata does not match"),
    ],
)
def test_incomplete_profile_never_fits_a_reduced_or_generic_model(
    problem: str,
    reason: str,
    config: AppConfig,
    dataset: ModelDataset,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    name = dataset.feature_names[0]
    if problem == "missing_flag":
        dataset = replace(dataset, missing_profile_factors=(FACTOR_IDS[0],))
    elif problem == "missing_column":
        dataset = replace(
            dataset,
            feature_names=dataset.feature_names[1:],
            training_frame=dataset.training_frame.drop(columns=name),
            current_frame=dataset.current_frame.drop(columns=name),
        )
    elif problem == "extra_macro":
        frame = dataset.training_frame.assign(sp500__return_1d=0.01)
        dataset = replace(dataset, training_frame=frame)
    elif problem == "missing_current":
        frame = dataset.current_frame.copy()
        frame[name] = np.nan
        dataset = replace(dataset, current_frame=frame)
    elif problem == "poor_history":
        frame = dataset.training_frame.copy()
        frame.loc[frame.index[:40], name] = np.nan
        dataset = replace(dataset, training_frame=frame)
    else:
        dataset = replace(dataset, company_profile=None)

    def unexpected_fit(*args, **kwargs):
        raise AssertionError("incomplete company profiles must fail before fitting")

    monkeypatch.setattr(prediction_service, "train_ticker_model", unexpected_fit)
    result = PredictionService(NoDatasetIO(), config).compute(
        "7203", TODAY, dataset=dataset, operational=False
    )
    assert result.model is None
    assert result.result.status == "INSUFFICIENT_DATA"
    assert reason in result.result.warnings[0]


@pytest.mark.parametrize(
    "problem,reason",
    [
        ("unconfigured_profile", "metadata does not match"),
        ("different_sample_value", "frame and sample values do not match"),
        ("missing_sample_value", "current sample schema mismatch"),
        ("extra_sample_value", "current sample schema mismatch"),
        ("nonfinite_sample_value", "frame and sample values do not match"),
        ("wrong_sample_ticker", "ticker/date does not match"),
        ("later_cutoff", "current cutoff does not match"),
        ("earlier_cutoff", "current cutoff does not match"),
        ("naive_cutoff", "current cutoff does not match"),
        ("missing_lineage", "feature lineage missing"),
        ("empty_lineage", "feature lineage must not be empty"),
        ("late_reference", "feature source became available after cutoff"),
    ],
)
def test_supplied_profile_dataset_must_match_its_audit_evidence(
    problem: str,
    reason: str,
    config: AppConfig,
    dataset: ModelDataset,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sample = dataset.current_sample
    name = dataset.feature_names[0]
    if problem == "unconfigured_profile":
        config = config.model_copy(update={"company_profiles": CompanyProfilesConfig()})
    elif problem.endswith("sample_value"):
        values = sample.values.copy()
        if problem == "different_sample_value":
            values[name] += 1.0
        elif problem == "missing_sample_value":
            del values[name]
        elif problem == "extra_sample_value":
            values["stock__return_1d"] = 0.01
        else:
            values[name] = float("nan")
        sample = replace(sample, values=values)
    elif problem == "wrong_sample_ticker":
        sample = replace(sample, ticker="8306")
    elif problem.endswith("cutoff"):
        cutoff = sample.cutoff_at
        if problem == "naive_cutoff":
            cutoff = cutoff.replace(tzinfo=None)
        else:
            delta = 1 if problem == "later_cutoff" else -1
            cutoff += timedelta(microseconds=delta)
        sample = replace(sample, cutoff_at=cutoff)
    elif problem == "late_reference":
        reference = replace(
            sample.lineage[name][-1],
            available_at=sample.cutoff_at + timedelta(microseconds=1),
        )
        sample = replace(sample, reference_source=reference)
    else:
        lineage = sample.lineage.copy()
        if problem == "missing_lineage":
            del lineage[name]
        else:
            lineage[name] = ()
        sample = replace(sample, lineage=lineage)
    dataset = replace(dataset, current_sample=sample)

    def unexpected_fit(*args, **kwargs):
        raise AssertionError("inconsistent supplied datasets must fail before fitting")

    monkeypatch.setattr(prediction_service, "train_ticker_model", unexpected_fit)
    result = PredictionService(NoDatasetIO(), config).compute(
        "7203", TODAY, dataset=dataset, operational=False
    )
    assert result.model is None
    assert result.result.status == "INSUFFICIENT_DATA"
    assert reason in result.result.warnings[0]


@pytest.mark.parametrize("operational", [False, True])
def test_unpublished_profile_source_is_rejected_in_live_and_replay(
    operational: bool,
    config: AppConfig,
    dataset: ModelDataset,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    name = dataset.feature_names[0]
    lineage = dataset.current_sample.lineage.copy()
    lineage[name] = (
        replace(
            lineage[name][-1],
            available_at=prediction_cutoff(TODAY) + timedelta(microseconds=1),
        ),
    )
    dataset = replace(
        dataset, current_sample=replace(dataset.current_sample, lineage=lineage)
    )

    def unexpected_fit(*args, **kwargs):
        raise AssertionError("unpublished data must fail before fitting")

    monkeypatch.setattr(prediction_service, "train_ticker_model", unexpected_fit)
    result = PredictionService(NoDatasetIO(), config).compute(
        "7203", TODAY, dataset=dataset, operational=operational
    )
    assert result.result.status == "INSUFFICIENT_DATA"
    assert "became available after cutoff" in result.result.warnings[0]


@pytest.mark.parametrize("late_field", ["first_observed_at", "retrieved_at"])
def test_profile_replay_allows_late_fetch_but_live_requires_cutoff_evidence(
    late_field: str, config: AppConfig, dataset: ModelDataset
) -> None:
    name = dataset.feature_names[0]
    lineage = dataset.current_sample.lineage.copy()
    lineage[name] = (
        replace(
            lineage[name][-1],
            **{late_field: prediction_cutoff(TODAY) + timedelta(days=1)},
        ),
    )
    dataset = replace(
        dataset, current_sample=replace(dataset.current_sample, lineage=lineage)
    )
    service = PredictionService(NoDatasetIO(), config)
    live = service.compute("7203", TODAY, dataset=dataset)
    replay = service.compute("7203", TODAY, dataset=dataset, operational=False)
    assert live.result.status == "INSUFFICIENT_DATA"
    assert "observed after cutoff" in live.result.warnings[0]
    assert replay.result.status == "READY"


def test_same_cutoff_in_utc_and_reordered_columns_are_consistent(
    config: AppConfig, dataset: ModelDataset
) -> None:
    dataset = replace(
        dataset,
        current_frame=dataset.current_frame.loc[
            :, list(reversed(dataset.feature_names))
        ],
        current_sample=replace(
            dataset.current_sample,
            cutoff_at=dataset.current_sample.cutoff_at.astimezone(UTC),
        ),
    )
    service = PredictionService(NoDatasetIO(), config)
    assert service._profile_issue("7203", TODAY, dataset, operational=True) is None


def test_complete_profile_refits_every_factor_each_day(
    config: AppConfig, dataset: ModelDataset, monkeypatch: pytest.MonkeyPatch
) -> None:
    original = prediction_service.train_ticker_model
    fits: list[ModelTrainingConfig] = []

    def record_fit(*args, **kwargs):
        fits.append(kwargs["config"])
        return original(*args, **kwargs)

    monkeypatch.setattr(prediction_service, "train_ticker_model", record_fit)
    service = PredictionService(NoDatasetIO(), config)
    first = service.compute("7203", TODAY, dataset=dataset, operational=False)
    tomorrow = TODAY + timedelta(days=1)
    next_dataset = replace(
        dataset,
        training_target=-dataset.training_target,
        current_frame=dataset.current_frame.set_axis([tomorrow]),
        current_sample=replace(
            dataset.current_sample,
            sample_date=tomorrow,
            cutoff_at=prediction_cutoff(tomorrow),
        ),
    )
    second = service.compute("7203", tomorrow, dataset=next_dataset, operational=False)

    assert len(fits) == 2
    assert first.result.status == second.result.status == "READY"
    assert first.model is not None and second.model is not None
    assert (
        first.model.feature_names == second.model.feature_names == dataset.feature_names
    )
    assert set(first.result.coefficients) == set(dataset.feature_names)
    name = dataset.feature_names[0]
    assert first.result.coefficients[name] == pytest.approx(
        -second.result.coefficients[name]
    )
    evidence = first.result.distribution.to_payload()["coverage_evidence"]
    assert evidence["status"] == "not_evaluated"
    assert evidence["samples"] == 0
    assert evidence["intervals"] == []
    assert evidence["profile_valid_from"] == TODAY.isoformat()


def test_comparison_distributions_cannot_inherit_old_profile_coverage(
    config: AppConfig, dataset: ModelDataset, monkeypatch: pytest.MonkeyPatch
) -> None:
    models = config.model.models.model_copy(update={"run_all_arms": True})
    config = config.model_copy(
        update={"model": config.model.model_copy(update={"models": models})}
    )
    distribution = ReturnDistribution(
        levels=(0.05, 0.5, 0.95),
        values=(-0.02, 0.0, 0.02),
        alpha=0.01,
        training_sessions=120,
    )
    arm = ArmForecast(name="test", label="Test", distribution=distribution)

    def fake_arms(features, targets, current, **kwargs):
        assert tuple(features.columns) == dataset.feature_names
        return (arm,)

    monkeypatch.setattr(prediction_service, "run_arms", fake_arms)
    result = PredictionService(NoDatasetIO(), config).compute(
        "7203", TODAY, dataset=dataset, operational=False
    )
    payload = result.result.arm_forecasts[0].to_payload()["distribution"]
    assert payload["coverage_evidence"]["status"] == "not_evaluated"
    assert payload["coverage_evidence"]["samples"] == 0


@pytest.mark.parametrize("single_class", [False, True])
def test_profile_and_coefficient_units_survive_actual_persistence(
    single_class: bool, config: AppConfig, dataset: ModelDataset
) -> None:
    if single_class:
        dataset = replace(
            dataset,
            training_target=dataset.training_target.abs() + 0.01,
        )
    computation = PredictionService(NoDatasetIO(), config).compute(
        "7203", TODAY, dataset=dataset, operational=False
    )
    assert computation.model is not None
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        for name, references in dataset.current_sample.lineage.items():
            for index, reference in enumerate(references):
                close = 100.0
                if index:
                    close *= 1.0 + dataset.current_sample.values[name]
                session.add(
                    MarketData(
                        id=reference.row_id,
                        canonical_symbol=reference.canonical_symbol,
                        symbol=f"SYNTHETIC-{reference.canonical_symbol}",
                        provider="yahoo_finance",
                        market="JP",
                        market_timezone="Asia/Tokyo",
                        market_date=reference.market_date,
                        timestamp=japan_session_close(reference.market_date),
                        source_timestamp=japan_session_close(reference.market_date),
                        available_timestamp=reference.available_at,
                        first_observed_at=reference.first_observed_at,
                        retrieved_at=reference.retrieved_at,
                        last_seen_at=reference.retrieved_at,
                        interval="eod",
                        availability_method="provider_sla_estimate",
                        data_quality=reference.data_quality,
                        is_realtime=False,
                        is_delayed=True,
                        close=Decimal(str(close)),
                        currency="JPY",
                        raw_hash=reference.raw_hash,
                        quality_flags=[],
                    )
                )
        session.flush()
        repository = PredictionPipelineRepository(session)
        run = MarketDataRepository(session).create_run(
            run_type="MORNING",
            prediction_date=TODAY,
            cutoff_at=prediction_cutoff(TODAY),
            data_version=config_hash(config),
        )
        versions = prediction_set_versions()
        days = japan_sessions_before(TODAY, 120)
        prediction_set = repository.create_prediction_set(
            run_id=run.run_id,
            prediction_date=TODAY,
            cutoff_at=prediction_cutoff(TODAY),
            feature_version=versions[0],
            model_version=versions[1],
            strategy_version=versions[2],
            training_start=days[0],
            training_end=days[-1],
            idempotency_key="company-profile-test",
        )
        persisted = persist_prediction_computation(
            repository,
            run_id=run.run_id,
            prediction_set=prediction_set,
            computation=computation,
            config=config,
            rank=None,
        )
        session.flush()
        assert persisted.feature_set.details["company_profile"] == (
            dataset.company_profile.model_dump(mode="json")
        )
        assert persisted.feature_set.details["missing_profile_factors"] == []
        stored_values = session.scalars(
            select(FeatureValue).where(
                FeatureValue.feature_set_id == persisted.feature_set.feature_set_id,
                FeatureValue.row_role == "SCORE",
            )
        ).all()
        assert len(stored_values) == len(dataset.feature_names)
        for stored in stored_values:
            assert float(stored.value) == pytest.approx(
                dataset.current_frame.iloc[0][stored.feature_name]
            )
        assert persisted.regression_model is not None
        assert persisted.regression_model.model_version == MODEL_VERSION
        assert MODEL_VERSION.endswith("v4")
        audit = persisted.regression_model.parameters["company_profile"]
        assert audit["valid_from"] == TODAY.isoformat()
        assert audit["prediction_date"] == TODAY.isoformat()
        assert audit["factor_count"] == 7
        assert audit["coefficient_response_unit"] == "intraday_return_fraction"
        coefficients = audit["factor_coefficients"]
        assert set(coefficients) == set(FACTOR_IDS)
        reconstructed = audit["original_feature_intercept"]
        for factor in dataset.company_profile.factors:
            row = coefficients[factor.id]
            assert row["coefficient_per_original_feature_unit"] == pytest.approx(
                row["standardized_coefficient"] / row["scaler_scale"]
            )
            reconstructed += (
                row["coefficient_per_original_feature_unit"]
                * dataset.current_frame.iloc[0][factor.feature_name]
            )
        assert reconstructed == pytest.approx(computation.result.predicted_return)
        assert persisted.classification_model is not None
        classification = persisted.classification_model.parameters["company_profile"]
        if single_class:
            assert all(
                row["standardized_coefficient"] is None
                and row["coefficient_per_original_feature_unit"] is None
                and row["status"] == "constant_probability"
                for row in classification["factor_coefficients"].values()
            )
        else:
            assert classification["coefficient_response_unit"] == "upward_log_odds"
        assert (
            persisted.prediction.return_distribution["coverage_evidence"]["samples"]
            == 0
        )
    engine.dispose()
