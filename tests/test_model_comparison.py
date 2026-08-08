from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from models import (
    ModelTrainingConfig,
    build_elastic_net_pipeline,
    build_lasso_pipeline,
    build_ols_pipeline,
    compare_regression_candidates,
)


def _linear_frame(rows: int = 80) -> tuple[pd.DataFrame, np.ndarray]:
    generator = np.random.default_rng(7)
    signal = generator.normal(size=rows)
    noise = generator.normal(scale=0.05, size=rows)
    features = pd.DataFrame(
        {
            "signal": signal,
            "irrelevant": generator.normal(size=rows),
        }
    )
    return features, 0.02 * signal + noise


def _small_config() -> ModelTrainingConfig:
    return ModelTrainingConfig(
        window_size=60,
        minimum_training_sessions=20,
        time_series_splits=3,
        ridge_alphas=(0.1, 1.0),
        elastic_net_alphas=(0.001, 0.01),
        elastic_net_l1_ratios=(0.5,),
        lasso_alphas=(0.001, 0.01),
    )


def test_elastic_net_pipeline_fits_and_exposes_coefficients() -> None:
    features, targets = _linear_frame()
    pipeline = build_elastic_net_pipeline(0.001, 0.5, random_state=42)
    pipeline.fit(features, targets)

    coefficients = pipeline.named_steps["model"].coef_
    assert len(coefficients) == 2
    assert abs(coefficients[0]) > abs(coefficients[1])


def test_lasso_and_ols_pipelines_share_the_preprocessing_contract() -> None:
    features, targets = _linear_frame()
    for pipeline in (
        build_lasso_pipeline(0.001, random_state=42),
        build_ols_pipeline(),
    ):
        pipeline.fit(features, targets)
        assert list(pipeline.named_steps) == ["imputer", "scaler", "model"]


def test_comparison_ranks_every_candidate_and_names_a_winner() -> None:
    features, targets = _linear_frame()
    comparison = compare_regression_candidates(
        features, targets, config=_small_config()
    )

    assert {score.candidate for score in comparison.scores} == {
        "ridge",
        "elastic_net",
        "ols",
        "lasso",
    }
    assert all(score.status == "OK" for score in comparison.scores)
    errors = [score.mean_squared_error for score in comparison.scores]
    assert errors == sorted(errors)
    assert comparison.best_candidate == comparison.scores[0].candidate
    assert comparison.production_candidate == "ridge"


def test_comparison_reports_rmse_consistent_with_mse() -> None:
    features, targets = _linear_frame()
    comparison = compare_regression_candidates(
        features, targets, candidates=("ridge",), config=_small_config()
    )

    score = comparison.score_for("ridge")
    assert score is not None
    assert score.root_mean_squared_error == pytest.approx(
        np.sqrt(score.mean_squared_error)
    )
    assert score.folds_evaluated == 3
    assert score.hyperparameters["alpha"] in {0.1, 1.0}


def test_comparison_refuses_to_score_an_unsplittable_sample() -> None:
    features = pd.DataFrame({"signal": [0.1, 0.2, 0.3]})
    comparison = compare_regression_candidates(
        features,
        np.array([0.01, 0.02, 0.03]),
        candidates=("ridge", "elastic_net"),
        config=_small_config(),
    )

    assert all(score.status == "NOT_EVALUATED" for score in comparison.scores)
    assert comparison.best_candidate is None


def test_comparison_rejects_unknown_candidates() -> None:
    features, targets = _linear_frame(30)
    with pytest.raises(ValueError, match="unknown regression candidates"):
        compare_regression_candidates(
            features,
            targets,
            candidates=("random_forest",),  # type: ignore[arg-type]
        )


def test_comparison_is_deterministic_across_runs() -> None:
    features, targets = _linear_frame()
    config = _small_config()
    first = compare_regression_candidates(features, targets, config=config)
    second = compare_regression_candidates(features, targets, config=config)

    assert first.as_dict() == second.as_dict()
