"""Ticker-level training and current prediction application service."""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date
from typing import Protocol

import numpy as np

from data.config import AppConfig
from models import (
    InsufficientTrainingData,
    ModelTrainingConfig,
    TickerModelBundle,
    train_ticker_model,
)
from models.arms import ArmForecast, run_arms
from models.distribution import ReturnDistribution
from scoring.confidence import calculate_confidence_score
from services.dataset import ModelDataset
from trading.strategy import BuySignalConfig, is_buy_signal


class DatasetSource(Protocol):
    """Whatever can produce one ticker's point-in-time window.

    A protocol rather than the concrete builder so a caller that has already
    read its windows can pass a stand-in that refuses to touch a database. The
    fitting needs no connection, and typing it against the real builder would
    force a session into places that must not have one.
    """

    def build(
        self,
        ticker: str,
        prediction_date: date,
        *,
        training_sessions: int,
        minimum_feature_coverage: float,
        operational: bool,
    ) -> ModelDataset:
        """Return the window ending before ``prediction_date``."""


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
    # The nominal coverage the two bounds above claim. Recorded because it
    # changed: the bounds used to be a 95% normal band around the point
    # forecast, and are now the 5th and 95th percentiles of the fitted
    # distribution, which is a 90% band and a different construction.
    prediction_interval_coverage: float | None = None
    # The whole forecast distribution. Everything above that describes
    # spread is read off this; the point fields remain because the trading
    # rule and the entire scored history are defined against them.
    distribution: ReturnDistribution | None = None
    # Every other model family's answer for the same row. Recorded, never
    # consulted by the trading rule: the point fields above still decide.
    arm_forecasts: tuple[ArmForecast, ...] = field(default_factory=tuple)
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
        dataset_builder: DatasetSource,
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
            distribution_quantiles=tuple(model.hyperparameters.quantile_levels),
            quantile_alphas=tuple(model.hyperparameters.quantile_alpha),
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

    def build_dataset(
        self,
        ticker: str,
        prediction_date: date,
        *,
        operational: bool = True,
    ) -> ModelDataset:
        """Read one ticker's point-in-time window. The only part that touches the DB.

        Separated from the fitting so a caller can do all the reading on one
        session and then fit several tickers at once. A SQLAlchemy session is
        not safe to share between workers, and the fitting needs no database at
        all, so the split is what makes the morning parallelisable without
        putting a connection anywhere near a worker.
        """

        missing_limit = self._config.model.features.max_missing_ratio
        if missing_limit is None:
            raise ValueError("feature missing threshold is not confirmed")
        return self._datasets.build(
            ticker,
            prediction_date,
            training_sessions=self._config.model.training.window_jpx_sessions,
            minimum_feature_coverage=1.0 - missing_limit,
            operational=operational,
        )

    def compute(
        self,
        ticker: str,
        prediction_date: date,
        *,
        readability_score: float = 0.0,
        operational: bool = True,
        dataset: ModelDataset | None = None,
    ) -> PredictionComputation:
        """Compute one result while retaining reproducibility artifacts.

        ``dataset`` lets a caller supply a window it has already read, so this
        method performs no I/O and can run in a worker process.
        """

        missing_limit = self._config.model.features.max_missing_ratio
        if missing_limit is None:
            raise ValueError("feature missing threshold is not confirmed")
        if dataset is None:
            dataset = self.build_dataset(
                ticker, prediction_date, operational=operational
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
        distribution = predicted.distribution
        # The interval now comes off the fitted quantile curve when there is
        # one. The old construction -- 1.96 standard deviations of the training
        # residuals -- assumed the errors were normal and used the very rows
        # the fit had already minimised against, so it was both the wrong shape
        # for a return distribution and narrower than the outcomes. It stays as
        # the fallback for a ticker whose quantile fit could not be made, and
        # the coverage it claims is recorded either way so the two are never
        # read as the same number.
        bounds = None if distribution is None else distribution.interval(0.90)
        if bounds is not None:
            interval_low, interval_high = bounds
            interval_coverage = 0.90
        else:
            training_prediction = model.predict_returns(dataset.training_frame)
            residual = (
                dataset.training_target.to_numpy(dtype=float) - training_prediction
            )
            residual_sigma = (
                float(np.std(residual, ddof=1)) if len(residual) > 1 else 0.0
            )
            interval_low = predicted.predicted_return - 1.96 * residual_sigma
            interval_high = predicted.predicted_return + 1.96 * residual_sigma
            interval_coverage = 0.95
        signal_settings = self._config.trading.signal
        buy = is_buy_signal(
            predicted.predicted_return,
            predicted.probability_up,
            BuySignalConfig(
                return_threshold=signal_settings.predicted_intraday_return_threshold,
                probability_threshold=signal_settings.probability_up_threshold,
            ),
        )
        # Every family, on exactly the rows the production model just used, so
        # the comparison is like-for-like. Failures are collected as results
        # rather than raised: a family that cannot fit is a fact to report, not
        # a reason to lose the morning.
        arm_forecasts: tuple[ArmForecast, ...] = ()
        if self._config.model.models.run_all_arms:
            arm_forecasts = run_arms(
                dataset.training_frame.loc[:, list(dataset.feature_names)],
                dataset.training_target.to_numpy(dtype=float),
                dataset.current_frame.loc[:, list(dataset.feature_names)],
                levels=tuple(self._config.model.hyperparameters.quantile_levels),
                n_splits=self._config.model.cross_validation.n_splits,
                include_sequence=self._config.model.models.include_sequence_arms,
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
                prediction_interval_coverage=interval_coverage,
                distribution=distribution,
                arm_forecasts=arm_forecasts,
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
