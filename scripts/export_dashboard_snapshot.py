"""Publish what the dashboard is showing as a public JSON file.

The Streamlit app is private, so the only way to see the current state is to
log in. This writes the same state to a file that can be committed to the
public repository and read from a plain URL by anything - a browser, curl, or
an assistant that cannot hold a Streamlit session.

The file is public, so nothing may reach it by accident. Two rules enforce
that:

1. Every field is copied by an explicit allowlist. No row is ever serialized
   wholesale, so a column added to a query later cannot appear here on its own.
2. Before the file is written, its text is checked against the values of every
   credential-shaped environment variable in this process. If any of them
   appears, nothing is written and the run fails.

The second rule is what makes the first one safe to be wrong about.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Iterable, Mapping
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from sqlalchemy.exc import SQLAlchemyError

from dashboard import DashboardQueryService, QueryResult
from dashboard.completeness import stock_from_details, summarise
from database.connection import create_database_engine

SCHEMA_VERSION = 1

# Substrings that mark an environment variable as carrying a secret. Its value
# is then treated as forbidden output, whatever the variable happens to be for.
_SECRET_NAME_MARKERS = (
    "URL",
    "KEY",
    "TOKEN",
    "SECRET",
    "PASSWORD",
    "PASS",
    "CREDENTIAL",
    "DSN",
)

# A value shorter than this is not distinctive enough to search for: a port
# number or a one-word host would match ordinary text and block every run.
_MIN_SECRET_LENGTH = 8

RUN_FIELDS = (
    "run_id",
    "run_type",
    "prediction_date",
    "cutoff_at",
    "started_at",
    "finished_at",
    "status",
    "current_step",
    "model_version",
    "failed_symbols",
)

PREDICTION_SET_FIELDS = (
    "prediction_set_id",
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
)

PREDICTION_FIELDS = (
    "ticker",
    "status",
    "signal",
    "rank",
    "predicted_intraday_return",
    "prediction_interval_low",
    "prediction_interval_high",
    "return_distribution",
    "arm_predictions",
    "probability_up",
    "reference_price",
    "reference_basis",
    "predicted_price_difference",
    "predicted_close",
    "return_threshold",
    "probability_threshold",
    "confidence_score",
    "feature_coverage",
    "positive_factors",
    "negative_factors",
    "warnings",
    "created_at",
    "actual_open",
)


class SnapshotError(RuntimeError):
    """The snapshot could not be produced safely."""


def _plain(value: object) -> Any:
    """Convert one database value into something JSON can hold."""

    if value is None or isinstance(value, bool | int | float | str):
        return value
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, datetime | date):
        return value.isoformat()
    if isinstance(value, Mapping):
        return {str(key): _plain(item) for key, item in value.items()}
    if isinstance(value, list | tuple | set | frozenset):
        return [_plain(item) for item in value]
    return str(value)


def _pick(row: Mapping[str, object] | None, fields: Iterable[str]) -> dict[str, Any]:
    """Copy only the named fields. Unknown columns never leave the database."""

    if row is None:
        return {}
    return {name: _plain(row.get(name)) for name in fields}


def _state(result: QueryResult) -> dict[str, Any]:
    """The availability of one query, without any connection detail.

    ``QueryResult`` messages are fixed Japanese strings chosen so that a failed
    read never carries a host, a port or a driver name.
    """

    return {"state": str(result.state), "message": result.message}


def _completeness_block(
    completeness: QueryResult, predictions: tuple[dict[str, Any], ...]
) -> dict[str, Any]:
    """The audit, as data, so nobody has to run a command to see it.

    LEGACY_UNKNOWN is reported as itself rather than folded into "clean": a
    feature set written before completeness was recorded says nothing about
    whether anything was missing, and reading it as healthy is the mistake
    this whole line of work exists to stop repeating.
    """

    if not completeness.ready:
        return {"state": str(completeness.state), "stocks": []}
    signals = {
        str(row.get("ticker")): str(row.get("signal") or "")
        for row in predictions
    }
    coverage = {
        str(row.get("ticker")): row.get("feature_coverage") for row in predictions
    }
    summary = summarise(
        [
            stock_from_details(
                str(row.get("ticker")),
                row.get("details"),
                feature_coverage=coverage.get(str(row.get("ticker"))),
                signal=signals.get(str(row.get("ticker")), ""),
            )
            for row in completeness.rows
        ]
    )
    return {
        "state": str(completeness.state),
        "data_status": summary.data_status,
        "stock_count": summary.stock_count,
        "clean": summary.clean_count,
        "degraded": summary.degraded_count,
        "legacy_unknown": summary.unknown_count,
        "buy_count": summary.buy_count,
        "clean_buy": summary.clean_buy_count,
        "degraded_buy": summary.degraded_buy_count,
        "degraded_buy_tickers": [item.ticker for item in summary.degraded_buys],
        "feature_coverage_hides_a_gap": list(summary.hidden_by_feature_coverage),
        "missing_required_ranking": [
            {"indicator": name, "stocks": count}
            for name, count in summary.missing_required_ranking
        ],
        "watched": [
            {"indicator": name, "stocks": count} for name, count in summary.watched()
        ],
        "stocks": [
            {
                "ticker": item.ticker,
                "status": item.status,
                "indicator_coverage": item.indicator_coverage,
                "missing_required": list(item.missing_required),
                "missing_optional": list(item.missing_optional),
                "signal": item.signal,
            }
            for item in summary.stocks
        ],
    }


def build_snapshot(service: DashboardQueryService) -> dict[str, Any]:
    """Assemble the published state from the same reads the dashboard makes."""

    health = service.database_health()
    latest_run = service.latest_run()
    prediction_set = service.latest_prediction_set()
    predictions = service.today_predictions()

    completeness = service.feature_completeness()
    rows = predictions.rows if predictions.ready else ()
    by_status: dict[str, int] = {}
    for row in rows:
        key = str(row.get("status") or "UNKNOWN")
        by_status[key] = by_status.get(key, 0) + 1

    run_row = latest_run.first or {}
    failed_symbols = _plain(run_row.get("failed_symbols")) or []

    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": datetime.now(UTC).isoformat(),
        "source": "ken-yokota/japan-stock-predictor",
        "note": (
            "Public mirror of the private Streamlit dashboard. Read-only, "
            "regenerated by GitHub Actions. Contains no credentials."
        ),
        "availability": {
            "database": _state(health),
            "latest_run": _state(latest_run),
            "prediction_set": _state(prediction_set),
            "predictions": _state(predictions),
            "completeness": _state(completeness),
        },
        # Whether the morning had the data it was owed, published where anyone
        # can read it without a login or a command. Only counts and indicator
        # ids - no values leave the database here either.
        "completeness": _completeness_block(completeness, rows),
        "latest_run": _pick(latest_run.first, RUN_FIELDS),
        "prediction_set": _pick(prediction_set.first, PREDICTION_SET_FIELDS),
        "predictions": [_pick(row, PREDICTION_FIELDS) for row in rows],
        "summary": {
            "ticker_count": len(rows),
            "by_status": by_status,
            "buy_count": sum(1 for row in rows if row.get("signal") == "BUY"),
            "failed_symbol_count": len(failed_symbols)
            if isinstance(failed_symbols, list)
            else 0,
            "failed_symbols": failed_symbols,
            "settled": sum(1 for row in rows if row.get("actual_open") is not None),
        },
    }


def secret_values(environment: Mapping[str, str]) -> list[str]:
    """Every environment value that must never appear in a published file."""

    values: list[str] = []
    for name, value in environment.items():
        upper = name.upper()
        if not any(marker in upper for marker in _SECRET_NAME_MARKERS):
            continue
        text = value.strip()
        if len(text) >= _MIN_SECRET_LENGTH:
            values.append(text)
    return values


def assert_no_secrets(payload: str, environment: Mapping[str, str]) -> None:
    """Refuse to publish if any credential-shaped value survived into the text.

    This is the check that does not depend on the allowlist above being
    complete. It fails closed: a match aborts the run rather than redacting,
    because a snapshot that needed redacting was built wrong.
    """

    leaked = [value for value in secret_values(environment) if value in payload]
    if leaked:
        # The value itself is never printed - that would move the secret into
        # a public build log.
        raise SnapshotError(
            f"refusing to write: {len(leaked)} credential value(s) from the "
            "environment appear in the snapshot"
        )


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("dashboard_snapshot.json"),
        help="file to write (default: dashboard_snapshot.json)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="print the snapshot instead of writing it",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    arguments = _parse_args(argv)
    database_url = os.environ.get("DATABASE_URL", "").strip()
    if not database_url:
        print("DATABASE_URL is not set", file=sys.stderr)
        return 2

    try:
        engine = create_database_engine(database_url)
        snapshot = build_snapshot(DashboardQueryService(engine))
    except SQLAlchemyError:
        # The exception text can carry the host and the user name, so it is
        # never printed or published.
        print("database read failed", file=sys.stderr)
        return 1

    payload = json.dumps(snapshot, ensure_ascii=False, indent=2, sort_keys=False)
    try:
        assert_no_secrets(payload, os.environ)
    except SnapshotError as error:
        print(str(error), file=sys.stderr)
        return 1

    if arguments.dry_run:
        print(payload)
        return 0

    arguments.output.write_text(payload + "\n", encoding="utf-8")
    state = snapshot["availability"]["predictions"]["state"]
    print(
        f"wrote {arguments.output} "
        f"({snapshot['summary']['ticker_count']} tickers, predictions={state})"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
