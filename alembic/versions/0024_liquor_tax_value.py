"""Retained orders validator: the liquor tax the order carries.

- ``order_validation_lines.liquor_tax_value``: the ``Vlr. imp. IMPTOLIC`` of the report, the
  liquor tax per unit the order line already carries. The flag now shows only when a product
  with liquor tax comes without it.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0024_liquor_tax_value"
down_revision: str | None = "0023_order_validation_rules"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add the liquor tax value of the line."""

    op.add_column("order_validation_lines", sa.Column("liquor_tax_value", sa.Numeric(18, 4)))


def downgrade() -> None:
    """Drop it."""

    op.drop_column("order_validation_lines", "liquor_tax_value")
