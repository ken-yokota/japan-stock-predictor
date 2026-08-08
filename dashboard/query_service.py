"""SQLAlchemy-backed, strictly read-only dashboard query service.

The service intentionally uses SQL text instead of importing pipeline models.
This keeps the UI independent from providers and training code, and lets a
deployment show a safe ``SCHEMA_PENDING`` state while migrations are rolling
out.  No database exception text is returned to callers because connection
errors may contain credentials.
"""

from __future__ import annotations

from collections.abc import Mapping

from sqlalchemy import Engine, inspect, text
from sqlalchemy.exc import SQLAlchemyError

from dashboard.types import QueryResult, QueryState

_Columns = Mapping[str, frozenset[str]]


class DashboardQueryService:
    """Execute allow-listed SELECT statements against persisted results only."""

    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    @property
    def dialect_name(self) -> str:
        return self._engine.dialect.name

    def database_health(self) -> QueryResult:
        """Prove that a read transaction can be opened without exposing its URL."""

        try:
            with self._engine.connect() as connection:
                if self.dialect_name == "postgresql":
                    connection.execute(text("SET TRANSACTION READ ONLY"))
                value = connection.scalar(text("SELECT 1"))
        except SQLAlchemyError:
            return QueryResult.unavailable()
        if value != 1:
            return QueryResult.unavailable()
        return QueryResult(
            QueryState.READY,
            rows=({"database": "CONNECTED_READ_ONLY"},),
        )

    def _read(
        self,
        *,
        required: _Columns,
        statement: str,
        parameters: Mapping[str, object] | None = None,
    ) -> QueryResult:
        try:
            inspector = inspect(self._engine)
            existing_tables = set(inspector.get_table_names())
            missing: list[str] = []
            for table_name, required_columns in required.items():
                if table_name not in existing_tables:
                    missing.append(table_name)
                    continue
                present_columns = {
                    str(column["name"]) for column in inspector.get_columns(table_name)
                }
                if not required_columns <= present_columns:
                    missing.append(table_name)
            if missing:
                return QueryResult.schema_pending(tuple(sorted(missing)))

            with self._engine.connect() as connection:
                if self.dialect_name == "postgresql":
                    connection.execute(text("SET TRANSACTION READ ONLY"))
                mappings = connection.execute(
                    text(statement), dict(parameters or {})
                ).mappings()
                rows = tuple(dict(row) for row in mappings)
        except SQLAlchemyError:
            return QueryResult.unavailable()
        return QueryResult.from_rows(rows)

    def latest_run(self) -> QueryResult:
        return self._read(
            required={
                "daily_runs": frozenset(
                    {
                        "run_id",
                        "run_type",
                        "prediction_date",
                        "cutoff_at",
                        "started_at",
                        "finished_at",
                        "status",
                        "current_step",
                        "data_version",
                        "model_version",
                        "failed_symbols",
                    }
                )
            },
            statement="""
                SELECT
                    run_id, run_type, prediction_date, cutoff_at, started_at,
                    finished_at, status, current_step, data_version,
                    model_version, failed_symbols
                FROM daily_runs
                ORDER BY prediction_date DESC, started_at DESC
                LIMIT 1
            """,
        )

    def latest_prediction_set(self) -> QueryResult:
        return self._read(
            required={
                "prediction_sets": frozenset(
                    {
                        "prediction_set_id",
                        "run_id",
                        "prediction_date",
                        "cutoff_at",
                        "status",
                        "feature_version",
                        "model_version",
                        "strategy_version",
                        "training_start",
                        "training_end",
                        "generated_at",
                        "published_at",
                        "warnings",
                    }
                )
            },
            statement="""
                SELECT
                    prediction_set_id, run_id, prediction_date, cutoff_at,
                    status, feature_version, model_version, strategy_version,
                    training_start, training_end, generated_at, published_at,
                    warnings
                FROM prediction_sets
                ORDER BY prediction_date DESC, generated_at DESC
                LIMIT 1
            """,
        )

    def today_predictions(self) -> QueryResult:
        return self._read(
            required={
                "prediction_sets": frozenset(
                    {
                        "prediction_set_id",
                        "run_id",
                        "prediction_date",
                        "cutoff_at",
                        "status",
                        "generated_at",
                        "published_at",
                        "warnings",
                    }
                ),
                "predictions": frozenset(
                    {
                        "prediction_set_id",
                        "prediction_id",
                        "ticker",
                        "status",
                        "predicted_intraday_return",
                        "prediction_interval_low",
                        "prediction_interval_high",
                        "probability_up",
                        "reference_price",
                        "reference_basis",
                        "predicted_price_difference",
                        "predicted_close",
                        "signal",
                        "rank",
                        "return_threshold",
                        "probability_threshold",
                        "confidence_score",
                        "positive_factors",
                        "negative_factors",
                        "feature_coverage",
                        "warnings",
                        "created_at",
                    }
                ),
            },
            statement="""
                SELECT
                    p.prediction_id, p.ticker, p.status,
                    p.predicted_intraday_return, p.prediction_interval_low,
                    p.prediction_interval_high, p.probability_up,
                    p.reference_price, p.reference_basis,
                    p.predicted_price_difference, p.predicted_close,
                    p.signal, p.rank, p.return_threshold,
                    p.probability_threshold, p.confidence_score,
                    p.positive_factors, p.negative_factors,
                    p.feature_coverage, p.warnings, p.created_at,
                    ps.prediction_set_id, ps.run_id, ps.prediction_date,
                    ps.cutoff_at, ps.status AS prediction_set_status,
                    ps.generated_at, ps.published_at,
                    ps.warnings AS prediction_set_warnings
                FROM predictions AS p
                JOIN prediction_sets AS ps
                  ON ps.prediction_set_id = p.prediction_set_id
                WHERE ps.prediction_set_id = (
                    SELECT prediction_set_id
                    FROM prediction_sets
                    ORDER BY prediction_date DESC, generated_at DESC
                    LIMIT 1
                )
                ORDER BY
                    CASE WHEN p.rank IS NULL THEN 1 ELSE 0 END,
                    p.rank,
                    p.ticker
            """,
        )

    def prediction_history(self, *, limit: int = 500) -> QueryResult:
        return self._read(
            required={
                "prediction_sets": frozenset(
                    {
                        "prediction_set_id",
                        "prediction_date",
                        "cutoff_at",
                        "generated_at",
                    }
                ),
                "predictions": frozenset(
                    {
                        "prediction_set_id",
                        "prediction_id",
                        "ticker",
                        "status",
                        "predicted_intraday_return",
                        "prediction_interval_low",
                        "prediction_interval_high",
                        "probability_up",
                        "reference_price",
                        "predicted_close",
                        "signal",
                        "rank",
                        "confidence_score",
                        "positive_factors",
                        "negative_factors",
                        "feature_coverage",
                        "warnings",
                    }
                ),
            },
            statement="""
                SELECT
                    p.prediction_id, p.ticker, p.status,
                    p.predicted_intraday_return, p.prediction_interval_low,
                    p.prediction_interval_high, p.probability_up,
                    p.reference_price, p.predicted_close, p.signal, p.rank,
                    p.confidence_score, p.positive_factors,
                    p.negative_factors, p.feature_coverage, p.warnings,
                    ps.prediction_date, ps.cutoff_at, ps.generated_at
                FROM predictions AS p
                JOIN prediction_sets AS ps
                  ON ps.prediction_set_id = p.prediction_set_id
                ORDER BY ps.prediction_date DESC, p.ticker
                LIMIT :limit
            """,
            parameters={"limit": max(1, min(limit, 5000))},
        )

    def actual_results(self, *, limit: int = 1000) -> QueryResult:
        return self._read(
            required={
                "actual_results": frozenset(
                    {
                        "actual_result_id",
                        "prediction_id",
                        "result_version",
                        "status",
                        "actual_open",
                        "actual_close",
                        "actual_intraday_return",
                        "actual_price_difference",
                        "observed_at",
                        "finalized_at",
                    }
                )
            },
            statement="""
                SELECT
                    actual_result_id, prediction_id, result_version, status,
                    actual_open, actual_close, actual_intraday_return,
                    actual_price_difference, observed_at, finalized_at
                FROM actual_results
                ORDER BY prediction_id, result_version DESC
                LIMIT :limit
            """,
            parameters={"limit": max(1, min(limit, 5000))},
        )

    def latest_metrics(self, *, limit: int = 500) -> QueryResult:
        return self._read(
            required={
                "metric_snapshots": frozenset(
                    {
                        "metric_snapshot_id",
                        "ticker",
                        "as_of_date",
                        "model_version",
                        "strategy_version",
                        "evaluation_window",
                        "status",
                        "sample_status",
                        "prediction_count",
                        "trade_count",
                        "win_count",
                        "loss_count",
                        "win_rate",
                        "net_profit_jpy",
                        "expectancy_jpy",
                        "profit_factor",
                        "sharpe_ratio",
                        "sortino_ratio",
                        "max_drawdown",
                        "pearson_correlation",
                        "spearman_correlation",
                        "direction_accuracy",
                        "readability_score",
                        "computed_at",
                    }
                )
            },
            statement="""
                SELECT
                    metric_snapshot_id, ticker, as_of_date, model_version,
                    strategy_version, evaluation_window, status, sample_status,
                    prediction_count, trade_count, win_count, loss_count,
                    win_rate, net_profit_jpy, expectancy_jpy, profit_factor,
                    sharpe_ratio, sortino_ratio, max_drawdown,
                    pearson_correlation, spearman_correlation,
                    direction_accuracy, readability_score, computed_at
                FROM metric_snapshots
                ORDER BY as_of_date DESC, computed_at DESC, ticker
                LIMIT :limit
            """,
            parameters={"limit": max(1, min(limit, 5000))},
        )

    def model_coefficients(self, *, limit: int = 2000) -> QueryResult:
        return self._read(
            required={
                "model_runs": frozenset(
                    {
                        "model_run_id",
                        "ticker",
                        "task",
                        "algorithm",
                        "training_start",
                        "training_end",
                        "model_version",
                        "status",
                        "finished_at",
                    }
                ),
                "model_coefficients": frozenset(
                    {
                        "model_run_id",
                        "feature_name",
                        "coefficient",
                        "scaler_mean",
                        "scaler_scale",
                    }
                ),
            },
            statement="""
                SELECT
                    mc.feature_name, mc.coefficient, mc.scaler_mean,
                    mc.scaler_scale, mr.model_run_id, mr.ticker, mr.task,
                    mr.algorithm, mr.training_start, mr.training_end,
                    mr.model_version, mr.finished_at
                FROM model_coefficients AS mc
                JOIN model_runs AS mr
                  ON mr.model_run_id = mc.model_run_id
                WHERE mr.status = 'SUCCESS'
                ORDER BY mr.finished_at DESC, mr.ticker, mc.feature_name
                LIMIT :limit
            """,
            parameters={"limit": max(1, min(limit, 10000))},
        )

    def simulated_trades(self, *, limit: int = 500) -> QueryResult:
        return self._read(
            required={
                "simulated_trades": frozenset(
                    {
                        "trade_id",
                        "prediction_id",
                        "status",
                        "is_simulated",
                        "capital_jpy",
                        "shares",
                        "entry_price",
                        "exit_price",
                        "gross_profit_jpy",
                        "commission_cost_jpy",
                        "slippage_cost_jpy",
                        "net_profit_jpy",
                        "realized_return",
                        "opened_at",
                        "closed_at",
                        "strategy_version",
                    }
                ),
                "predictions": frozenset(
                    {"prediction_id", "prediction_set_id", "ticker", "signal"}
                ),
                "prediction_sets": frozenset({"prediction_set_id", "prediction_date"}),
            },
            statement="""
                SELECT
                    st.trade_id, p.ticker, ps.prediction_date, p.signal,
                    st.status, st.is_simulated, st.capital_jpy, st.shares,
                    st.entry_price, st.exit_price, st.gross_profit_jpy,
                    st.commission_cost_jpy, st.slippage_cost_jpy,
                    st.net_profit_jpy, st.realized_return, st.opened_at,
                    st.closed_at, st.strategy_version
                FROM simulated_trades AS st
                JOIN predictions AS p ON p.prediction_id = st.prediction_id
                JOIN prediction_sets AS ps
                  ON ps.prediction_set_id = p.prediction_set_id
                ORDER BY ps.prediction_date DESC, p.ticker
                LIMIT :limit
            """,
            parameters={"limit": max(1, min(limit, 5000))},
        )

    def provider_selections(self) -> QueryResult:
        return self._read(
            required={
                "daily_runs": frozenset({"run_id", "prediction_date", "started_at"}),
                "provider_selections": frozenset(
                    {
                        "run_id",
                        "canonical_symbol",
                        "interval",
                        "selected_registry_key",
                        "selected_provider",
                        "selection_role",
                        "data_quality",
                        "freshness_status",
                        "cutoff_at",
                        "coverage",
                        "selected_at",
                    }
                ),
            },
            statement="""
                SELECT
                    canonical_symbol, interval, selected_registry_key,
                    selected_provider, selection_role, data_quality,
                    freshness_status, cutoff_at, coverage, selected_at
                FROM provider_selections
                WHERE run_id = (
                    SELECT run_id FROM daily_runs
                    ORDER BY prediction_date DESC, started_at DESC
                    LIMIT 1
                )
                ORDER BY canonical_symbol, interval
            """,
        )

    def ingestion_batches(self) -> QueryResult:
        return self._read(
            required={
                "daily_runs": frozenset({"run_id", "prediction_date", "started_at"}),
                "ingestion_batches": frozenset(
                    {
                        "run_id",
                        "provider",
                        "started_at",
                        "finished_at",
                        "status",
                        "requested_symbols",
                        "succeeded_symbols",
                        "failed_symbols",
                        "inserted_rows",
                        "reused_rows",
                    }
                ),
            },
            statement="""
                SELECT
                    provider, started_at, finished_at, status,
                    requested_symbols, succeeded_symbols, failed_symbols,
                    inserted_rows, reused_rows
                FROM ingestion_batches
                WHERE run_id = (
                    SELECT run_id FROM daily_runs
                    ORDER BY prediction_date DESC, started_at DESC
                    LIMIT 1
                )
                ORDER BY started_at
            """,
        )

    def run_steps(self) -> QueryResult:
        return self._read(
            required={
                "daily_runs": frozenset({"run_id", "prediction_date", "started_at"}),
                "run_steps": frozenset(
                    {
                        "run_id",
                        "step_name",
                        "attempt_number",
                        "status",
                        "started_at",
                        "finished_at",
                    }
                ),
            },
            statement="""
                SELECT
                    step_name, attempt_number, status, started_at, finished_at
                FROM run_steps
                WHERE run_id = (
                    SELECT run_id FROM daily_runs
                    ORDER BY prediction_date DESC, started_at DESC
                    LIMIT 1
                )
                ORDER BY started_at, attempt_number
            """,
        )

    def raw_data_summary(self) -> QueryResult:
        return self._read(
            required={
                "market_data": frozenset(
                    {"retrieved_at", "data_quality", "is_delayed"}
                ),
                "stock_prices": frozenset(
                    {"retrieved_at", "data_quality", "is_delayed"}
                ),
            },
            statement="""
                SELECT
                    'market_data' AS source_table,
                    COUNT(*) AS row_count,
                    MAX(retrieved_at) AS last_retrieved_at,
                    SUM(CASE WHEN is_delayed THEN 1 ELSE 0 END) AS delayed_rows,
                    SUM(CASE WHEN data_quality = 'FREE_UNVERIFIED'
                             THEN 1 ELSE 0 END) AS unverified_rows
                FROM market_data
                UNION ALL
                SELECT
                    'stock_prices' AS source_table,
                    COUNT(*) AS row_count,
                    MAX(retrieved_at) AS last_retrieved_at,
                    SUM(CASE WHEN is_delayed THEN 1 ELSE 0 END) AS delayed_rows,
                    SUM(CASE WHEN data_quality = 'FREE_UNVERIFIED'
                             THEN 1 ELSE 0 END) AS unverified_rows
                FROM stock_prices
            """,
        )
