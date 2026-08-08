"""Add free-provider quality, revision evidence, and selection audit.

Revision ID: 0002_free_provider
Revises: 0001_phase1
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0002_free_provider"
down_revision: str | None = "0001_phase1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _upgrade_price_table(name: str) -> None:
    with op.batch_alter_table(name) as batch:
        batch.alter_column("timestamp", new_column_name="market_timestamp")
        batch.add_column(
            sa.Column(
                "data_quality",
                sa.String(length=32),
                nullable=False,
                server_default="FREE_UNVERIFIED",
            )
        )
        batch.add_column(
            sa.Column(
                "is_realtime", sa.Boolean(), nullable=False, server_default=sa.false()
            )
        )
        batch.add_column(
            sa.Column(
                "is_delayed", sa.Boolean(), nullable=False, server_default=sa.false()
            )
        )
        batch.add_column(
            sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True)
        )
    op.execute(sa.text(f"UPDATE {name} SET last_seen_at = retrieved_at"))
    with op.batch_alter_table(name) as batch:
        batch.alter_column("last_seen_at", nullable=False)
        batch.alter_column("data_quality", server_default=None)
        batch.alter_column("is_realtime", server_default=None)
        batch.alter_column("is_delayed", server_default=None)
        batch.create_check_constraint(
            f"ck_{name}_event_available",
            "market_timestamp <= available_timestamp",
        )
        batch.create_check_constraint(
            f"ck_{name}_observed_retrieved",
            "first_observed_at <= retrieved_at",
        )
        batch.create_check_constraint(
            f"ck_{name}_retrieved_last_seen",
            "retrieved_at <= last_seen_at",
        )
        batch.create_check_constraint(
            f"ck_{name}_source_retrieved",
            "source_timestamp IS NULL OR source_timestamp <= retrieved_at",
        )


def upgrade() -> None:
    _upgrade_price_table("market_data")
    _upgrade_price_table("stock_prices")
    op.create_table(
        "provider_attempts",
        sa.Column("attempt_id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("run_id", sa.String(length=36), nullable=False),
        sa.Column("canonical_symbol", sa.String(length=64), nullable=False),
        sa.Column("interval", sa.String(length=24), nullable=False),
        sa.Column("registry_key", sa.String(length=32), nullable=False),
        sa.Column("provider", sa.String(length=32), nullable=True),
        sa.Column("priority", sa.Integer(), nullable=False),
        sa.Column("accepted", sa.Boolean(), nullable=False),
        sa.Column("data_quality", sa.String(length=32), nullable=True),
        sa.Column("freshness_status", sa.String(length=32), nullable=True),
        sa.Column("expected_session", sa.Date(), nullable=True),
        sa.Column("actual_session", sa.Date(), nullable=True),
        sa.Column("coverage", sa.Float(), nullable=True),
        sa.Column("reason", sa.JSON(), nullable=False),
        sa.Column("attempted_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["run_id"], ["daily_runs.run_id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("attempt_id"),
    )
    op.create_index(
        "ix_provider_attempt_run_symbol",
        "provider_attempts",
        ["run_id", "canonical_symbol"],
    )
    op.create_table(
        "provider_selections",
        sa.Column("selection_id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("run_id", sa.String(length=36), nullable=False),
        sa.Column("canonical_symbol", sa.String(length=64), nullable=False),
        sa.Column("interval", sa.String(length=24), nullable=False),
        sa.Column("selected_registry_key", sa.String(length=32), nullable=False),
        sa.Column("selected_provider", sa.String(length=32), nullable=False),
        sa.Column("selection_role", sa.String(length=16), nullable=False),
        sa.Column("data_quality", sa.String(length=32), nullable=False),
        sa.Column("freshness_status", sa.String(length=32), nullable=False),
        sa.Column("cutoff_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("coverage", sa.Float(), nullable=True),
        sa.Column("details", sa.JSON(), nullable=False),
        sa.Column("selected_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["run_id"], ["daily_runs.run_id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("selection_id"),
        sa.UniqueConstraint(
            "run_id",
            "canonical_symbol",
            "interval",
            name="uq_provider_selection_run_series_interval",
        ),
    )
    op.create_index(
        "ix_provider_selection_run_symbol",
        "provider_selections",
        ["run_id", "canonical_symbol"],
    )


def _downgrade_price_table(name: str) -> None:
    with op.batch_alter_table(name) as batch:
        batch.drop_constraint(f"ck_{name}_source_retrieved", type_="check")
        batch.drop_constraint(f"ck_{name}_retrieved_last_seen", type_="check")
        batch.drop_constraint(f"ck_{name}_observed_retrieved", type_="check")
        batch.drop_constraint(f"ck_{name}_event_available", type_="check")
        batch.drop_column("last_seen_at")
        batch.drop_column("is_delayed")
        batch.drop_column("is_realtime")
        batch.drop_column("data_quality")
        batch.alter_column("market_timestamp", new_column_name="timestamp")


def downgrade() -> None:
    op.drop_index("ix_provider_selection_run_symbol", table_name="provider_selections")
    op.drop_table("provider_selections")
    op.drop_index("ix_provider_attempt_run_symbol", table_name="provider_attempts")
    op.drop_table("provider_attempts")
    _downgrade_price_table("stock_prices")
    _downgrade_price_table("market_data")
