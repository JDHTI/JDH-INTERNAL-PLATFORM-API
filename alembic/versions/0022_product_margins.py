"""Margins by product and channel, replacing the margin by supplier.

- ``products``: one margin per channel (list, wholesale, mass food, market, salsamentarias),
  its notes and who set it, plus the liquor consumption tax (``IMPTOLIC``). The warehouse
  sync never writes these columns.
- ``commercial_suppliers``: the margin by supplier goes away; the margin now lives on each
  product. Downgrading brings the empty columns back, not their values.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0022_product_margins"
down_revision: str | None = "0021_order_validation_approvals"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_MARGINS = (
    "margin_list",
    "margin_wholesale",
    "margin_mass_food",
    "margin_market",
    "margin_salsamentarias",
)


def upgrade() -> None:
    """Add the product margins and drop the supplier margin."""

    for name in _MARGINS:
        op.add_column("products", sa.Column(name, sa.Numeric(8, 6)))
    op.add_column("products", sa.Column("margin_notes", sa.Text))
    op.add_column("products", sa.Column("margin_source", sa.String(20)))
    op.add_column("products", sa.Column("margin_updated_by", sa.String(320)))
    op.add_column("products", sa.Column("margin_updated_at", sa.DateTime(timezone=True)))
    op.add_column("products", sa.Column("liquor_tax", sa.Numeric(18, 4)))
    op.add_column("products", sa.Column("liquor_tax_coast", sa.Numeric(18, 4)))
    op.add_column("products", sa.Column("liquor_tax_group", sa.String(20)))
    for name in _MARGINS:
        op.create_check_constraint(
            f"ck_products_{name}", "products", f"{name} IS NULL OR ({name} >= 0 AND {name} < 1)"
        )
    op.drop_column("commercial_suppliers", "list_margin_notes")
    op.drop_column("commercial_suppliers", "list_margin_pct")


def downgrade() -> None:
    """Bring the empty supplier margin back and drop the product margins."""

    op.add_column("commercial_suppliers", sa.Column("list_margin_pct", sa.Numeric(8, 6)))
    op.add_column("commercial_suppliers", sa.Column("list_margin_notes", sa.Text))
    for name in _MARGINS:
        op.drop_constraint(f"ck_products_{name}", "products", type_="check")
    for name in (
        "liquor_tax_group",
        "liquor_tax_coast",
        "liquor_tax",
        "margin_updated_at",
        "margin_updated_by",
        "margin_source",
        "margin_notes",
        *reversed(_MARGINS),
    ):
        op.drop_column("products", name)
