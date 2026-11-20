"""Quoter: the warehouses chosen inside each operation center (2026-11-20).

``quotations.cost_warehouses``: by center code, the main (``BP``) or spot-request (``BSP``)
warehouses chosen to cost and show stock from. A center without an entry keeps its own
warehouses, so every existing quotation stays as it is.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0077_quotation_cost_warehouses"
down_revision: str | Sequence[str] | None = "0076_support_group_wide"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "quotations",
        sa.Column(
            "cost_warehouses",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
    )
    op.alter_column("quotations", "cost_warehouses", server_default=None)


def downgrade() -> None:
    op.drop_column("quotations", "cost_warehouses")
