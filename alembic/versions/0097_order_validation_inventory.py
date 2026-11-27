"""Retained orders validator: inventory by warehouse and the invoice discount (2026-11-27).

- ``order_validation_lines.discount_pct``: the line's average discount at the foot of the
  invoice (new column of the SIESA report), as a fraction.
- ``inventory_cost``, ``inventory_stock`` and ``inventory_date``: the product in the line's
  warehouse when it was validated (``consumo.inventario_fecha``: average cost and stock).
- ``reason`` accepts ``precio_libre`` (the warehouse's average cost is 0 or 1) and
  ``margen_100_con_existencias`` (100 % margin with stock in the warehouse: releasable).

Lines validated before keep empty columns until their file is validated again.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0097_order_validation_inventory"
down_revision: str | Sequence[str] | None = "0096_vendor_master_teams"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "order_validation_lines"
_CHECK = "ck_order_validation_lines_reason"
_OLD_REASONS = (
    "cumple_con_apoyo",
    "cumple_margen_lista",
    "falta_impuesto_licor",
    "acuerdo_en_revision",
    "no_cumple_acuerdo",
    "acuerdo_no_evaluable",
    "acuerdo_vencido",
    "sin_acuerdo",
    "margen_100",
    "datos_incompletos",
)
_NEW_REASONS = (
    *_OLD_REASONS[:2],
    "precio_libre",
    "margen_100_con_existencias",
    *_OLD_REASONS[2:],
)


def _in(column: str, values: tuple[str, ...]) -> str:
    return f"{column} IN ({', '.join(repr(value) for value in values)})"


def upgrade() -> None:
    """Add the columns and allow the new reasons."""

    op.add_column(_TABLE, sa.Column("discount_pct", sa.Numeric(14, 6)))
    op.add_column(_TABLE, sa.Column("inventory_cost", sa.Numeric(18, 4)))
    op.add_column(_TABLE, sa.Column("inventory_stock", sa.Numeric(18, 4)))
    op.add_column(_TABLE, sa.Column("inventory_date", sa.Date()))
    op.drop_constraint(_CHECK, _TABLE, type_="check")
    op.create_check_constraint(_CHECK, _TABLE, _in("reason", _NEW_REASONS))


def downgrade() -> None:
    """Back to the previous reasons and columns.

    A line at a free price or a 100 % margin with stock goes back to what it was before: pending
    with ``margen_100``, or ``sin_acuerdo`` (its file must be validated again).
    """

    op.execute(
        f"UPDATE {_TABLE} SET status = 'pendiente_validacion', reason = 'margen_100' "
        "WHERE reason = 'margen_100_con_existencias'"
    )
    op.execute(
        f"UPDATE {_TABLE} SET status = 'pendiente_validacion', reason = 'sin_acuerdo' "
        "WHERE reason = 'precio_libre'"
    )
    op.drop_constraint(_CHECK, _TABLE, type_="check")
    op.create_check_constraint(_CHECK, _TABLE, _in("reason", _OLD_REASONS))
    for column in ("inventory_date", "inventory_stock", "inventory_cost", "discount_pct"):
        op.drop_column(_TABLE, column)
