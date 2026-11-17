"""Employee margin of a product (``margin_employee``, "Margen Empleado JDH").

The margin of the JDH employee price list: the MDE1 average cost divided by
``1 - margin_employee``. Every product starts at 13 %, and a product the sync adds later gets
it from the server default; a person may change it product by product. It is not one of the
channel margins: no order or quotation rule reads it, and it does not count for ``has_margin``.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0065_employee_margin"
down_revision: str | None = "0064_pqrsf_resolution_and_om"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add the column at 13 % for every product, with its range."""

    op.add_column(
        "products",
        sa.Column(
            "margin_employee",
            sa.Numeric(8, 6),
            nullable=True,
            server_default=sa.text("0.130000"),
        ),
    )
    op.execute("UPDATE products SET margin_employee = 0.130000 WHERE margin_employee IS NULL")
    op.create_check_constraint(
        "ck_products_margin_employee",
        "products",
        "margin_employee IS NULL OR (margin_employee > 0 AND margin_employee < 1)",
    )


def downgrade() -> None:
    """Drop it."""

    op.drop_constraint("ck_products_margin_employee", "products", type_="check")
    op.drop_column("products", "margin_employee")
