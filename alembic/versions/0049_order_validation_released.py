"""Orders a person marks are released, not ready: the file counts them apart.

``order_validation_batches.released_order_count`` holds the orders with a manual mark
(``order_validation_approvals``). They used to count as ready, so the backfill moves them out
of ``ready_order_count``.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0049_order_validation_released"
down_revision: str | None = "0048_masters_live"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_RELEASED = (
    "(SELECT count(*) FROM order_validation_approvals a"
    " WHERE a.batch_id = order_validation_batches.id)"
)


def upgrade() -> None:
    """Add the released count and move the marked orders out of the ready count."""

    op.add_column(
        "order_validation_batches",
        sa.Column("released_order_count", sa.Integer(), nullable=False, server_default="0"),
    )
    op.execute(
        "UPDATE order_validation_batches SET"
        f" released_order_count = {_RELEASED},"
        f" ready_order_count = GREATEST(ready_order_count - {_RELEASED}, 0)"
    )


def downgrade() -> None:
    """Count the marked orders as ready again and drop the column."""

    op.execute(
        "UPDATE order_validation_batches"
        " SET ready_order_count = ready_order_count + released_order_count"
    )
    op.drop_column("order_validation_batches", "released_order_count")
