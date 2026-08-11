"""Ticker-level training and current prediction application service."""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date

import numpy as np

from data.config import AppConfig
from models import (
    InsufficientTrainingData,
    ModelTrainingConfig,
    TickerModelBundle,
    train_ticker_model,
)
from scoring.confidence import calculate_confidence_score
from services.dataset import ModelDataset, PointInTimeDatasetBuilder
from trading.strategy import BuySignalConfig, is_buy_signal


@dataclass(frozen=True, slots=True)
class PredictionResult:
    """Complete explainable result ready for persistence and ranking."""

    ticker: str
    prediction_date: date
    status: str
    predicted_return: float | None = None
    probability_up: float | None = None
    prediction_interval_low: float | None = None
    prediction_interval_high: float | None = None
    reference_price: float | None = None
    predicted_difference: float | None = None
    predicted_close: float | None = None
    signal: str = "NO_BUY"
    confidence_score: float = 0.0
    feature_coverage: float = 0.0
    training_sessions: int = 0
    ridge_alpha: float | None = None
    logistic_c: float | None = None
    coefficients: dict[str, float] = field(default_factory=dict)
    positive_factors: tuple[str, ...] = field(default_factory=tuple)
    negative_factors: tuple[str, ...] = field(default_factory=tuple)
    warnings: tuple[str, ...] = field(default_factory=tuple)


@dataclass(frozen=True, slots=True)
class PredictionComputation:
    """Result plus the exact dataset/model objects required for persistence."""

    dataset: ModelDataset
    model: TickerModelBundle | None
    result: PredictionResult


def _drivers(
    model: TickerModelBundle,
    dataset: ModelDataset,
    coefficients: dict[str, float],
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Name the predictors that actually moved today's number, and by how much.

    Ranking by coefficient alone answers "what does this model weigh in
    general", which is not the question a reader of one morning's prediction is
    asking. A large coefficient on a feature sitting at its training average
    contributes nothing today. The product of the coefficient and the
    standardized value is the contribution, and Ridge's prediction is exactly
    the intercept plus the sum of those contributions, so the parts add up to
    the whole and can be quoted in the same units.

    Falls back to the coefficient ordering if the scaler statistics are
    unavailable for any reason: a morning email with a weaker explanation is
    better than a morning pipeline that fails while building one.
    """

    def by_coefficient() -> tuple[tuple[str, ...], tuple[str, ...]]:
        ordered = sorted(coefficients.items(), key=lambda item: item[1], reverse=True)
        return (
            tuple(name for name, value in ordered if value > 0.0)[:3],
            tuple(name for name, value in reversed(ordered) if value < 0.0)[:3],
        )

    try:
        statistics = model.scaler_statistics("regression")
        if statistics is None or dataset.current_frame.empty:
            return by_coefficient()
        row = dataset.current_frame.iloc[0]
        contributions: list[tuple[str, float]] = []
        for name in model.feature_names:
            raw = row.get(name)
            value = float(raw) if isinstance(raw, int | float) else float("nan")
            if not math.isfinite(value):
                # Imputed to the training median, so it standardizes to about
                # zero and contributes about nothing. Reporting it as a driver
                # would be inventing an explanation for a missing input.
                continue
            scale = statistics.scales.get(name) or 1.0
            standardized = (value - statistics.means.get(name, 0.0)) / scale
            contributions.append((name, coefficients.get(name, 0.0) * standardized))
    except Exception:
        return by_coefficient()

    if not contributions:
        return by_coefficient()
    contributions.sort(key=lambda item: item[1], reverse=True)
    return (
        tuple(
            f"{name} ({value * 100:+.2f}%)"
            for name, value in contributions
            if value > 0.0
        )[:3],
        tuple(
            f"{name} ({value * 100:+.2f}%)"
            for name, value in reversed(contributions)
            if value < 0.0
        )[:3],
    )


class PredictionService:
    """Build a PIT dataset, fit linear models, and make one morning prediction."""

    def __init__(
        self,
        dataset_builder: PointInTimeDatasetBuilder,
        config: AppConfig,
    ) -> None:
        self._datasets = dataset_builder
        self._config = config

    def _model_config(self) -> ModelTrainingConfig:
        model = self._config.model
        return ModelTrainingConfig(
            window_size=model.training.window_jpx_sessions,
            minimum_training_sessions=model.training.minimum_complete_rows,
            time_series_splits=model.cross_validation.n_splits,
            ridge_alphas=tuple(model.hyperparameters.ridge_alpha),
            logistic_cs=tuple(model.hyperparameters.logistic_c),
            random_state=model.reproducibility.random_seed,
        )

    def _insufficient(
        self,
        ticker: str,
        prediction_date: date,
        dataset: ModelDataset,
        reason: str,
    ) -> PredictionResult:
        warnings = tuple(dict.fromkeys((reason, *dataset.current_sample.warnings)))
        return PredictionResult(
            ticker=ticker,
            prediction_date=prediction_date,
            status="INSUFFICIENT_DATA",
            reference_price=dataset.current_sample.reference_price,
            feature_coverage=dataset.feature_coverage,
            training_sessions=len(dataset.training_frame),
            warnings=warnings,
        )

    def compute(
        self,
        ticker: str,
        prediction_date: date,
        *,
        readability_score: float = 0.0,
        operational: bool = True,
    ) -> PredictionComputation:
        """Compute one result while retaining reproducibility artifacts."""

        missing_limit = self._config.model.features.max_missing_ratio
        if missing_limit is None:
            raise ValueError("feature missing threshold is not confirmed")
        dataset = self._datasets.build(
            ticker,
            prediction_date,
            training_sessions=self._config.model.training.window_jpx_sessions,
            minimum_feature_coverage=1.0 - missing_limit,
            operational=operational,
        )
        minimum_rows = self._config.model.training.minimum_complete_rows
        if len(dataset.training_frame) < minimum_rows:
            return PredictionComputation(
                dataset,
                None,
                self._insufficient(
                    ticker,
                    prediction_date,
                    dataset,
                    f"requires {minimum_rows} complete training targets",
                ),
            )
        if not dataset.feature_names:
            return PredictionComputation(
                dataset,
                None,
                self._insufficient(
                    ticker, prediction_date, dataset, "no current PIT-safe features"
                ),
            )
        missing_ratio = float(dataset.training_frame.isna().to_numpy(dtype=bool).mean())
        if missing_ratio > missing_limit:
            return PredictionComputation(
                dataset,
                None,
                self._insufficient(
                    ticker,
                    prediction_date,
                    dataset,
                    f"training feature missing ratio {missing_ratio:.1%} exceeds limit",
                ),
            )
        try:
            model = train_ticker_model(
                ticker,
                dataset.training_frame,
                dataset.training_target,
                feature_names=dataset.feature_names,
                config=self._model_config(),
            )
        except InsufficientTrainingData as exc:
            return PredictionComputation(
                dataset,
                None,
                self._insufficient(ticker, prediction_date, dataset, str(exc)),
            )
        predicted = model.predict_one(dataset.current_frame)
        training_prediction = model.predict_returns(dataset.training_frame)
        residual = dataset.training_target.to_numpy(dtype=float) - training_prediction
        residual_sigma = float(np.std(residual, ddof=1)) if len(residual) > 1 else 0.0
        interval_low = predicted.predicted_return - 1.96 * residual_sigma
        interval_high = predicted.predicted_return + 1.96 * residual_sigma
        signal_settings = self._config.trading.signal
        buy = is_buy_signal(
            predicted.predicted_return,
            predicted.probability_up,
            BuySignalConfig(
                return_threshold=signal_settings.predicted_intraday_return_threshold,
                probability_threshold=signal_settings.probability_up_threshold,
            ),
        )
        coefficients = model.regression_coefficients()
        positive, negative = _drivers(model, dataset, coefficients)
        reference = dataset.current_sample.reference_price
        difference = (
            reference * predicted.predicted_return if reference is not None else None
        )
        predicted_close = (
            reference + difference
            if reference is not None and difference is not None
            else None
        )
        confidence = calculate_confidence_score(
            predicted_return=predicted.predicted_return,
            probability_up=predicted.probability_up,
            readability_score=readability_score,
            feature_coverage=dataset.feature_coverage,
        )
        if not all(
            math.isfinite(value)
            for value in (
                predicted.predicted_return,
                predicted.probability_up,
                interval_low,
                interval_high,
            )
        ):
            return PredictionComputation(
                dataset,
                None,
                self._insufficient(
                    ticker,
                    prediction_date,
                    dataset,
                    "model returned a non-finite result",
                ),
            )
        return PredictionComputation(
            dataset,
            model,
            PredictionResult(
                ticker=ticker,
                prediction_date=prediction_date,
                status="READY",
                predicted_return=predicted.predicted_return,
                probability_up=predicted.probability_up,
                prediction_interval_low=interval_low,
                prediction_interval_high=interval_high,
                reference_price=reference,
                predicted_difference=difference,
                predicted_close=predicted_close,
                signal="BUY" if buy else "NO_BUY",
                confidence_score=confidence,
                feature_coverage=dataset.feature_coverage,
                training_sessions=predicted.training_sessions,
                ridge_alpha=predicted.ridge_alpha,
                logistic_c=predicted.logistic_c,
                coefficients=coefficients,
                positive_factors=positive,
                negative_factors=negative,
                warnings=dataset.current_sample.warnings,
            ),
        )

    def predict(
        self,
        ticker: str,
        prediction_date: date,
        *,
        readability_score: float = 0.0,
    ) -> PredictionResult:
        """Return READY or fail-closed INSUFFICIENT_DATA for one ticker."""

        return self.compute(
            ticker,
            prediction_date,
            readability_score=readability_score,
        ).result
