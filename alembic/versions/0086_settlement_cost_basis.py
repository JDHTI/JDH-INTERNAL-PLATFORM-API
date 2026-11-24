"""Liquidación de apoyos with two costs (2026-11-24).

A month is settled with the supplier's quotation (``cotizacion``) and with the inventory cost
billed (``inventario``); each one is its own settlement, read and closed on its own.

- ``support_settlements.cost_basis``: ``cotizacion`` (the months stored so far) or
  ``inventario``.
- One settlement per month and cost basis, instead of one per month.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0086_settlement_cost_basis"
down_revision: str | Sequence[str] | None = "0085_support_settlements"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "support_settlements",
        sa.Column("cost_basis", sa.String(12), nullable=False, server_default="cotizacion"),
    )
    op.create_check_constraint(
        "ck_support_settlements_cost_basis",
        "support_settlements",
        "cost_basis IN ('cotizacion','inventario')",
    )
    # The column-level UNIQUE of 0085 took PostgreSQL's default name.
    op.execute(
        "ALTER TABLE support_settlements DROP CONSTRAINT IF EXISTS support_settlements_period_key"
    )
    op.create_unique_constraint(
        "uq_support_settlements_period_basis", "support_settlements", ["period", "cost_basis"]
    )


def downgrade() -> None:
    op.execute("DELETE FROM support_settlements WHERE cost_basis <> 'cotizacion'")
    op.drop_constraint("uq_support_settlements_period_basis", "support_settlements", type_="unique")
    op.create_unique_constraint("support_settlements_period_key", "support_settlements", ["period"])
    op.drop_constraint("ck_support_settlements_cost_basis", "support_settlements", type_="check")
    op.drop_column("support_settlements", "cost_basis")
