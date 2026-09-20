"""Persist today's linear explanation without copying training matrices.

Revision ID: 0007_model_diagnostics
Revises: 0006_prediction_arms

Nullable; historical records remain unknown, not fabricated or backfilled.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0007_model_diagnostics"
down_revision: str | None = "0006_prediction_arms"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("model_runs", sa.Column("diagnostics", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("model_runs", "diagnostics")
