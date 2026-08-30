"""Record every model family's answer beside the one that decides.

Revision ID: 0006_prediction_arms
Revises: 0005_prediction_distribution

The operator asked for six model families to be run each morning rather than
the two that were in production. Running them is only half of it: unless every
answer is written down on the same rows, on the same morning, there is no
basis on which one could ever be compared to the incumbent, and "we tried a
forest once" is not evidence.

``arm_predictions`` holds one document per prediction: for each family, its
point forecast, its probability, its distribution, the hyperparameters chosen
inside that window, and how its spread was arrived at -- conditional, ensemble
or residual, which is the difference between a width that reacts to today's
inputs and one that cannot.

Nullable, and no backfill. Mornings before this ran only two families, and
inventing the other four for them would be fabricating a comparison that never
happened.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0006_prediction_arms"
down_revision: str | None = "0005_prediction_distribution"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "predictions",
        sa.Column("arm_predictions", sa.JSON(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("predictions", "arm_predictions")
