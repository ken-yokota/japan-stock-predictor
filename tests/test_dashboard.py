"""Dashboard read boundary and pure presentation tests."""

from __future__ import annotations

import ast
import json
import subprocess
import sys
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.engine import Connection

from dashboard.catalog import STOCKS_BY_TICKER
from dashboard.history import build_history_report
from dashboard.presenters import (
    AlertLevel,
    derive_operational_alerts,
    format_jst,
    format_percent_range,
    operational_counts,
    safe_text,
    sector_rows,
    string_list,
    today_table_rows,
)
from dashboard.query_service import DashboardQueryService
from dashboard.research_artifacts import labelled_runs
from dashboard.types import QueryResult, QueryState
from database.models import Base


def _create_daily_runs_table(connection: Connection) -> None:
    connection.exec_driver_sql(
        """
        CREATE TABLE daily_runs (
            run_id TEXT PRIMARY KEY,
            run_type TEXT NOT NULL,
            prediction_date DATE NOT NULL,
            cutoff_at TIMESTAMP,
            started_at TIMESTAMP NOT NULL,
            finished_at TIMESTAMP,
            status TEXT NOT NULL,
            current_step TEXT,
            data_version TEXT NOT NULL,
            model_version TEXT,
            failed_symbols TEXT NOT NULL
        )
        """
    )


def test_query_result_states_are_serializable_and_safe() -> None:
    empty = QueryResult.from_rows(())
    pending = QueryResult.schema_pending(("predictions", "daily_runs"))
    unavailable = QueryResult.unavailable()

    assert empty.state is QueryState.EMPTY
    assert empty.first is None
    assert pending.state is QueryState.SCHEMA_PENDING
    assert "daily_runs" in pending.message
    assert unavailable.state is QueryState.UNAVAILABLE
    assert "password" not in unavailable.message.lower()


def test_query_service_handles_empty_and_unmigrated_databases() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    service = DashboardQueryService(engine)

    assert service.database_health().state is QueryState.READY
    assert service.latest_run().state is QueryState.SCHEMA_PENDING

    with engine.begin() as connection:
        _create_daily_runs_table(connection)

    assert service.latest_run().state is QueryState.EMPTY


def test_query_service_rejects_partial_schema_before_selecting() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    with engine.begin() as connection:
        connection.exec_driver_sql(
            "CREATE TABLE daily_runs (run_id TEXT PRIMARY KEY, status TEXT)"
        )

    result = DashboardQueryService(engine).latest_run()

    assert result.state is QueryState.SCHEMA_PENDING
    assert "daily_runs" in result.message


def test_latest_run_reads_persisted_row_without_writing() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    with engine.begin() as connection:
        _create_daily_runs_table(connection)
        connection.exec_driver_sql(
            """
            INSERT INTO daily_runs (
                run_id, run_type, prediction_date, cutoff_at, started_at,
                finished_at, status, current_step, data_version,
                model_version, failed_symbols
            ) VALUES (
                'run-1', 'DAILY', '2026-08-08', '2026-08-07 23:30:00',
                '2026-08-07 23:20:00', '2026-08-07 23:35:00',
                'SUCCESS', 'PUBLISH', 'data-v1', 'model-v1', '[]'
            )
            """
        )

    statements: list[str] = []

    def capture_statement(
        _connection: Connection,
        _cursor: object,
        statement: str,
        _parameters: object,
        _context: object,
        _executemany: bool,
    ) -> None:
        statements.append(statement.strip())

    event.listen(engine, "before_cursor_execute", capture_statement)
    result = DashboardQueryService(engine).latest_run()
    event.remove(engine, "before_cursor_execute", capture_statement)

    assert result.state is QueryState.READY
    assert result.first is not None
    assert result.first["run_id"] == "run-1"
    assert result.first["status"] == "SUCCESS"
    assert statements
    forbidden = {"INSERT", "UPDATE", "DELETE", "CREATE", "DROP", "ALTER"}
    assert all(
        statement.split(maxsplit=1)[0].upper() not in forbidden
        for statement in statements
    )


def test_all_dashboard_queries_match_the_migrated_schema() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    service = DashboardQueryService(engine)

    results = (
        service.latest_run(),
        service.latest_prediction_set(),
        service.today_predictions(),
        service.prediction_history(),
        service.actual_results(),
        service.latest_metrics(),
        service.model_coefficients(),
        service.simulated_trades(),
        service.provider_selections(),
        service.ingestion_batches(),
        service.run_steps(),
        service.raw_data_summary(),
    )

    assert all(
        result.state not in {QueryState.SCHEMA_PENDING, QueryState.UNAVAILABLE}
        for result in results
    )


def test_presenters_surface_cutoff_quality_and_pending_states() -> None:
    alerts = derive_operational_alerts(
        run={
            "status": "PARTIAL",
            "cutoff_at": None,
            "failed_symbols": ["7203"],
        },
        prediction_set={"status": "BUILDING", "warnings": ["LOW_SAMPLE"]},
        predictions=({"ticker": "7203", "status": "FAILED", "warnings": ["PENDING"]},),
        selections=(
            {
                "selection_role": "FALLBACK",
                "freshness_status": "STALE",
                "data_quality": "FREE_UNVERIFIED",
            },
        ),
    )
    titles = {alert.title for alert in alerts}

    assert "Data Cutoff未記録" in titles
    assert "Provider fallback使用" in titles
    assert "STALE / MISSING" in titles
    assert "品質注意" in titles
    assert any(alert.level is AlertLevel.ERROR for alert in alerts)
    assert (
        operational_counts(
            (
                {
                    "selection_role": "FALLBACK",
                    "freshness_status": "MISSING",
                    "data_quality": "DELAYED",
                },
            )
        ).stale_or_missing
        == 1
    )


def test_presenters_redact_secrets_and_render_jst() -> None:
    secret = "postgresql+psycopg://viewer:super-secret@db.example/predict"

    rendered = safe_text(secret)
    warning_values = string_list(["api_key=should-not-appear", "LOW_SAMPLE"])

    assert "super-secret" not in rendered
    assert "db.example" not in rendered
    assert "should-not-appear" not in " ".join(warning_values)
    assert "LOW_SAMPLE" in warning_values
    assert (
        format_jst(datetime(2026, 8, 7, 23, 30, tzinfo=UTC)) == "2026-08-08 08:30 JST"
    )
    assert format_percent_range(-0.01, 0.02) == "[-1.00%, 2.00%]"
    assert format_percent_range(None, 0.02) == "—"


def test_stock_and_sector_presenters_cover_configured_universe() -> None:
    assert len(STOCKS_BY_TICKER) == 22
    assert len(set(STOCKS_BY_TICKER)) == 22

    predictions = (
        {
            "ticker": "7203",
            "status": "SUCCESS",
            "rank": 1,
            "signal": "BUY",
            "predicted_intraday_return": 0.012,
            "probability_up": 0.62,
            "confidence_score": 80,
            "prediction_interval_low": 0.002,
            "prediction_interval_high": 0.022,
            "feature_coverage": 0.95,
            "positive_factors": ["USDJPY"],
            "negative_factors": [],
        },
        {
            "ticker": "7267",
            "status": "SUCCESS",
            "rank": 2,
            "signal": "NO_BUY",
            "predicted_intraday_return": -0.002,
            "probability_up": 0.48,
            "confidence_score": 60,
            "prediction_interval_low": None,
            "prediction_interval_high": None,
            "feature_coverage": 0.8,
            "positive_factors": [],
            "negative_factors": ["Nikkei futures"],
        },
    )
    metrics = (
        {
            "ticker": "7203",
            "trade_count": 12,
            "win_rate": 0.5,
            "sample_status": "LOW_SAMPLE",
            "readability_score": 70,
        },
    )

    table = today_table_rows(predictions, metrics)
    sectors = sector_rows(predictions, metrics)

    assert table[0]["銘柄"].startswith("7203 ")
    assert table[0]["予測リターン"] == "1.20%"
    assert table[0]["予測区間"] == "[0.20%, 2.20%]"
    assert table[0]["Feature Coverage"] == "95.0%"
    assert table[0]["Positive Factors"] == "USDJPY"
    assert table[1]["Sample"] == "PENDING"
    assert sectors == [
        {
            "業種": "自動車",
            "銘柄数": 2,
            "SUCCESS": 2,
            "BUY": 1,
            "平均予測リターン": 0.005,
            "平均上昇確率": 0.55,
            "平均Readability": 70.0,
        }
    ]


def _dashboard_source_files() -> list[Path]:
    files = [Path("app.py"), *sorted(Path("dashboard").glob("*.py"))]
    files.extend(sorted(Path("pages").glob("*.py")))
    return files


def test_ui_import_graph_cannot_reach_fetch_training_or_delivery_code() -> None:
    """The dashboard may compute, but it must not fetch, train, or deliver.

    ``backtest.scenario``, ``trading``, and ``metrics`` are pure arithmetic over
    rows the dashboard already read, and the Backtest page needs them to
    re-simulate stored predictions under user-chosen thresholds. Reimplementing
    that arithmetic inside ``dashboard/`` would let the displayed numbers drift
    away from the production strategy, which is worse than the dependency.

    Everything that performs I/O or fits a model stays forbidden: provider and
    HTTP clients, the training stack, scoring, and the notification/service
    layers. ``test_dashboard_never_imports_the_training_stack`` additionally
    proves the ban holds transitively at runtime, not just in these files.
    """

    forbidden_roots = {
        "data",
        "features",
        "httpx",
        "models",
        "notifications",
        "services",
        "sklearn",
        "smtplib",
        "yfinance",
    }

    imported: list[tuple[Path, str]] = []
    for path in _dashboard_source_files():
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.extend((path, alias.name) for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.append((path, node.module))

    violations = [
        (path, module)
        for path, module in imported
        if module.split(".", maxsplit=1)[0] in forbidden_roots
    ]
    assert violations == []
    assert not any(module == "database.models" for _, module in imported)

    # Pure packages are allowed only through the specific modules the UI needs,
    # so a later import cannot quietly widen the boundary.
    allowed_submodules = {
        "backtest": {"backtest.scenario"},
        "scoring": {"scoring.stability"},
    }
    for package, allowed in allowed_submodules.items():
        used = {
            module
            for _, module in imported
            if module.split(".", maxsplit=1)[0] == package
        }
        assert used <= allowed, f"{package}: {sorted(used - allowed)}"


def test_dashboard_never_imports_the_training_stack() -> None:
    """Prove transitively that importing the UI cannot load scikit-learn.

    A pure-arithmetic dependency can still drag the training stack in through a
    package ``__init__``. This runs in a fresh interpreter so the assertion is
    about the real import graph rather than modules a previous test cached.
    """

    program = (
        "import sys\n"
        "import dashboard.presenters, dashboard.query_service, dashboard.ui\n"
        "import backtest.scenario\n"
        "leaked = sorted(\n"
        "    name\n"
        "    for name in sys.modules\n"
        "    if name.split('.', 1)[0]\n"
        "    in {'sklearn', 'yfinance', 'httpx', 'smtplib', 'services'}\n"
        ")\n"
        "print(','.join(leaked))\n"
    )
    completed = subprocess.run(
        [sys.executable, "-c", program],
        capture_output=True,
        text=True,
        check=True,
        cwd=Path.cwd(),
    )

    assert completed.stdout.strip() == ""


def test_dashboard_reads_only_database_url_from_environment() -> None:
    path = Path("dashboard/ui.py")
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    environment_keys: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
            continue
        owner = node.func.value
        if (
            node.func.attr == "get"
            and isinstance(owner, ast.Attribute)
            and owner.attr == "environ"
            and isinstance(owner.value, ast.Name)
            and owner.value.id == "os"
            and node.args
            and isinstance(node.args[0], ast.Constant)
            and isinstance(node.args[0].value, str)
        ):
            environment_keys.append(node.args[0].value)

    assert environment_keys == ["DATABASE_URL"]


def _write_window(
    directory: Path, name: str, start: str, end: str, sessions: int = 120
) -> None:
    directory.joinpath(name).write_text(
        json.dumps(
            {
                "generated_for": {
                    "from": start,
                    "to": end,
                    "training_window_sessions": sessions,
                }
            }
        ),
        encoding="utf-8",
    )


def test_each_tested_window_becomes_one_entry_ordered_by_start_date(tmp_path) -> None:
    _write_window(tmp_path, "c.json", "2026-08-01", "2026-08-07")
    _write_window(tmp_path, "a.json", "2026-06-01", "2026-08-07")
    _write_window(tmp_path, "b.json", "2026-07-01", "2026-08-07")

    labels = [label for label, _ in labelled_runs(tmp_path)]
    assert labels == [
        "2026-06-01 〜 2026-08-07",
        "2026-07-01 〜 2026-08-07",
        "2026-08-01 〜 2026-08-07",
    ]


def test_a_window_also_written_as_latest_is_one_entry_not_two(tmp_path) -> None:
    _write_window(tmp_path, "2026-06-01_2026-08-07.json", "2026-06-01", "2026-08-07")
    _write_window(tmp_path, "latest.json", "2026-06-01", "2026-08-07")
    assert len(labelled_runs(tmp_path)) == 1


def test_the_same_dates_trained_differently_are_two_entries(tmp_path) -> None:
    """A different training window is a different model, not a duplicate.

    Keying on dates alone silently dropped one of the two, which is how a
    250-session run could vanish from the page without any error.
    """

    _write_window(tmp_path, "w120.json", "2026-06-01", "2026-08-07", sessions=120)
    _write_window(tmp_path, "w250.json", "2026-06-01", "2026-08-07", sessions=250)
    labels = [label for label, _ in labelled_runs(tmp_path)]
    assert len(labels) == 2
    # The training setup is named only when more than one was run.
    assert all("学習" in label for label in labels)


def test_an_unreadable_artifact_is_skipped_rather_than_breaking_the_page(
    tmp_path,
) -> None:
    tmp_path.joinpath("broken.json").write_text("{not json", encoding="utf-8")
    _write_window(tmp_path, "2026-06-01_2026-08-07.json", "2026-06-01", "2026-08-07")
    assert len(labelled_runs(tmp_path)) == 1


def _history_row(
    day: str, ticker: str, predicted: float, actual: float | None, **extra
):
    """One joined prediction/outcome row shaped like the read query returns."""

    row = {
        "prediction_date": day,
        "ticker": ticker,
        "status": "SUCCESS",
        "signal": extra.get("signal", "BUY"),
        "predicted_intraday_return": Decimal(str(predicted)),
        "probability_up": Decimal("0.65"),
        "reference_price": Decimal("1000"),
        "predicted_close": Decimal("1010"),
        "predicted_price_difference": Decimal("10"),
        "return_threshold": Decimal("0.003"),
        "probability_threshold": Decimal("0.60"),
        "positive_factors": ["usdjpy (+0.20%)"],
        "negative_factors": [],
        "actual_open": None,
        "actual_close": None,
        "actual_intraday_return": None,
        "actual_price_difference": None,
        "shares": extra.get("shares", 100),
        "net_profit_jpy": extra.get("net_profit_jpy"),
    }
    if actual is not None:
        row.update(
            actual_open=Decimal("1000"),
            actual_close=Decimal(str(1000 * (1 + actual))),
            actual_intraday_return=Decimal(str(actual)),
            actual_price_difference=Decimal(str(1000 * actual)),
        )
    return row


def test_history_scores_only_the_sessions_that_have_closed() -> None:
    """An unsettled prediction must not count as right or as wrong.

    Counting it wrong drags the day's accuracy down for no reason; counting it
    right flatters it. Both are worse than leaving it out.
    """

    report = build_history_report(
        [
            _history_row(
                "2026-08-10", "7203", 0.01, 0.02, net_profit_jpy=Decimal("500")
            ),
            _history_row(
                "2026-08-10", "7267", 0.01, -0.02, net_profit_jpy=Decimal("-300")
            ),
            _history_row("2026-08-11", "7203", 0.01, None),
        ]
    )
    totals = report["totals"]
    assert totals["predictions"] == 3
    # Two settled, one right: the unsettled row is excluded from the ratio.
    assert totals["direction_accuracy"] == pytest.approx(0.5)
    assert totals["wins"] == 1
    assert totals["losses"] == 1
    assert totals["net_profit_jpy"] == pytest.approx(200.0)


def test_history_reports_the_rule_stored_on_the_prediction() -> None:
    # Not today's config: an edited threshold that never ran must not be shown
    # as though it produced these signals.
    report = build_history_report([_history_row("2026-08-10", "7203", 0.01, 0.02)])
    assert report["rule"]["return_threshold"] == pytest.approx(0.003)
    assert report["rule"]["probability_threshold"] == pytest.approx(0.60)


def test_history_splits_by_day_and_keeps_them_ordered() -> None:
    report = build_history_report(
        [
            _history_row(
                "2026-08-11", "7203", 0.01, 0.02, net_profit_jpy=Decimal("100")
            ),
            _history_row(
                "2026-08-10", "7203", 0.01, -0.02, net_profit_jpy=Decimal("-50")
            ),
        ]
    )
    assert [day["date"] for day in report["daily"]] == ["2026-08-10", "2026-08-11"]
    assert report["generated_for"]["from"] == "2026-08-10"
    assert report["generated_for"]["to"] == "2026-08-11"


def test_history_renders_the_same_shape_the_report_view_expects() -> None:
    """The page reuses the research renderer, so the keys must line up."""

    report = build_history_report([_history_row("2026-08-10", "7203", 0.01, 0.02)])
    for key in (
        "generated_for",
        "rule",
        "totals",
        "daily",
        "predictions",
        "coefficient_changes",
        "company_coefficients",
        "failures",
        "caveats",
    ):
        assert key in report
    row = report["predictions"][0]
    for key in (
        "date",
        "ticker",
        "signal",
        "predicted_return",
        "probability_up",
        "actual_open",
        "actual_close",
        "actual_return",
        "direction_correct",
        "shares",
        "net_profit_jpy",
        "reference_close",
        "morning_predicted_close",
        "post_open_predicted_close",
    ):
        assert key in row


def test_history_is_empty_without_predictions_rather_than_raising() -> None:
    report = build_history_report([])
    assert report["totals"]["predictions"] == 0
    assert report["totals"]["direction_accuracy"] is None
    assert report["predictions"] == []
