"""Retained orders validator: margin threshold, margin basis and liquor tax flag.

- ``order_validation_batches.margin_threshold``: margin a line may fall short by and still be
  released, as a fraction; one value per file, applied every time it is validated.
- ``order_validation_lines``: the product margin the line was compared with (``margin_basis``),
  the liquor tax flag and amount, and the new reason ``cumple_con_umbral``.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0023_order_validation_rules"
down_revision: str | None = "0022_product_margins"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_OLD_REASONS = (
    "cumple_con_apoyo",
    "cumple_margen_lista",
    "acuerdo_en_revision",
    "no_cumple_acuerdo",
    "acuerdo_no_evaluable",
    "acuerdo_vencido",
    "sin_acuerdo",
    "datos_incompletos",
)
_NEW_REASONS = (*_OLD_REASONS[:2], "cumple_con_umbral", *_OLD_REASONS[2:])


def _in(column: str, values: tuple[str, ...]) -> str:
    return f"{column} IN ({', '.join(repr(value) for value in values)})"


def upgrade() -> None:
    """Add the threshold, the basis, the flag and the new reason."""

    op.add_column(
        "order_validation_batches",
        sa.Column("margin_threshold", sa.Numeric(8, 6), nullable=False, server_default="0"),
    )
    op.add_column("order_validation_lines", sa.Column("margin_basis", sa.String(20)))
    op.add_column(
        "order_validation_lines",
        sa.Column("liquor_tax", sa.Boolean, nullable=False, server_default="false"),
    )
    op.add_column("order_validation_lines", sa.Column("liquor_tax_amount", sa.Numeric(18, 4)))
    op.drop_constraint("ck_order_validation_lines_reason", "order_validation_lines", type_="check")
    op.create_check_constraint(
        "ck_order_validation_lines_reason", "order_validation_lines", _in("reason", _NEW_REASONS)
    )


def downgrade() -> None:
    """Drop what this revision added; lines released by the threshold go back to pending."""

    op.execute(
        "UPDATE order_validation_lines SET status = 'pendiente_validacion', "
        "reason = 'no_cumple_acuerdo' WHERE reason = 'cumple_con_umbral'"
    )
    op.drop_constraint("ck_order_validation_lines_reason", "order_validation_lines", type_="check")
    op.create_check_constraint(
        "ck_order_validation_lines_reason", "order_validation_lines", _in("reason", _OLD_REASONS)
    )
    op.drop_column("order_validation_lines", "liquor_tax_amount")
    op.drop_column("order_validation_lines", "liquor_tax")
    op.drop_column("order_validation_lines", "margin_basis")
    op.drop_column("order_validation_batches", "margin_threshold")
