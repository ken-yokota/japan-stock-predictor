"""Give every prediction room for its whole distribution, not two bounds.

Revision ID: 0005_prediction_distribution
Revises: 0004_operational_email_log

``prediction_interval_low``/``high`` held one 95% band derived from the
residuals of the fit that produced the point forecast. Two numbers cannot say
where the other 95% of the mass sits, and an in-sample residual band was never
checkable against outcomes in the first place.

``return_distribution`` holds the fitted conditional-quantile curve as one JSON
document: every level with its predicted return, the L1 penalty the curve was
fitted under, the method name, and the coverage that method actually achieved
on this repository's out-of-sample window. One document rather than a column
per level, because which levels are fitted is a modelling choice that will
change, and a column per level would make changing it a migration.

Nullable and with no backfill: rows written before this existed genuinely have
no distribution, and inventing one for them -- by re-deriving a curve from the
stored bounds under a normal assumption -- would put a fabricated number in the
historical record where a NULL tells the truth.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0005_prediction_distribution"
down_revision: str | None = "0004_operational_email_log"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "predictions",
        sa.Column("return_distribution", sa.JSON(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("predictions", "return_distribution")
