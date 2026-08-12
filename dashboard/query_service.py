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

    def published_prediction_history(self, since: str | None = None) -> QueryResult:
        """Every published prediction in the window, with its settled outcome.

        Left joins on purpose: a prediction whose session has not closed yet
        must still appear, showing what was predicted and nothing else. Dropping
        it would make today's page look empty until the evening.
        """

        clause = "WHERE s.prediction_date >= :since" if since else ""
        return self._read(
            required={
                "predictions": frozenset(
                    {
                        "prediction_id",
                        "prediction_set_id",
                        "ticker",
                        "status",
                        "signal",
                        "predicted_intraday_return",
                        "probability_up",
                        "reference_price",
                        "predicted_close",
                        "predicted_price_difference",
                        "return_threshold",
                        "probability_threshold",
                        "positive_factors",
                        "negative_factors",
                    }
                ),
                "prediction_sets": frozenset(
                    {
                        "prediction_set_id",
                        "prediction_date",
                        "status",
                        "model_version",
                        "feature_version",
                        "strategy_version",
                    }
                ),
                "actual_results": frozenset(
                    {
                        "prediction_id",
                        "actual_open",
                        "actual_close",
                        "actual_intraday_return",
                        "actual_price_difference",
                    }
                ),
                "simulated_trades": frozenset(
                    {"prediction_id", "shares", "net_profit_jpy"}
                ),
            },
            statement=f"""
                SELECT
                    s.prediction_date, s.model_version, s.feature_version,
                    s.strategy_version,
                    p.ticker, p.status, p.signal,
                    p.predicted_intraday_return, p.probability_up,
                    p.reference_price, p.predicted_close,
                    p.predicted_price_difference,
                    p.return_threshold, p.probability_threshold,
                    p.positive_factors, p.negative_factors,
                    a.actual_open, a.actual_close, a.actual_intraday_return,
                    a.actual_price_difference,
                    t.shares, t.net_profit_jpy
                FROM predictions p
                JOIN prediction_sets s
                  ON s.prediction_set_id = p.prediction_set_id
                LEFT JOIN actual_results a ON a.prediction_id = p.prediction_id
                LEFT JOIN simulated_trades t ON t.prediction_id = p.prediction_id
                {clause}
                ORDER BY s.prediction_date, p.ticker
            """,
            parameters={"since": since} if since else None,
        )

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
                ORDER BY
                    CASE WHEN status = 'SKIPPED' THEN 1 ELSE 0 END,
                    prediction_date DESC,
                    started_at DESC
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

    def feature_completeness(self) -> QueryResult:
        """Per-ticker completeness for the newest published prediction date.

        Read from the same feature sets the run wrote, so the page cannot
        disagree with the audit about what a morning was missing.
        """

        return self._read(
            required={
                "feature_sets": frozenset(
                    {"ticker", "prediction_date", "details", "run_id"}
                )
            },
            statement="""
                SELECT fs.ticker, fs.prediction_date, fs.details
                FROM feature_sets AS fs
                WHERE fs.prediction_date = (
                    SELECT prediction_date
                    FROM prediction_sets
                    ORDER BY prediction_date DESC, generated_at DESC
                    LIMIT 1
                )
                ORDER BY fs.ticker
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
                "actual_results": frozenset(
                    {"prediction_id", "result_version", "actual_open"}
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
                    ps.warnings AS prediction_set_warnings,
                    (
                        SELECT ar.actual_open
                        FROM actual_results AS ar
                        WHERE ar.prediction_id = p.prediction_id
                          AND ar.actual_open IS NOT NULL
                        ORDER BY ar.result_version DESC
                        LIMIT 1
                    ) AS actual_open
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

    def oos_scenario_rows(self, *, limit: int = 5000) -> QueryResult:
        """Return finalized prediction/outcome pairs for scenario recomputation.

        Only the newest ``result_version`` of each prediction is returned, and
        only once the outcome reached ``FINAL`` or ``CORRECTED``.  ``PENDING``
        rows are excluded so an unconfirmed session cannot be traded in a
        recomputed scenario.
        """

        return self._read(
            required={
                "prediction_sets": frozenset(
                    {"prediction_set_id", "prediction_date", "cutoff_at"}
                ),
                "predictions": frozenset(
                    {
                        "prediction_id",
                        "prediction_set_id",
                        "ticker",
                        "status",
                        "predicted_intraday_return",
                        "probability_up",
                    }
                ),
                "actual_results": frozenset(
                    {
                        "prediction_id",
                        "result_version",
                        "status",
                        "actual_open",
                        "actual_close",
                    }
                ),
            },
            statement="""
                SELECT
                    p.ticker,
                    ps.prediction_date,
                    p.predicted_intraday_return AS predicted_return,
                    p.probability_up,
                    ar.actual_open,
                    ar.actual_close,
                    ar.status AS outcome_status,
                    ar.result_version
                FROM predictions AS p
                JOIN prediction_sets AS ps
                  ON ps.prediction_set_id = p.prediction_set_id
                JOIN actual_results AS ar
                  ON ar.prediction_id = p.prediction_id
                WHERE p.status = 'SUCCESS'
                  AND ar.status IN ('FINAL', 'CORRECTED')
                  AND ar.actual_open IS NOT NULL
                  AND ar.actual_close IS NOT NULL
                  AND ar.result_version = (
                      SELECT MAX(inner_ar.result_version)
                      FROM actual_results AS inner_ar
                      WHERE inner_ar.prediction_id = p.prediction_id
                        AND inner_ar.status IN ('FINAL', 'CORRECTED')
                  )
                ORDER BY ps.prediction_date, p.ticker
                LIMIT :limit
            """,
            parameters={"limit": max(1, min(limit, 20000))},
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

    def coefficient_history(
        self, *, ticker: str, task: str, limit: int = 6000
    ) -> QueryResult:
        """Return one ticker/task's rolling coefficients, newest fit first.

        ``model_coefficients`` returns only the newest fits across all tickers,
        which covers barely a day once 22 tickers are trained. Summarizing how a
        feature behaved over roughly six months needs one ticker's own history,
        so this narrows by ticker/task and lets the caller ask for far more rows.
        """

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
                    {"model_run_id", "feature_name", "coefficient"}
                ),
            },
            statement="""
                SELECT
                    mr.model_run_id, mr.training_start, mr.training_end,
                    mr.finished_at, mr.algorithm, mr.model_version,
                    mc.feature_name, mc.coefficient
                FROM model_coefficients AS mc
                JOIN model_runs AS mr
                  ON mr.model_run_id = mc.model_run_id
                WHERE mr.status = 'SUCCESS'
                  AND mr.ticker = :ticker
                  AND mr.task = :task
                ORDER BY mr.training_end DESC, mr.finished_at DESC, mc.feature_name
                LIMIT :limit
            """,
            parameters={
                "ticker": ticker,
                "task": task,
                "limit": max(1, min(limit, 60000)),
            },
        )

    def applied_buy_thresholds(self, *, limit: int = 2000) -> QueryResult:
        """Return the BUY thresholds actually stored on recent predictions.

        The rule shown to a user must be the one that produced the saved
        signals, not whatever ``config/trading.yaml`` happens to say today. A
        config edit that has not been through a morning run would otherwise be
        displayed as if it were already in force.
        """

        return self._read(
            required={
                "prediction_sets": frozenset({"prediction_set_id", "prediction_date"}),
                "predictions": frozenset(
                    {
                        "prediction_set_id",
                        "return_threshold",
                        "probability_threshold",
                        "signal",
                    }
                ),
            },
            statement="""
                SELECT
                    p.return_threshold,
                    p.probability_threshold,
                    COUNT(*) AS prediction_count,
                    SUM(CASE WHEN p.signal = 'BUY' THEN 1 ELSE 0 END) AS buy_count,
                    MIN(ps.prediction_date) AS first_date,
                    MAX(ps.prediction_date) AS last_date
                FROM predictions AS p
                JOIN prediction_sets AS ps
                  ON ps.prediction_set_id = p.prediction_set_id
                WHERE p.return_threshold IS NOT NULL
                  AND p.probability_threshold IS NOT NULL
                GROUP BY p.return_threshold, p.probability_threshold
                ORDER BY MAX(ps.prediction_date) DESC
                LIMIT :limit
            """,
            parameters={"limit": max(1, min(limit, 5000))},
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
                    SELECT run_id FROM provider_selections
                    ORDER BY selected_at DESC
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
                    SELECT run_id FROM ingestion_batches
                    ORDER BY started_at DESC
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
                    -- A holiday leaves a SKIPPED run as the newest row, and it
                    -- owns no steps, batches or selections. Keying off it made
                    -- every status panel read "no data" on the very morning a
                    -- prediction had just been published.
                    ORDER BY
                        CASE WHEN status = 'SKIPPED' THEN 1 ELSE 0 END,
                        prediction_date DESC,
                        started_at DESC
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
