"""Create Phase 1 point-in-time market-data schema.

Revision ID: 0001_phase1
Revises: none
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0001_phase1"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _price_table(name: str) -> None:
    op.create_table(
        name,
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("canonical_symbol", sa.String(length=64), nullable=False),
        sa.Column("symbol", sa.String(length=96), nullable=False),
        sa.Column("provider", sa.String(length=32), nullable=False),
        sa.Column("market", sa.String(length=32), nullable=False),
        sa.Column("market_timezone", sa.String(length=64), nullable=False),
        sa.Column("market_date", sa.Date(), nullable=False),
        sa.Column("timestamp", sa.DateTime(timezone=True), nullable=False),
        sa.Column("source_timestamp", sa.DateTime(timezone=True), nullable=True),
        sa.Column("available_timestamp", sa.DateTime(timezone=True), nullable=False),
        sa.Column("first_observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("retrieved_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("interval", sa.String(length=24), nullable=False),
        sa.Column("availability_method", sa.String(length=32), nullable=False),
        sa.Column("open", sa.Numeric(precision=24, scale=10), nullable=True),
        sa.Column("high", sa.Numeric(precision=24, scale=10), nullable=True),
        sa.Column("low", sa.Numeric(precision=24, scale=10), nullable=True),
        sa.Column("close", sa.Numeric(precision=24, scale=10), nullable=False),
        sa.Column("adjusted_close", sa.Numeric(precision=24, scale=10), nullable=True),
        sa.Column("volume", sa.BigInteger(), nullable=True),
        sa.Column("currency", sa.String(length=12), nullable=True),
        sa.Column("raw_hash", sa.String(length=64), nullable=False),
        sa.Column("quality_flags", sa.JSON(), nullable=False),
        sa.CheckConstraint(
            "available_timestamp <= first_observed_at",
            name=f"ck_{name}_availability_observed",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "provider",
            "symbol",
            "interval",
            "timestamp",
            "raw_hash",
            name=f"uq_{name}_revision",
        ),
    )
    op.create_index(
        f"ix_{name}_symbol_available",
        name,
        ["canonical_symbol", "available_timestamp"],
    )
    op.create_index(
        f"ix_{name}_symbol_market_date",
        name,
        ["canonical_symbol", "market_date"],
    )


def upgrade() -> None:
    _price_table("market_data")
    _price_table("stock_prices")
    op.create_table(
        "instrument_mappings",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("canonical_symbol", sa.String(length=64), nullable=False),
        sa.Column("provider", sa.String(length=32), nullable=False),
        sa.Column("provider_symbol", sa.String(length=96), nullable=True),
        sa.Column("exchange_code", sa.String(length=32), nullable=True),
        sa.Column("exchange_mic", sa.String(length=32), nullable=True),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("currency", sa.String(length=12), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("verified_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("details", sa.JSON(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "provider", "canonical_symbol", name="uq_instrument_mapping_provider_symbol"
        ),
    )
    op.create_table(
        "daily_runs",
        sa.Column("run_id", sa.String(length=36), nullable=False),
        sa.Column("run_type", sa.String(length=24), nullable=False),
        sa.Column("prediction_date", sa.Date(), nullable=False),
        sa.Column("cutoff_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("current_step", sa.String(length=64), nullable=True),
        sa.Column("data_version", sa.String(length=64), nullable=False),
        sa.Column("model_version", sa.String(length=64), nullable=True),
        sa.Column("failed_symbols", sa.JSON(), nullable=False),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("run_id"),
    )
    op.create_index(
        "ix_daily_runs_prediction_status",
        "daily_runs",
        ["prediction_date", "status"],
    )
    op.create_table(
        "ingestion_batches",
        sa.Column("batch_id", sa.String(length=36), nullable=False),
        sa.Column("run_id", sa.String(length=36), nullable=False),
        sa.Column("provider", sa.String(length=32), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("requested_symbols", sa.Integer(), nullable=False),
        sa.Column("succeeded_symbols", sa.Integer(), nullable=False),
        sa.Column("failed_symbols", sa.JSON(), nullable=False),
        sa.Column("inserted_rows", sa.Integer(), nullable=False),
        sa.Column("reused_rows", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["run_id"], ["daily_runs.run_id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("batch_id"),
    )
    op.create_index("ix_ingestion_batches_run_id", "ingestion_batches", ["run_id"])


def downgrade() -> None:
    op.drop_index("ix_ingestion_batches_run_id", table_name="ingestion_batches")
    op.drop_table("ingestion_batches")
    op.drop_index("ix_daily_runs_prediction_status", table_name="daily_runs")
    op.drop_table("daily_runs")
    op.drop_table("instrument_mappings")
    for name in ("stock_prices", "market_data"):
        op.drop_index(f"ix_{name}_symbol_market_date", table_name=name)
        op.drop_index(f"ix_{name}_symbol_available", table_name=name)
        op.drop_table(name)
