"""Quoter: the customer offer, quantities and the requested products registry.

- ``quotations``: ``validity_days`` (1 to 365) from ``valid_from``, the offer's
  ``conditions`` (JSON list), ``include_seller``, ``quote_quantities``, ``offer_scope``
  (``changed`` or ``all``) and ``seller_contact`` (the customer's salesperson, JSON). Stored
  quotations get 30 days from the day they were created (Bogotá) and no seller until a
  refresh.
- ``quotation_requested_products``: products advisors quoted that SIESA does not have, for
  BI to create or discard. Every local line stored before gets its own entry.
- ``quotation_lines``: ``quantity`` (inventory units) and ``requested_product_id``. Deleting
  a line or a quotation never deletes its registry entry.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0051_quotation_offer"
down_revision: str | Sequence[str] | None = "0050_quotation_local_lines"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_UUID = postgresql.UUID(as_uuid=True)


def upgrade() -> None:
    op.add_column(
        "quotations",
        sa.Column("validity_days", sa.SmallInteger(), nullable=False, server_default="30"),
    )
    op.add_column("quotations", sa.Column("valid_from", sa.Date()))
    op.execute(
        "UPDATE quotations SET valid_from = (created_at AT TIME ZONE 'America/Bogota')::date"
    )
    op.alter_column("quotations", "valid_from", nullable=False)
    op.add_column(
        "quotations",
        sa.Column(
            "conditions",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
    )
    op.add_column(
        "quotations",
        sa.Column("include_seller", sa.Boolean(), nullable=False, server_default=sa.true()),
    )
    op.add_column(
        "quotations",
        sa.Column("quote_quantities", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column(
        "quotations",
        sa.Column("offer_scope", sa.String(10), nullable=False, server_default="changed"),
    )
    op.add_column("quotations", sa.Column("seller_contact", postgresql.JSONB()))
    for name in ("validity_days", "conditions", "include_seller", "quote_quantities"):
        op.alter_column("quotations", name, server_default=None)
    op.alter_column("quotations", "offer_scope", server_default=None)
    op.create_check_constraint(
        "ck_quotations_validity_days", "quotations", "validity_days BETWEEN 1 AND 365"
    )
    op.create_check_constraint(
        "ck_quotations_offer_scope", "quotations", "offer_scope IN ('changed', 'all')"
    )

    op.create_table(
        "quotation_requested_products",
        sa.Column("id", _UUID, primary_key=True),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("supplier_name", sa.Text()),
        sa.Column("estimated_price", sa.Numeric(18, 4)),
        sa.Column("vat_pct", sa.Numeric(8, 4)),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("siesa_sku", sa.Text()),
        sa.Column("note", sa.Text()),
        sa.Column("created_by", sa.String(320), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_by", sa.String(320), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "status IN ('solicitado', 'creado', 'descartado')",
            name="ck_quotation_requested_products_status",
        ),
    )
    op.create_index(
        "ix_quotation_requested_products_status",
        "quotation_requested_products",
        ["status", "created_at"],
    )

    op.add_column("quotation_lines", sa.Column("quantity", sa.Numeric(14, 2)))
    op.create_check_constraint(
        "ck_quotation_lines_quantity", "quotation_lines", "quantity IS NULL OR quantity > 0"
    )
    op.add_column("quotation_lines", sa.Column("requested_product_id", _UUID))
    # Every local line stored before gets its own registry entry, then the key is enforced.
    op.execute(
        "UPDATE quotation_lines SET requested_product_id = gen_random_uuid() WHERE origin = 'local'"
    )
    op.execute(
        "INSERT INTO quotation_requested_products (id, description, supplier_name, "
        "estimated_price, vat_pct, status, created_by, created_at, updated_by, updated_at) "
        "SELECT l.requested_product_id, coalesce(l.description, l.sku), l.supplier_name, "
        "l.new_price, l.vat_pct, 'solicitado', q.created_by, q.updated_at, q.created_by, "
        "q.updated_at FROM quotation_lines l JOIN quotations q ON q.id = l.quotation_id "
        "WHERE l.origin = 'local'"
    )
    op.create_foreign_key(
        "fk_quotation_lines_requested_product",
        "quotation_lines",
        "quotation_requested_products",
        ["requested_product_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(
        "ix_quotation_lines_requested_product", "quotation_lines", ["requested_product_id"]
    )


def downgrade() -> None:
    op.drop_index("ix_quotation_lines_requested_product", table_name="quotation_lines")
    op.drop_constraint(
        "fk_quotation_lines_requested_product", "quotation_lines", type_="foreignkey"
    )
    op.drop_column("quotation_lines", "requested_product_id")
    op.drop_constraint("ck_quotation_lines_quantity", "quotation_lines", type_="check")
    op.drop_column("quotation_lines", "quantity")
    op.drop_index(
        "ix_quotation_requested_products_status", table_name="quotation_requested_products"
    )
    op.drop_table("quotation_requested_products")
    op.drop_constraint("ck_quotations_offer_scope", "quotations", type_="check")
    op.drop_constraint("ck_quotations_validity_days", "quotations", type_="check")
    for name in (
        "seller_contact",
        "offer_scope",
        "quote_quantities",
        "include_seller",
        "conditions",
        "valid_from",
        "validity_days",
    ):
        op.drop_column("quotations", name)
