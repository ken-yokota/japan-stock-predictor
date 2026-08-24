"""Let an operational mail have a delivery record of its own.

Revision ID: 0004_operational_email_log
Revises: 0003_prediction_pipeline

``email_logs`` was written for the morning publication, so every row had to
belong to a prediction set. The after-close summary is a delivery too, and it
had no record at all -- which meant nothing could tell whether it went out, the
watchdog could not check it, and a retried workflow had no way to avoid sending
it twice. On a JPX holiday there is no prediction set to attach it to, and that
is exactly a day the operator still wants one mail and not three.

So ``prediction_set_id`` becomes nullable. The uniqueness that matters for
delivery is ``idempotency_key``, which is already unique and already carries the
date.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0004_operational_email_log"
down_revision: str | None = "0003_prediction_pipeline"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# SQLite has no ALTER COLUMN, so the nullability change has to go through
# batch mode, which copies the table. CI runs the migrations on SQLite as a
# portability guard and PostgreSQL is what production uses; a plain
# ``op.alter_column`` passes the second and fails the first.
def upgrade() -> None:
    with op.batch_alter_table("email_logs") as batch:
        batch.alter_column(
            "prediction_set_id",
            existing_type=sa.String(length=36),
            existing_nullable=False,
            nullable=True,
        )


def downgrade() -> None:
    # Rows with no prediction set cannot be represented by the old shape, and
    # silently deleting delivery history to fit it would be worse than failing.
    op.execute("DELETE FROM email_logs WHERE prediction_set_id IS NULL")
    with op.batch_alter_table("email_logs") as batch:
        batch.alter_column(
            "prediction_set_id",
            existing_type=sa.String(length=36),
            existing_nullable=True,
            nullable=False,
        )
