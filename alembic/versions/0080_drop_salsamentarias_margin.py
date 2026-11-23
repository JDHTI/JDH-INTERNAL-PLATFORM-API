"""The Salsamentarias margin of the products goes away (2026-11-23).

Since 2026-11-17 the customers of the SALSAMENTARIAS channel use the Mayoristas margin, so no
rule reads ``products.margin_salsamentarias`` any more. The column and its range check are
dropped. The downgrade brings both back with the column empty: the margins it held are not
restored.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0080_drop_salsamentarias_margin"
down_revision: str | Sequence[str] | None = "0079_call_center_and_deletions"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_COLUMN = "margin_salsamentarias"
_CHECK = f"ck_products_{_COLUMN}"


def upgrade() -> None:
    """Drop the Salsamentarias margin column and its check."""

    op.drop_constraint(_CHECK, "products", type_="check")
    op.drop_column("products", _COLUMN)


def downgrade() -> None:
    """The column again, empty, with the check ``0029`` gave it."""

    op.add_column("products", sa.Column(_COLUMN, sa.Numeric(8, 6), nullable=True))
    op.create_check_constraint(
        _CHECK, "products", f"{_COLUMN} IS NULL OR ({_COLUMN} >= 0 AND {_COLUMN} < 1)"
    )
