"""The current margin an authorized-margin request was compared with.

BI, 2026-11-18: a request (from a quotation or from the requests panel, by customer and SKU)
must ask a margin lower than the product's optimal margin on the customer's list; each item
keeps that margin as it was when asked.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0070_request_target_margin"
down_revision: str | None = "0069_support_promo_warehouses"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add the column (empty for the requests already stored)."""

    op.add_column(
        "internal_support_request_items",
        sa.Column("target_margin_pct", sa.Numeric(10, 6), nullable=True),
    )


def downgrade() -> None:
    """Drop it."""

    op.drop_column("internal_support_request_items", "target_margin_pct")
