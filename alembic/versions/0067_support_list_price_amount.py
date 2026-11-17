"""Support type «$ Apoyo en Precio Lista» (``calc_method = valor_precio_lista``).

Rules v2 of the supports master (2026-11-17): a fixed amount taken off the list price, next to
the «$ Apoyo en Costo» (``valor_unidad``). Only the check constraint changes.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0067_support_list_price_amount"
down_revision: str | None = "0066_customer_sales_team"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_BEFORE = (
    "pct_costo",
    "pct_precio_lista",
    "valor_unidad",
    "costo_neto",
    "precio_neto",
    "puntos_margen",
    "pct_ventas",
    "margen_minimo",
)
_AFTER = (*_BEFORE, "valor_precio_lista")


def _constraint(methods: tuple[str, ...]) -> None:
    op.drop_constraint("ck_supplier_supports_calc_method", "supplier_supports", type_="check")
    values = ", ".join(repr(method) for method in methods)
    op.create_check_constraint(
        "ck_supplier_supports_calc_method", "supplier_supports", f"calc_method IN ({values})"
    )


def upgrade() -> None:
    """Allow the new type."""

    _constraint(_AFTER)


def downgrade() -> None:
    """Back to the previous types (fails while a support uses the new one)."""

    _constraint(_BEFORE)
