"""Stock of the main lists' items, for the board to sort by.

``quotation_lines.stock_on_hand`` and ``stock_available``: the stock of the item in its
list's warehouses, read by the sync of the main lists (``main-lists-sync``) from the
morning's photo. 0 without stock, NULL when it was not read (every other line, and the main
lists until their next sync).
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0060_main_lists_stock"
down_revision: str | Sequence[str] | None = "0059_main_lists_review"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("quotation_lines", sa.Column("stock_on_hand", sa.Numeric(18, 4)))
    op.add_column("quotation_lines", sa.Column("stock_available", sa.Numeric(18, 4)))


def downgrade() -> None:
    op.drop_column("quotation_lines", "stock_available")
    op.drop_column("quotation_lines", "stock_on_hand")
