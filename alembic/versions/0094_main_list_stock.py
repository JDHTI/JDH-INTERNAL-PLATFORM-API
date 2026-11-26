"""Listas principales: the stock of each item by warehouse (2026-11-26).

- ``main_list_stock``: a main list item's stock in one of its list's warehouses, only those
  with stock, read by the sync. The board filters by warehouse and breaks the stock down.

It starts empty: the next sync of the main lists fills it (and corrects the stock totals,
until now read from each list's saved filter of operation centers).
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0094_main_list_stock"
down_revision: str | Sequence[str] | None = "0093_commercial_budget"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "main_list_stock",
        sa.Column(
            "quotation_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("quotations.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("sku", sa.Text(), primary_key=True),
        sa.Column("warehouse", sa.Text(), primary_key=True),
        sa.Column("warehouse_name", sa.Text()),
        sa.Column("on_hand", sa.Numeric(18, 4), nullable=False),
        sa.Column("available", sa.Numeric(18, 4)),
    )
    op.create_index(
        "ix_main_list_stock_warehouse", "main_list_stock", ["quotation_id", "warehouse"]
    )


def downgrade() -> None:
    op.drop_index("ix_main_list_stock_warehouse", table_name="main_list_stock")
    op.drop_table("main_list_stock")
