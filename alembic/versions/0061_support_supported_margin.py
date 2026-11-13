"""Supported margin of a support (``supported_margin_pct``).

The margin down to which the product's margin is authorized to drop on top of the supplier's
support: the supported price is the net cost (cost minus the supplier's money) divided by
``1 - supported_margin_pct``. Replaces the JDH percentage in the general template.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0061_support_supported_margin"
down_revision: str | None = "0060_main_lists_stock"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add the column and its range."""

    op.add_column("supplier_supports", sa.Column("supported_margin_pct", sa.Numeric(10, 6)))
    op.create_check_constraint(
        "ck_supplier_supports_supported_margin",
        "supplier_supports",
        "supported_margin_pct IS NULL OR (supported_margin_pct >= 0 AND supported_margin_pct < 1)",
    )


def downgrade() -> None:
    """Drop it."""

    op.drop_constraint("ck_supplier_supports_supported_margin", "supplier_supports", type_="check")
    op.drop_column("supplier_supports", "supported_margin_pct")
