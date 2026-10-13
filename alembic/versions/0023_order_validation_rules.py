"""Retained orders validator: margin threshold, margin basis and liquor tax flag.

- ``order_validation_batches.margin_threshold``: margin points an order may fall short by and
  still be released, as a fraction; one value per file. It applies to the margin of each order.
- ``order_validation_lines``: the product margin the line was compared with (``margin_basis``)
  and the liquor tax flag and amount.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0023_order_validation_rules"
down_revision: str | None = "0022_product_margins"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add the threshold, the basis and the flag."""

    op.add_column(
        "order_validation_batches",
        sa.Column("margin_threshold", sa.Numeric(8, 6), nullable=False, server_default="0"),
    )
    op.add_column("order_validation_lines", sa.Column("margin_basis", sa.String(20)))
    op.add_column(
        "order_validation_lines",
        sa.Column("liquor_tax", sa.Boolean, nullable=False, server_default="false"),
    )
    op.add_column("order_validation_lines", sa.Column("liquor_tax_amount", sa.Numeric(18, 4)))


def downgrade() -> None:
    """Drop what this revision added."""

    op.drop_column("order_validation_lines", "liquor_tax_amount")
    op.drop_column("order_validation_lines", "liquor_tax")
    op.drop_column("order_validation_lines", "margin_basis")
    op.drop_column("order_validation_batches", "margin_threshold")
