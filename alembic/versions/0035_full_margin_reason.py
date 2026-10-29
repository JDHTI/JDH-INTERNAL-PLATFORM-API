"""Retained orders validator: lines with a 100 % margin get their own reason.

- ``order_validation_lines.reason`` accepts ``margen_100``: a product with a 100 % margin holds
  the whole order back (it would inflate the order margin). Before, those lines were
  ``datos_incompletos``; files validated before keep it until they are validated again.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0035_full_margin_reason"
down_revision: str | None = "0034_supplier_support_documents"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_OLD_REASONS = (
    "cumple_con_apoyo",
    "cumple_margen_lista",
    "falta_impuesto_licor",
    "acuerdo_en_revision",
    "no_cumple_acuerdo",
    "acuerdo_no_evaluable",
    "acuerdo_vencido",
    "sin_acuerdo",
    "datos_incompletos",
)
_NEW_REASONS = (*_OLD_REASONS[:-1], "margen_100", "datos_incompletos")


def _in(column: str, values: tuple[str, ...]) -> str:
    return f"{column} IN ({', '.join(repr(value) for value in values)})"


def upgrade() -> None:
    """Allow the new reason."""

    op.drop_constraint("ck_order_validation_lines_reason", "order_validation_lines", type_="check")
    op.create_check_constraint(
        "ck_order_validation_lines_reason", "order_validation_lines", _in("reason", _NEW_REASONS)
    )


def downgrade() -> None:
    """Back to the previous reasons; 100 % margin lines become incomplete data again."""

    op.execute(
        "UPDATE order_validation_lines SET reason = 'datos_incompletos' WHERE reason = 'margen_100'"
    )
    op.drop_constraint("ck_order_validation_lines_reason", "order_validation_lines", type_="check")
    op.create_check_constraint(
        "ck_order_validation_lines_reason", "order_validation_lines", _in("reason", _OLD_REASONS)
    )
