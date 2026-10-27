"""Retained orders validator: lines held back for a missing liquor tax.

- ``order_validation_lines.reason`` accepts ``falta_impuesto_licor``: a line of a product with
  liquor tax that comes without the tax is pending even when it meets its margin.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0032_liquor_tax_reason"
down_revision: str | None = "0031_liquor_tax_value"
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
_NEW_REASONS = (*_OLD_REASONS[:2], "falta_impuesto_licor", *_OLD_REASONS[2:])


def _in(column: str, values: tuple[str, ...]) -> str:
    return f"{column} IN ({', '.join(repr(value) for value in values)})"


def upgrade() -> None:
    """Allow the new reason."""

    op.drop_constraint("ck_order_validation_lines_reason", "order_validation_lines", type_="check")
    op.create_check_constraint(
        "ck_order_validation_lines_reason", "order_validation_lines", _in("reason", _NEW_REASONS)
    )


def downgrade() -> None:
    """Back to the previous reasons; held-back lines fall to a margin check on revalidation."""

    op.execute(
        "UPDATE order_validation_lines SET reason = 'datos_incompletos' "
        "WHERE reason = 'falta_impuesto_licor'"
    )
    op.drop_constraint("ck_order_validation_lines_reason", "order_validation_lines", type_="check")
    op.create_check_constraint(
        "ck_order_validation_lines_reason", "order_validation_lines", _in("reason", _OLD_REASONS)
    )
