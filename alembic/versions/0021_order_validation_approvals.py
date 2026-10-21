"""Retained orders validator: manual approval of pending orders.

- ``order_validation_approvals``: a person releases a pending order of an uploaded file by
  hand (who, when, an optional note). The order counts as ready for release while the
  approval exists; its lines keep their own result, and re-validating keeps it.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0021_order_validation_approvals"
down_revision: str | None = "0020_order_validation"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create the manual approvals."""

    op.create_table(
        "order_validation_approvals",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "batch_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("order_validation_batches.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("document", sa.Text, nullable=False),
        sa.Column("approved_by", sa.String(320), nullable=False),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("note", sa.String(500)),
        sa.UniqueConstraint("batch_id", "document", name="uq_order_validation_approvals_document"),
    )


def downgrade() -> None:
    """Drop the manual approvals."""

    op.drop_table("order_validation_approvals")
