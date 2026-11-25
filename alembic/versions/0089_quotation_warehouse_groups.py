"""Quoter: warehouses are chosen by whole groups (2026-11-25).

- ``quotations.warehouse_groups``: by center code, the groups chosen in it: main (``BP``),
  spot requests (``BSP``) or promotional (``PROMO``). The screen shows these;
  ``cost_warehouses`` and ``promo_warehouses`` keep their breakdown, as before.

It starts empty. A quotation whose warehouses were chosen one by one keeps its breakdown,
and the screen shows it by the groups of those warehouses.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0089_quotation_warehouse_groups"
down_revision: str | Sequence[str] | None = "0088_increases_chain_support"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "quotations",
        sa.Column(
            "warehouse_groups",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
    )
    op.alter_column("quotations", "warehouse_groups", server_default=None)


def downgrade() -> None:
    op.drop_column("quotations", "warehouse_groups")
