"""Create the point-in-time prediction, evaluation, and delivery schema.

Revision ID: 0003_prediction_pipeline
Revises: 0002_free_provider
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0003_prediction_pipeline"
down_revision: str | None = "0002_free_provider"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "run_steps",
        sa.Column("step_id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("run_id", sa.String(length=36), nullable=False),
        sa.Column("step_name", sa.String(length=64), nullable=False),
        sa.Column("attempt_number", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("details", sa.JSON(), nullable=False),
        sa.CheckConstraint("attempt_number > 0", name="ck_run_steps_attempt_positive"),
        sa.CheckConstraint(
            "status IN ('RUNNING', 'SUCCESS', 'FAILED', 'SKIPPED')",
            name="ck_run_steps_status",
        ),
        sa.CheckConstraint(
            "finished_at IS NULL OR finished_at >= started_at",
            name="ck_run_steps_time_order",
        ),
        sa.ForeignKeyConstraint(["run_id"], ["daily_runs.run_id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("step_id"),
        sa.UniqueConstraint(
            "run_id", "step_name", "attempt_number", name="uq_run_steps_attempt"
        ),
    )
    op.create_index("ix_run_steps_run_status", "run_steps", ["run_id", "status"])

    op.create_table(
        "feature_sets",
        sa.Column("feature_set_id", sa.String(length=36), nullable=False),
        sa.Column("run_id", sa.String(length=36), nullable=False),
        sa.Column("ticker", sa.String(length=16), nullable=False),
        sa.Column("prediction_date", sa.Date(), nullable=False),
        sa.Column("cutoff_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("feature_version", sa.String(length=64), nullable=False),
        sa.Column("set_kind", sa.String(length=24), nullable=False),
        sa.Column("training_start", sa.Date(), nullable=False),
        sa.Column("training_end", sa.Date(), nullable=False),
        sa.Column("config_hash", sa.String(length=64), nullable=False),
        sa.Column("input_manifest_hash", sa.String(length=64), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("required_feature_count", sa.Integer(), nullable=False),
        sa.Column("missing_feature_count", sa.Integer(), nullable=False),
        sa.Column("missing_ratio", sa.Float(), nullable=False),
        sa.Column("max_available_timestamp", sa.DateTime(timezone=True), nullable=True),
        sa.Column("max_first_observed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("max_retrieved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finalized_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("details", sa.JSON(), nullable=False),
        sa.Column("idempotency_key", sa.String(length=128), nullable=False),
        sa.CheckConstraint(
            "status IN ('BUILDING', 'READY', 'INSUFFICIENT_DATA', 'FAILED')",
            name="ck_feature_sets_status",
        ),
        sa.CheckConstraint(
            "set_kind IN ('MORNING', 'WALK_FORWARD')",
            name="ck_feature_sets_kind",
        ),
        sa.CheckConstraint(
            "training_start <= training_end",
            name="ck_feature_sets_training_dates",
        ),
        sa.CheckConstraint(
            "training_end < prediction_date",
            name="ck_feature_sets_training_before_prediction",
        ),
        sa.CheckConstraint(
            "required_feature_count >= 0 AND missing_feature_count >= 0 "
            "AND missing_feature_count <= required_feature_count",
            name="ck_feature_sets_counts",
        ),
        sa.CheckConstraint(
            "missing_ratio >= 0 AND missing_ratio <= 1",
            name="ck_feature_sets_missing_ratio",
        ),
        sa.CheckConstraint(
            "max_available_timestamp IS NULL OR max_available_timestamp <= cutoff_at",
            name="ck_feature_sets_available_cutoff",
        ),
        sa.CheckConstraint(
            "max_first_observed_at IS NULL OR max_first_observed_at <= cutoff_at",
            name="ck_feature_sets_observed_cutoff",
        ),
        sa.CheckConstraint(
            "max_retrieved_at IS NULL OR max_retrieved_at <= cutoff_at",
            name="ck_feature_sets_retrieved_cutoff",
        ),
        sa.CheckConstraint(
            "finalized_at IS NULL OR finalized_at >= created_at",
            name="ck_feature_sets_time_order",
        ),
        sa.ForeignKeyConstraint(["run_id"], ["daily_runs.run_id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("feature_set_id"),
        sa.UniqueConstraint(
            "run_id",
            "ticker",
            "feature_version",
            "set_kind",
            name="uq_feature_sets_run_ticker_version",
        ),
        sa.UniqueConstraint("idempotency_key", name="uq_feature_sets_idempotency"),
    )
    op.create_index(
        "ix_feature_sets_date_ticker",
        "feature_sets",
        ["prediction_date", "ticker"],
    )

    op.create_table(
        "feature_values",
        sa.Column("feature_value_id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("feature_set_id", sa.String(length=36), nullable=False),
        sa.Column("sample_date", sa.Date(), nullable=False),
        sa.Column("sample_cutoff_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("row_role", sa.String(length=16), nullable=False),
        sa.Column("value_kind", sa.String(length=16), nullable=False),
        sa.Column("feature_name", sa.String(length=128), nullable=False),
        sa.Column("value", sa.Numeric(precision=30, scale=12), nullable=True),
        sa.Column("is_missing", sa.Boolean(), nullable=False),
        sa.Column("available_timestamp", sa.DateTime(timezone=True), nullable=True),
        sa.Column("data_quality", sa.String(length=32), nullable=True),
        sa.Column("details", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "row_role IN ('TRAIN', 'SCORE')", name="ck_feature_values_row_role"
        ),
        sa.CheckConstraint(
            "value_kind IN ('FEATURE', 'TARGET')",
            name="ck_feature_values_value_kind",
        ),
        sa.CheckConstraint(
            "(is_missing AND value IS NULL) OR (NOT is_missing AND value IS NOT NULL)",
            name="ck_feature_values_missing_value",
        ),
        sa.CheckConstraint(
            "available_timestamp IS NULL OR available_timestamp <= sample_cutoff_at",
            name="ck_feature_values_available_cutoff",
        ),
        sa.ForeignKeyConstraint(
            ["feature_set_id"],
            ["feature_sets.feature_set_id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("feature_value_id"),
        sa.UniqueConstraint(
            "feature_set_id",
            "sample_date",
            "row_role",
            "value_kind",
            "feature_name",
            name="uq_feature_values_name",
        ),
    )

    op.create_table(
        "feature_inputs",
        sa.Column("feature_input_id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("feature_value_id", sa.Integer(), nullable=False),
        sa.Column("input_role", sa.String(length=64), nullable=False),
        sa.Column("source_type", sa.String(length=16), nullable=False),
        sa.Column("source_row_id", sa.Integer(), nullable=False),
        sa.Column("market_data_id", sa.Integer(), nullable=True),
        sa.Column("stock_price_id", sa.Integer(), nullable=True),
        sa.Column("raw_hash", sa.String(length=64), nullable=False),
        sa.Column("available_timestamp", sa.DateTime(timezone=True), nullable=False),
        sa.Column("first_observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("retrieved_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "source_type IN ('MARKET_DATA', 'STOCK_PRICE')",
            name="ck_feature_inputs_source_type",
        ),
        sa.CheckConstraint(
            "(source_type = 'MARKET_DATA' AND market_data_id IS NOT NULL "
            "AND stock_price_id IS NULL AND source_row_id = market_data_id) OR "
            "(source_type = 'STOCK_PRICE' AND stock_price_id IS NOT NULL "
            "AND market_data_id IS NULL AND source_row_id = stock_price_id)",
            name="ck_feature_inputs_exact_source",
        ),
        sa.CheckConstraint(
            "available_timestamp <= first_observed_at",
            name="ck_feature_inputs_available_observed",
        ),
        sa.CheckConstraint(
            "first_observed_at <= retrieved_at",
            name="ck_feature_inputs_observed_retrieved",
        ),
        sa.ForeignKeyConstraint(
            ["feature_value_id"],
            ["feature_values.feature_value_id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(["market_data_id"], ["market_data.id"]),
        sa.ForeignKeyConstraint(["stock_price_id"], ["stock_prices.id"]),
        sa.PrimaryKeyConstraint("feature_input_id"),
        sa.UniqueConstraint(
            "feature_value_id",
            "input_role",
            "source_type",
            "source_row_id",
            name="uq_feature_inputs_source",
        ),
    )
    op.create_index(
        "ix_feature_inputs_feature_value",
        "feature_inputs",
        ["feature_value_id"],
    )

    op.create_table(
        "model_runs",
        sa.Column("model_run_id", sa.String(length=36), nullable=False),
        sa.Column("run_id", sa.String(length=36), nullable=False),
        sa.Column("ticker", sa.String(length=16), nullable=False),
        sa.Column("feature_set_id", sa.String(length=36), nullable=False),
        sa.Column("task", sa.String(length=24), nullable=False),
        sa.Column("algorithm", sa.String(length=32), nullable=False),
        sa.Column("training_start", sa.Date(), nullable=False),
        sa.Column("training_end", sa.Date(), nullable=False),
        sa.Column("cutoff_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("training_rows", sa.Integer(), nullable=False),
        sa.Column("feature_version", sa.String(length=64), nullable=False),
        sa.Column("model_version", sa.String(length=64), nullable=False),
        sa.Column("random_seed", sa.Integer(), nullable=False),
        sa.Column("parameters", sa.JSON(), nullable=False),
        sa.Column("cv_results", sa.JSON(), nullable=False),
        sa.Column("intercept", sa.Numeric(precision=30, scale=12), nullable=True),
        sa.Column("artifact_uri", sa.String(length=512), nullable=True),
        sa.Column("artifact_hash", sa.String(length=64), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("idempotency_key", sa.String(length=128), nullable=False),
        sa.CheckConstraint(
            "task IN ('REGRESSION', 'CLASSIFICATION')", name="ck_model_runs_task"
        ),
        sa.CheckConstraint(
            "status IN ('RUNNING', 'SUCCESS', 'FAILED')",
            name="ck_model_runs_status",
        ),
        sa.CheckConstraint("training_rows > 0", name="ck_model_runs_training_rows"),
        sa.CheckConstraint(
            "training_start <= training_end", name="ck_model_runs_training_dates"
        ),
        sa.CheckConstraint(
            "finished_at IS NULL OR finished_at >= started_at",
            name="ck_model_runs_time_order",
        ),
        sa.ForeignKeyConstraint(["run_id"], ["daily_runs.run_id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["feature_set_id"], ["feature_sets.feature_set_id"]),
        sa.PrimaryKeyConstraint("model_run_id"),
        sa.UniqueConstraint(
            "run_id", "ticker", "task", "algorithm", name="uq_model_runs_identity"
        ),
        sa.UniqueConstraint("idempotency_key", name="uq_model_runs_idempotency"),
    )
    op.create_index("ix_model_runs_ticker_status", "model_runs", ["ticker", "status"])

    op.create_table(
        "model_coefficients",
        sa.Column("coefficient_id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("model_run_id", sa.String(length=36), nullable=False),
        sa.Column("feature_name", sa.String(length=128), nullable=False),
        sa.Column("coefficient", sa.Numeric(precision=30, scale=12), nullable=False),
        sa.Column("scaler_mean", sa.Numeric(precision=30, scale=12), nullable=True),
        sa.Column("scaler_scale", sa.Numeric(precision=30, scale=12), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "scaler_scale IS NULL OR scaler_scale > 0",
            name="ck_model_coefficients_scaler_scale",
        ),
        sa.ForeignKeyConstraint(
            ["model_run_id"], ["model_runs.model_run_id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("coefficient_id"),
        sa.UniqueConstraint(
            "model_run_id", "feature_name", name="uq_model_coefficients_feature"
        ),
    )

    op.create_table(
        "prediction_sets",
        sa.Column("prediction_set_id", sa.String(length=36), nullable=False),
        sa.Column("run_id", sa.String(length=36), nullable=False),
        sa.Column("prediction_date", sa.Date(), nullable=False),
        sa.Column("cutoff_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("feature_version", sa.String(length=64), nullable=False),
        sa.Column("model_version", sa.String(length=64), nullable=False),
        sa.Column("strategy_version", sa.String(length=64), nullable=False),
        sa.Column("training_start", sa.Date(), nullable=False),
        sa.Column("training_end", sa.Date(), nullable=False),
        sa.Column("generated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("warnings", sa.JSON(), nullable=False),
        sa.Column("idempotency_key", sa.String(length=128), nullable=False),
        sa.CheckConstraint(
            "status IN ('BUILDING', 'READY', 'INSUFFICIENT_DATA', 'FAILED')",
            name="ck_prediction_sets_status",
        ),
        sa.CheckConstraint(
            "training_start <= training_end",
            name="ck_prediction_sets_training_dates",
        ),
        sa.CheckConstraint(
            "training_end < prediction_date",
            name="ck_prediction_sets_training_before_prediction",
        ),
        sa.CheckConstraint(
            "published_at IS NULL OR published_at >= generated_at",
            name="ck_prediction_sets_time_order",
        ),
        sa.ForeignKeyConstraint(["run_id"], ["daily_runs.run_id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("prediction_set_id"),
        sa.UniqueConstraint("run_id", name="uq_prediction_sets_run"),
        sa.UniqueConstraint("idempotency_key", name="uq_prediction_sets_idempotency"),
    )
    op.create_index(
        "ix_prediction_sets_date_status",
        "prediction_sets",
        ["prediction_date", "status"],
    )

    op.create_table(
        "predictions",
        sa.Column("prediction_id", sa.String(length=36), nullable=False),
        sa.Column("prediction_set_id", sa.String(length=36), nullable=False),
        sa.Column("ticker", sa.String(length=16), nullable=False),
        sa.Column("feature_set_id", sa.String(length=36), nullable=False),
        sa.Column("reference_stock_price_id", sa.Integer(), nullable=True),
        sa.Column("regression_model_run_id", sa.String(length=36), nullable=True),
        sa.Column("classification_model_run_id", sa.String(length=36), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column(
            "predicted_intraday_return",
            sa.Numeric(precision=30, scale=12),
            nullable=True,
        ),
        sa.Column(
            "prediction_interval_low",
            sa.Numeric(precision=30, scale=12),
            nullable=True,
        ),
        sa.Column(
            "prediction_interval_high",
            sa.Numeric(precision=30, scale=12),
            nullable=True,
        ),
        sa.Column("probability_up", sa.Numeric(precision=18, scale=12), nullable=True),
        sa.Column("reference_price", sa.Numeric(precision=24, scale=10), nullable=True),
        sa.Column("reference_basis", sa.String(length=32), nullable=False),
        sa.Column(
            "predicted_price_difference",
            sa.Numeric(precision=24, scale=10),
            nullable=True,
        ),
        sa.Column("predicted_close", sa.Numeric(precision=24, scale=10), nullable=True),
        sa.Column("signal", sa.String(length=16), nullable=False),
        sa.Column("rank", sa.Integer(), nullable=True),
        sa.Column(
            "return_threshold", sa.Numeric(precision=18, scale=12), nullable=False
        ),
        sa.Column(
            "probability_threshold",
            sa.Numeric(precision=18, scale=12),
            nullable=False,
        ),
        sa.Column("confidence_score", sa.Numeric(precision=8, scale=4), nullable=True),
        sa.Column("positive_factors", sa.JSON(), nullable=False),
        sa.Column("negative_factors", sa.JSON(), nullable=False),
        sa.Column("feature_coverage", sa.Float(), nullable=True),
        sa.Column("warnings", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("idempotency_key", sa.String(length=128), nullable=False),
        sa.CheckConstraint(
            "status IN ('SUCCESS', 'INSUFFICIENT_DATA', 'FAILED')",
            name="ck_predictions_status",
        ),
        sa.CheckConstraint(
            "signal IN ('BUY', 'NO_BUY', 'NONE')", name="ck_predictions_signal"
        ),
        sa.CheckConstraint(
            "probability_up IS NULL OR (probability_up >= 0 AND probability_up <= 1)",
            name="ck_predictions_probability",
        ),
        sa.CheckConstraint(
            "confidence_score IS NULL OR "
            "(confidence_score >= 0 AND confidence_score <= 100)",
            name="ck_predictions_confidence",
        ),
        sa.CheckConstraint(
            "(prediction_interval_low IS NULL AND prediction_interval_high IS NULL) "
            "OR (prediction_interval_low IS NOT NULL "
            "AND prediction_interval_high IS NOT NULL "
            "AND prediction_interval_low <= prediction_interval_high)",
            name="ck_predictions_interval",
        ),
        sa.CheckConstraint(
            "feature_coverage IS NULL OR "
            "(feature_coverage >= 0 AND feature_coverage <= 1)",
            name="ck_predictions_feature_coverage",
        ),
        sa.CheckConstraint(
            "reference_price IS NULL OR reference_price > 0",
            name="ck_predictions_reference_price",
        ),
        sa.CheckConstraint("rank IS NULL OR rank > 0", name="ck_predictions_rank"),
        sa.CheckConstraint(
            "status != 'SUCCESS' OR "
            "(regression_model_run_id IS NOT NULL "
            "AND classification_model_run_id IS NOT NULL "
            "AND predicted_intraday_return IS NOT NULL "
            "AND probability_up IS NOT NULL)",
            name="ck_predictions_success_values",
        ),
        sa.ForeignKeyConstraint(
            ["prediction_set_id"],
            ["prediction_sets.prediction_set_id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(["feature_set_id"], ["feature_sets.feature_set_id"]),
        sa.ForeignKeyConstraint(["reference_stock_price_id"], ["stock_prices.id"]),
        sa.ForeignKeyConstraint(
            ["regression_model_run_id"], ["model_runs.model_run_id"]
        ),
        sa.ForeignKeyConstraint(
            ["classification_model_run_id"], ["model_runs.model_run_id"]
        ),
        sa.PrimaryKeyConstraint("prediction_id"),
        sa.UniqueConstraint(
            "prediction_set_id", "ticker", name="uq_predictions_set_ticker"
        ),
        sa.UniqueConstraint("idempotency_key", name="uq_predictions_idempotency"),
    )
    op.create_index(
        "ix_predictions_ticker_created", "predictions", ["ticker", "created_at"]
    )

    op.create_table(
        "actual_results",
        sa.Column("actual_result_id", sa.String(length=36), nullable=False),
        sa.Column("prediction_id", sa.String(length=36), nullable=False),
        sa.Column("stock_price_id", sa.Integer(), nullable=True),
        sa.Column("supersedes_actual_result_id", sa.String(length=36), nullable=True),
        sa.Column("result_version", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("actual_open", sa.Numeric(precision=24, scale=10), nullable=True),
        sa.Column("actual_close", sa.Numeric(precision=24, scale=10), nullable=True),
        sa.Column(
            "actual_intraday_return",
            sa.Numeric(precision=30, scale=12),
            nullable=True,
        ),
        sa.Column(
            "actual_price_difference",
            sa.Numeric(precision=24, scale=10),
            nullable=True,
        ),
        sa.Column("raw_hash", sa.String(length=64), nullable=True),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finalized_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("idempotency_key", sa.String(length=128), nullable=False),
        sa.CheckConstraint("result_version > 0", name="ck_actual_results_version"),
        sa.CheckConstraint(
            "status IN ('PENDING', 'FINAL', 'CORRECTED')",
            name="ck_actual_results_status",
        ),
        sa.CheckConstraint(
            "actual_open IS NULL OR actual_open > 0",
            name="ck_actual_results_open",
        ),
        sa.CheckConstraint(
            "actual_close IS NULL OR actual_close > 0",
            name="ck_actual_results_close",
        ),
        sa.CheckConstraint(
            "status = 'PENDING' OR "
            "(actual_open IS NOT NULL AND actual_close IS NOT NULL "
            "AND actual_intraday_return IS NOT NULL "
            "AND actual_price_difference IS NOT NULL "
            "AND finalized_at IS NOT NULL)",
            name="ck_actual_results_final_values",
        ),
        sa.ForeignKeyConstraint(
            ["prediction_id"], ["predictions.prediction_id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["stock_price_id"], ["stock_prices.id"]),
        sa.ForeignKeyConstraint(
            ["supersedes_actual_result_id"], ["actual_results.actual_result_id"]
        ),
        sa.PrimaryKeyConstraint("actual_result_id"),
        sa.UniqueConstraint(
            "prediction_id", "result_version", name="uq_actual_results_version"
        ),
        sa.UniqueConstraint(
            "supersedes_actual_result_id", name="uq_actual_results_supersedes"
        ),
        sa.UniqueConstraint("idempotency_key", name="uq_actual_results_idempotency"),
    )
    op.create_index(
        "ix_actual_results_prediction",
        "actual_results",
        ["prediction_id", "result_version"],
    )

    op.create_table(
        "simulated_trades",
        sa.Column("trade_id", sa.String(length=36), nullable=False),
        sa.Column("prediction_id", sa.String(length=36), nullable=False),
        sa.Column("actual_result_id", sa.String(length=36), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("is_simulated", sa.Boolean(), nullable=False),
        sa.Column("capital_jpy", sa.Numeric(precision=24, scale=4), nullable=False),
        sa.Column("shares", sa.Integer(), nullable=False),
        sa.Column("entry_price", sa.Numeric(precision=24, scale=10), nullable=True),
        sa.Column("exit_price", sa.Numeric(precision=24, scale=10), nullable=True),
        sa.Column("gross_profit_jpy", sa.Numeric(precision=24, scale=4), nullable=True),
        sa.Column(
            "commission_cost_jpy", sa.Numeric(precision=24, scale=4), nullable=True
        ),
        sa.Column(
            "slippage_cost_jpy", sa.Numeric(precision=24, scale=4), nullable=True
        ),
        sa.Column("net_profit_jpy", sa.Numeric(precision=24, scale=4), nullable=True),
        sa.Column("realized_return", sa.Numeric(precision=30, scale=12), nullable=True),
        sa.Column("opened_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("strategy_version", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("idempotency_key", sa.String(length=128), nullable=False),
        sa.CheckConstraint(
            "status IN ('NOT_TRIGGERED', 'PENDING', 'FINAL', 'INSUFFICIENT_CONFIG')",
            name="ck_simulated_trades_status",
        ),
        sa.CheckConstraint("is_simulated", name="ck_simulated_trades_paper_only"),
        sa.CheckConstraint("capital_jpy > 0", name="ck_simulated_trades_capital"),
        sa.CheckConstraint("shares >= 0", name="ck_simulated_trades_shares"),
        sa.CheckConstraint(
            "commission_cost_jpy IS NULL OR commission_cost_jpy >= 0",
            name="ck_simulated_trades_commission",
        ),
        sa.CheckConstraint(
            "slippage_cost_jpy IS NULL OR slippage_cost_jpy >= 0",
            name="ck_simulated_trades_slippage",
        ),
        sa.CheckConstraint(
            "closed_at IS NULL OR opened_at IS NULL OR closed_at >= opened_at",
            name="ck_simulated_trades_time_order",
        ),
        sa.ForeignKeyConstraint(
            ["prediction_id"], ["predictions.prediction_id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["actual_result_id"], ["actual_results.actual_result_id"]
        ),
        sa.PrimaryKeyConstraint("trade_id"),
        sa.UniqueConstraint(
            "prediction_id",
            "actual_result_id",
            "strategy_version",
            name="uq_simulated_trades_valuation",
        ),
        sa.UniqueConstraint("idempotency_key", name="uq_simulated_trades_idempotency"),
    )
    op.create_index("ix_simulated_trades_status", "simulated_trades", ["status"])

    op.create_table(
        "metric_snapshots",
        sa.Column("metric_snapshot_id", sa.String(length=36), nullable=False),
        sa.Column("ticker", sa.String(length=16), nullable=False),
        sa.Column("as_of_date", sa.Date(), nullable=False),
        sa.Column("model_version", sa.String(length=64), nullable=False),
        sa.Column("strategy_version", sa.String(length=64), nullable=False),
        sa.Column("evaluation_window", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("sample_status", sa.String(length=32), nullable=False),
        sa.Column("prediction_count", sa.Integer(), nullable=False),
        sa.Column("trade_count", sa.Integer(), nullable=False),
        sa.Column("win_count", sa.Integer(), nullable=False),
        sa.Column("loss_count", sa.Integer(), nullable=False),
        sa.Column("win_rate", sa.Numeric(precision=18, scale=12), nullable=True),
        sa.Column("gross_profit_jpy", sa.Numeric(precision=24, scale=4), nullable=True),
        sa.Column("gross_loss_jpy", sa.Numeric(precision=24, scale=4), nullable=True),
        sa.Column("net_profit_jpy", sa.Numeric(precision=24, scale=4), nullable=True),
        sa.Column("average_win_jpy", sa.Numeric(precision=24, scale=4), nullable=True),
        sa.Column("average_loss_jpy", sa.Numeric(precision=24, scale=4), nullable=True),
        sa.Column("largest_win_jpy", sa.Numeric(precision=24, scale=4), nullable=True),
        sa.Column("largest_loss_jpy", sa.Numeric(precision=24, scale=4), nullable=True),
        sa.Column("payoff_ratio", sa.Numeric(precision=30, scale=12), nullable=True),
        sa.Column("profit_factor", sa.Numeric(precision=30, scale=12), nullable=True),
        sa.Column("expectancy_jpy", sa.Numeric(precision=24, scale=4), nullable=True),
        sa.Column("sharpe_ratio", sa.Numeric(precision=30, scale=12), nullable=True),
        sa.Column("sortino_ratio", sa.Numeric(precision=30, scale=12), nullable=True),
        sa.Column("max_drawdown", sa.Numeric(precision=30, scale=12), nullable=True),
        sa.Column(
            "pearson_correlation",
            sa.Numeric(precision=30, scale=12),
            nullable=True,
        ),
        sa.Column(
            "spearman_correlation",
            sa.Numeric(precision=30, scale=12),
            nullable=True,
        ),
        sa.Column(
            "direction_accuracy", sa.Numeric(precision=18, scale=12), nullable=True
        ),
        sa.Column("readability_score", sa.Numeric(precision=8, scale=4), nullable=True),
        sa.Column("input_manifest_hash", sa.String(length=64), nullable=False),
        sa.Column("details", sa.JSON(), nullable=False),
        sa.Column("computed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("idempotency_key", sa.String(length=128), nullable=False),
        sa.CheckConstraint(
            "status IN ('READY', 'INSUFFICIENT_DATA', 'FAILED')",
            name="ck_metric_snapshots_status",
        ),
        sa.CheckConstraint(
            "sample_status IN ('NO_TRADES', 'LOW_SAMPLE', 'SUFFICIENT')",
            name="ck_metric_snapshots_sample_status",
        ),
        sa.CheckConstraint(
            "prediction_count >= 0 AND trade_count >= 0 AND win_count >= 0 "
            "AND loss_count >= 0 AND win_count + loss_count <= trade_count",
            name="ck_metric_snapshots_counts",
        ),
        sa.CheckConstraint(
            "win_rate IS NULL OR (win_rate >= 0 AND win_rate <= 1)",
            name="ck_metric_snapshots_win_rate",
        ),
        sa.CheckConstraint(
            "direction_accuracy IS NULL OR "
            "(direction_accuracy >= 0 AND direction_accuracy <= 1)",
            name="ck_metric_snapshots_direction_accuracy",
        ),
        sa.CheckConstraint(
            "readability_score IS NULL OR "
            "(readability_score >= 0 AND readability_score <= 100)",
            name="ck_metric_snapshots_readability",
        ),
        sa.PrimaryKeyConstraint("metric_snapshot_id"),
        sa.UniqueConstraint(
            "ticker",
            "as_of_date",
            "model_version",
            "strategy_version",
            "evaluation_window",
            name="uq_metric_snapshots_identity",
        ),
        sa.UniqueConstraint("idempotency_key", name="uq_metric_snapshots_idempotency"),
    )
    op.create_index(
        "ix_metric_snapshots_date_ticker",
        "metric_snapshots",
        ["as_of_date", "ticker"],
    )

    op.create_table(
        "email_logs",
        sa.Column("email_log_id", sa.String(length=36), nullable=False),
        sa.Column("prediction_set_id", sa.String(length=36), nullable=False),
        sa.Column("recipient", sa.String(length=320), nullable=False),
        sa.Column("template_version", sa.String(length=64), nullable=False),
        sa.Column("subject", sa.String(length=255), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("provider_message_id", sa.String(length=255), nullable=True),
        sa.Column("attempt_count", sa.Integer(), nullable=False),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("idempotency_key", sa.String(length=128), nullable=False),
        sa.CheckConstraint(
            "status IN ('PENDING', 'SENDING', 'SENT', 'FAILED')",
            name="ck_email_logs_status",
        ),
        sa.CheckConstraint("attempt_count >= 0", name="ck_email_logs_attempt_count"),
        sa.CheckConstraint(
            "sent_at IS NULL OR sent_at >= created_at",
            name="ck_email_logs_time_order",
        ),
        sa.ForeignKeyConstraint(
            ["prediction_set_id"],
            ["prediction_sets.prediction_set_id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("email_log_id"),
        sa.UniqueConstraint(
            "prediction_set_id",
            "recipient",
            "template_version",
            name="uq_email_logs_delivery",
        ),
        sa.UniqueConstraint("idempotency_key", name="uq_email_logs_idempotency"),
        sa.UniqueConstraint(
            "provider_message_id", name="uq_email_logs_provider_message"
        ),
    )
    op.create_index(
        "ix_email_logs_status_created", "email_logs", ["status", "created_at"]
    )


def downgrade() -> None:
    op.drop_index("ix_email_logs_status_created", table_name="email_logs")
    op.drop_table("email_logs")
    op.drop_index("ix_metric_snapshots_date_ticker", table_name="metric_snapshots")
    op.drop_table("metric_snapshots")
    op.drop_index("ix_simulated_trades_status", table_name="simulated_trades")
    op.drop_table("simulated_trades")
    op.drop_index("ix_actual_results_prediction", table_name="actual_results")
    op.drop_table("actual_results")
    op.drop_index("ix_predictions_ticker_created", table_name="predictions")
    op.drop_table("predictions")
    op.drop_index("ix_prediction_sets_date_status", table_name="prediction_sets")
    op.drop_table("prediction_sets")
    op.drop_table("model_coefficients")
    op.drop_index("ix_model_runs_ticker_status", table_name="model_runs")
    op.drop_table("model_runs")
    op.drop_index("ix_feature_inputs_feature_value", table_name="feature_inputs")
    op.drop_table("feature_inputs")
    op.drop_table("feature_values")
    op.drop_index("ix_feature_sets_date_ticker", table_name="feature_sets")
    op.drop_table("feature_sets")
    op.drop_index("ix_run_steps_run_status", table_name="run_steps")
    op.drop_table("run_steps")
