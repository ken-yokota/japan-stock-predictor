"""Persist PIT datasets, fitted linear models, and prediction outputs."""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal

from data.availability import prediction_cutoff
from data.config import AppConfig
from data.market_calendar import japan_sessions_before
from database.models import (
    FeatureSet,
    FeatureValue,
    ModelRun,
    Prediction,
    PredictionSet,
)
from database.repository import PredictionPipelineRepository
from services.dataset import ModelDataset, ModelSample, SourceReference
from services.prediction import PredictionComputation
from services.versioning import (
    FEATURE_VERSION,
    MODEL_VERSION,
    STRATEGY_VERSION,
    config_hash,
    lineage_manifest_hash,
    sha256_json,
)

_QUALITY_ORDER = {
    "OFFICIAL": 4,
    "EOD_CONFIRMED": 3,
    "FREE_UNVERIFIED": 2,
    "DELAYED": 1,
}


def _decimal(value: float | None) -> Decimal | None:
    if value is None:
        return None
    if not math.isfinite(value):
        raise ValueError("persisted numeric values must be finite")
    return Decimal(str(value))


def _quality(references: tuple[SourceReference, ...]) -> str | None:
    if not references:
        return None
    return min(
        (reference.data_quality for reference in references),
        key=lambda value: _QUALITY_ORDER.get(value, 0),
    )


def _source_type(reference: SourceReference) -> str:
    if reference.table_name == "market_data":
        return "MARKET_DATA"
    if reference.table_name == "stock_prices":
        return "STOCK_PRICE"
    raise ValueError(f"unsupported lineage table: {reference.table_name}")


def _warning_list(values: tuple[str, ...], *, limit: int = 20) -> list[str]:
    unique = list(dict.fromkeys(item for item in values if item))
    if len(unique) > limit:
        return [*unique[:limit], f"additional warnings: {len(unique) - limit}"]
    return unique


def _manifest_entry(
    *,
    sample_date: date,
    feature_name: str,
    reference: SourceReference,
) -> dict[str, object]:
    return {
        "sample_date": sample_date.isoformat(),
        "feature_name": feature_name,
        "table": reference.table_name,
        "row_id": reference.row_id,
        "raw_hash": reference.raw_hash,
        "available_at": reference.available_at.isoformat(),
        "first_observed_at": reference.first_observed_at.isoformat(),
        "retrieved_at": reference.retrieved_at.isoformat(),
    }


@dataclass(frozen=True, slots=True)
class _PendingFeatureValue:
    # None for a training cell: it is validated and hashed, but not stored.
    row: FeatureValue | None
    sample_date: date
    feature_name: str
    references: tuple[SourceReference, ...]
    # Only the scored row's lineage is written out as rows. See
    # ``persist_feature_set`` for why the training rows keep the hash instead.
    is_scored: bool = False


def _persist_value(
    repository: PredictionPipelineRepository,
    *,
    feature_set_id: str,
    sample: ModelSample,
    row_role: str,
    feature_name: str,
    value: float | None,
    references: tuple[SourceReference, ...],
    value_kind: str = "FEATURE",
    sample_cutoff_at: datetime | None = None,
    feature_set_cutoff_at: datetime,
    is_scored: bool = False,
    persist: bool = True,
) -> _PendingFeatureValue:
    if not persist:
        # The cutoff check is the one guarantee add_feature_value was making
        # for a training cell, so it is made here instead rather than dropped
        # along with the row.
        cutoff = sample_cutoff_at or sample.cutoff_at
        if cutoff.tzinfo is None or cutoff.utcoffset() is None:
            raise ValueError("sample_cutoff_at must be timezone-aware")
        if cutoff.astimezone(UTC) > feature_set_cutoff_at.astimezone(UTC):
            raise ValueError("sample cutoff cannot exceed feature-set cutoff")
        return _PendingFeatureValue(
            row=None,
            sample_date=sample.sample_date,
            feature_name=feature_name,
            references=references,
            is_scored=is_scored,
        )
    row = repository.add_feature_value(
        feature_set_id=feature_set_id,
        sample_date=sample.sample_date,
        sample_cutoff_at=sample_cutoff_at or sample.cutoff_at,
        row_role=row_role,
        value_kind=value_kind,
        feature_name=feature_name,
        value=_decimal(value),
        is_missing=value is None,
        data_quality=_quality(references),
        flush=False,
    )
    return _PendingFeatureValue(
        row=row,
        sample_date=sample.sample_date,
        feature_name=feature_name,
        references=references,
        is_scored=is_scored,
    )


def persist_feature_set(
    repository: PredictionPipelineRepository,
    *,
    run_id: str,
    prediction_date: date,
    config: AppConfig,
    dataset: ModelDataset,
    terminal_status: str,
    observed_by_cutoff: bool = True,
) -> FeatureSet:
    """Freeze every selected train/score cell and its exact raw-row lineage."""

    sessions = japan_sessions_before(
        prediction_date, config.model.training.window_jpx_sessions
    )
    feature_count = len(dataset.feature_names)
    # Only the scored row is written now. Training cells are validated, folded
    # into the manifest hash and dropped: one morning stored 543,000 of them,
    # 400 MB of a 512 MB ceiling, and nothing in production ever read one. The
    # raw rows they were computed from stay, so a day can still be rebuilt.
    required_count = feature_count
    training_cell_count = len(dataset.training_samples) * (feature_count + 1)
    key = f"feature/{run_id}/{dataset.ticker}/{FEATURE_VERSION}"
    feature_set = repository.create_feature_set(
        run_id=run_id,
        ticker=dataset.ticker,
        prediction_date=prediction_date,
        cutoff_at=dataset.current_sample.cutoff_at,
        feature_version=FEATURE_VERSION,
        set_kind="MORNING",
        training_start=sessions[0],
        training_end=sessions[-1],
        config_hash=config_hash(config),
        required_feature_count=required_count,
        idempotency_key=key,
        details={
            "feature_names": list(dataset.feature_names),
            "candidate_feature_count": dataset.candidate_feature_count,
            "feature_coverage": dataset.feature_coverage,
            "target": "raw_close/raw_open-1",
            # Only the misses are stored. The expected set is reconstructible
            # from the configuration for this feature version, so repeating it
            # on every feature set would cost storage to say nothing new -
            # and the hosted project has 512 MB in total.
            "expected_indicator_count": len(dataset.expected_indicators),
            "observed_indicator_count": len(dataset.observed_indicators),
            "indicator_coverage": dataset.indicator_coverage,
            "missing_required_indicators": list(dataset.missing_required_indicators),
            "missing_optional_indicators": list(dataset.missing_optional_indicators),
        },
    )
    if feature_set.status != "BUILDING":
        return feature_set

    pending_values: list[_PendingFeatureValue] = []
    for sample in dataset.training_samples:
        for name in dataset.feature_names:
            value = sample.values.get(name)
            references = sample.lineage.get(name, ()) if value is not None else ()
            pending_values.append(
                _persist_value(
                    repository,
                    feature_set_id=feature_set.feature_set_id,
                    sample=sample,
                    row_role="TRAIN",
                    feature_name=name,
                    value=value,
                    references=references,
                    feature_set_cutoff_at=feature_set.cutoff_at,
                    persist=False,
                )
            )
        pending_values.append(
            _persist_value(
                repository,
                feature_set_id=feature_set.feature_set_id,
                sample=sample,
                row_role="TRAIN",
                feature_name="intraday_return",
                value=sample.target_return,
                references=sample.target_lineage,
                value_kind="TARGET",
                feature_set_cutoff_at=feature_set.cutoff_at,
                persist=False,
                # The target becomes a permissible training label only once it
                # is known by the current prediction cutoff, not at its own
                # session's 08:30 feature cutoff.
                sample_cutoff_at=dataset.current_sample.cutoff_at,
            )
        )
    for name in dataset.feature_names:
        pending_values.append(
            _persist_value(
                repository,
                feature_set_id=feature_set.feature_set_id,
                sample=dataset.current_sample,
                row_role="SCORE",
                feature_name=name,
                value=dataset.current_sample.values.get(name),
                references=dataset.current_sample.lineage.get(name, ()),
                feature_set_cutoff_at=feature_set.cutoff_at,
                is_scored=True,
            )
        )
    # Autoincrement IDs are needed by lineage rows. Assign every feature ID in
    # one flush, then stage all lineage and flush that batch once as well.
    repository.flush_pending()
    # A morning's training rows carried 340,000 lineage rows -- 102 MB of the
    # 156 MB one run wrote, and the reason the hosted database hit its 512 MB
    # ceiling before any prediction was ever published. Those rows are audit
    # evidence, never model input, and the manifest hash below is built from
    # every one of them either way. So the hash still proves that no training
    # input was substituted; only the ability to walk a single training cell
    # back to its raw revision by query is given up. The scored row -- the one
    # the published prediction is actually computed from -- keeps its lineage
    # rows, because that is the provenance anyone would ask about.
    scored = [pending for pending in pending_values if pending.is_scored]
    market_data_ids: set[int] = set()
    stock_price_ids: set[int] = set()
    for pending in scored:
        for reference in pending.references:
            if _source_type(reference) == "MARKET_DATA":
                market_data_ids.add(reference.row_id)
            else:
                stock_price_ids.add(reference.row_id)
    repository.preload_feature_input_sources(
        market_data_ids=market_data_ids,
        stock_price_ids=stock_price_ids,
    )
    manifest: list[dict[str, object]] = []
    for pending in pending_values:
        if pending.is_scored and (
            pending.row is None or pending.row.feature_value_id is None
        ):
            raise RuntimeError("feature value ID was not assigned by batch flush")
        for index, reference in enumerate(pending.references, start=1):
            if pending.is_scored and pending.row is not None:
                repository.add_feature_input(
                    feature_value_id=pending.row.feature_value_id,
                    input_role=f"source_{index:03d}",
                    source_type=_source_type(reference),
                    source_row_id=reference.row_id,
                    flush=False,
                    observed_by_cutoff=observed_by_cutoff,
                )
            manifest.append(
                _manifest_entry(
                    sample_date=pending.sample_date,
                    feature_name=pending.feature_name,
                    reference=reference,
                )
            )
    repository.flush_pending()
    manifest.sort(
        key=lambda item: (
            str(item["sample_date"]),
            str(item["feature_name"]),
            str(item["table"]),
            int(str(item["row_id"])),
        )
    )
    return repository.finalize_feature_set(
        feature_set,
        status=terminal_status,
        input_manifest_hash=(
            lineage_manifest_hash(manifest) if terminal_status == "READY" else None
        ),
        details={
            "feature_names": list(dataset.feature_names),
            "candidate_feature_count": dataset.candidate_feature_count,
            "feature_coverage": dataset.feature_coverage,
            "warnings": _warning_list(dataset.current_sample.warnings),
            # finalize replaces details wholesale, so completeness has to be
            # restated here or it would be written at creation and then lost -
            # which is why the first audit reported every stock as UNKNOWN.
            "expected_indicator_count": len(dataset.expected_indicators),
            "observed_indicator_count": len(dataset.observed_indicators),
            "indicator_coverage": dataset.indicator_coverage,
            "missing_required_indicators": list(dataset.missing_required_indicators),
            "missing_optional_indicators": list(dataset.missing_optional_indicators),
            "training_cells_validated_not_stored": training_cell_count,
        },
    )


def persist_failed_feature_set(
    repository: PredictionPipelineRepository,
    *,
    run_id: str,
    ticker: str,
    prediction_date: date,
    config: AppConfig,
    reason: str,
) -> FeatureSet:
    sessions = japan_sessions_before(
        prediction_date, config.model.training.window_jpx_sessions
    )
    row = repository.create_feature_set(
        run_id=run_id,
        ticker=ticker,
        prediction_date=prediction_date,
        cutoff_at=prediction_cutoff(
            prediction_date,
            cutoff_time=config.settings.schedule.prediction_cutoff,
            timezone_name=config.settings.application.timezone,
        ),
        feature_version=FEATURE_VERSION,
        set_kind="MORNING",
        training_start=sessions[0],
        training_end=sessions[-1],
        config_hash=config_hash(config),
        required_feature_count=0,
        idempotency_key=f"feature/{run_id}/{ticker}/{FEATURE_VERSION}",
        details={"reason": reason},
    )
    if row.status == "BUILDING":
        repository.finalize_feature_set(
            row,
            status="FAILED",
            input_manifest_hash=None,
            details={"reason": reason},
        )
    return row


def _persist_model(
    repository: PredictionPipelineRepository,
    *,
    run_id: str,
    feature_set: FeatureSet,
    computation: PredictionComputation,
    task: str,
) -> ModelRun:
    model = computation.model
    if model is None:
        raise ValueError("a fitted model is required")
    is_regression = task == "REGRESSION"
    coefficients = (
        model.regression_coefficients()
        if is_regression
        else model.classification_coefficients()
    )
    scaler = model.scaler_statistics(
        "regression" if is_regression else "classification"
    )
    constant_probability = (
        None if is_regression else model.classification_constant_probability()
    )
    intercept = (
        model.regression_intercept()
        if is_regression
        else model.classification_intercept()
    )
    if intercept is None:
        # Constant-probability classification is reconstructed from parameters;
        # zero is a finite storage sentinel, not a fitted logistic intercept.
        intercept = 0.0
    algorithm = (
        "ridge"
        if is_regression
        else (
            "logistic_regression"
            if constant_probability is None
            else "constant_probability"
        )
    )
    parameters: dict[str, object] = (
        {"alpha": model.ridge_alpha}
        if is_regression
        else (
            {"C": model.logistic_c}
            if constant_probability is None
            else {"constant_probability_up": constant_probability}
        )
    )
    model_run = repository.create_model_run(
        run_id=run_id,
        ticker=computation.result.ticker,
        feature_set_id=feature_set.feature_set_id,
        task=task,
        algorithm=algorithm,
        training_start=feature_set.training_start,
        training_end=feature_set.training_end,
        cutoff_at=feature_set.cutoff_at,
        training_rows=computation.result.training_sessions,
        feature_version=FEATURE_VERSION,
        model_version=MODEL_VERSION,
        random_seed=42,
        parameters=parameters,
        cv_results={
            "strategy": "TimeSeriesSplit",
            "selected_parameters": parameters,
        },
        idempotency_key=(f"model/{run_id}/{computation.result.ticker}/{task.lower()}"),
    )
    if model_run.status != "RUNNING":
        return model_run
    for name in model.feature_names:
        repository.add_model_coefficient(
            model_run_id=model_run.model_run_id,
            feature_name=name,
            coefficient=Decimal(str(coefficients[name])),
            scaler_mean=(
                Decimal(str(scaler.means[name])) if scaler is not None else None
            ),
            scaler_scale=(
                Decimal(str(scaler.scales[name])) if scaler is not None else None
            ),
            flush=False,
        )
    artifact_hash = sha256_json(
        {
            "task": task,
            "algorithm": algorithm,
            "features": list(model.feature_names),
            "coefficients": coefficients,
            "intercept": intercept,
            "scaler_means": scaler.means if scaler is not None else None,
            "scaler_scales": scaler.scales if scaler is not None else None,
            "parameters": parameters,
        }
    )
    return repository.finish_model_run(
        model_run,
        status="SUCCESS",
        intercept=Decimal(str(intercept)),
        artifact_hash=artifact_hash,
    )


@dataclass(frozen=True, slots=True)
class PersistedTickerPrediction:
    feature_set: FeatureSet
    regression_model: ModelRun | None
    classification_model: ModelRun | None
    prediction: Prediction


def persist_prediction_computation(
    repository: PredictionPipelineRepository,
    *,
    run_id: str,
    prediction_set: PredictionSet,
    computation: PredictionComputation,
    config: AppConfig,
    rank: int | None,
    observed_by_cutoff: bool = True,
) -> PersistedTickerPrediction:
    result = computation.result
    success = result.status == "READY" and computation.model is not None
    feature_set = persist_feature_set(
        repository,
        run_id=run_id,
        prediction_date=result.prediction_date,
        config=config,
        dataset=computation.dataset,
        terminal_status="READY" if success else "INSUFFICIENT_DATA",
        observed_by_cutoff=observed_by_cutoff,
    )
    regression_model = None
    classification_model = None
    if success:
        regression_model = _persist_model(
            repository,
            run_id=run_id,
            feature_set=feature_set,
            computation=computation,
            task="REGRESSION",
        )
        classification_model = _persist_model(
            repository,
            run_id=run_id,
            feature_set=feature_set,
            computation=computation,
            task="CLASSIFICATION",
        )
    reference = computation.dataset.current_sample.reference_source
    prediction = repository.add_prediction(
        prediction_set_id=prediction_set.prediction_set_id,
        ticker=result.ticker,
        feature_set_id=feature_set.feature_set_id,
        regression_model_run_id=(
            regression_model.model_run_id if regression_model is not None else None
        ),
        classification_model_run_id=(
            classification_model.model_run_id
            if classification_model is not None
            else None
        ),
        status="SUCCESS" if success else "INSUFFICIENT_DATA",
        predicted_intraday_return=_decimal(result.predicted_return),
        probability_up=_decimal(result.probability_up),
        reference_stock_price_id=(
            reference.row_id
            if reference is not None and reference.table_name == "stock_prices"
            else None
        ),
        reference_price=_decimal(result.reference_price),
        reference_basis="PREVIOUS_CLOSE",
        predicted_price_difference=_decimal(result.predicted_difference),
        predicted_close=_decimal(result.predicted_close),
        signal=result.signal if success else "NONE",
        rank=rank if success and result.signal == "BUY" else None,
        return_threshold=Decimal(
            str(config.trading.signal.predicted_intraday_return_threshold)
        ),
        probability_threshold=Decimal(
            str(config.trading.signal.probability_up_threshold)
        ),
        confidence_score=_decimal(result.confidence_score) if success else None,
        prediction_interval_low=_decimal(result.prediction_interval_low),
        prediction_interval_high=_decimal(result.prediction_interval_high),
        return_distribution=(
            result.distribution.to_payload()
            if result.distribution is not None
            else None
        ),
        arm_predictions=(
            [arm.to_payload() for arm in result.arm_forecasts]
            if result.arm_forecasts
            else None
        ),
        positive_factors=list(result.positive_factors),
        negative_factors=list(result.negative_factors),
        feature_coverage=result.feature_coverage,
        warnings=_warning_list(result.warnings),
        observed_by_cutoff=observed_by_cutoff,
        idempotency_key=f"prediction/{run_id}/{result.ticker}",
    )
    return PersistedTickerPrediction(
        feature_set,
        regression_model,
        classification_model,
        prediction,
    )


def persist_failed_prediction(
    repository: PredictionPipelineRepository,
    *,
    run_id: str,
    prediction_set: PredictionSet,
    ticker: str,
    prediction_date: date,
    config: AppConfig,
    reason: str,
) -> Prediction:
    feature_set = persist_failed_feature_set(
        repository,
        run_id=run_id,
        ticker=ticker,
        prediction_date=prediction_date,
        config=config,
        reason=reason,
    )
    return repository.add_prediction(
        prediction_set_id=prediction_set.prediction_set_id,
        ticker=ticker,
        feature_set_id=feature_set.feature_set_id,
        regression_model_run_id=None,
        classification_model_run_id=None,
        status="FAILED",
        predicted_intraday_return=None,
        probability_up=None,
        reference_stock_price_id=None,
        reference_price=None,
        reference_basis="PREVIOUS_CLOSE",
        predicted_price_difference=None,
        predicted_close=None,
        signal="NONE",
        rank=None,
        return_threshold=Decimal(
            str(config.trading.signal.predicted_intraday_return_threshold)
        ),
        probability_threshold=Decimal(
            str(config.trading.signal.probability_up_threshold)
        ),
        confidence_score=None,
        idempotency_key=f"prediction/{run_id}/{ticker}",
        warnings=[reason],
        feature_coverage=0.0,
    )


def prediction_set_versions() -> tuple[str, str, str]:
    return FEATURE_VERSION, MODEL_VERSION, STRATEGY_VERSION
