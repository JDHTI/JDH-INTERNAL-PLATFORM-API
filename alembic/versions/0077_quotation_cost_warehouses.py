"""Quoter: the warehouses chosen inside each operation center (2026-11-20).

- ``quotations.cost_warehouses``: by center code, the main (``BP``) or spot-request
  (``BSP``) warehouses chosen to cost and show stock from. A center without an entry keeps
  its own warehouses.
- ``quotations.promo_warehouses``: by center code, the promotional warehouses added to the
  center's (``consumo.bodegas.promocional``): read for every line, the highest cost with
  stock wins.

Both start empty, so every existing quotation stays as it is.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0077_quotation_cost_warehouses"
down_revision: str | Sequence[str] | None = "0076_support_group_wide"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_COLUMNS = ("cost_warehouses", "promo_warehouses")


def upgrade() -> None:
    for name in _COLUMNS:
        op.add_column(
            "quotations",
            sa.Column(
                name, postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")
            ),
        )
        op.alter_column("quotations", name, server_default=None)


def downgrade() -> None:
    for name in reversed(_COLUMNS):
        op.drop_column("quotations", name)
