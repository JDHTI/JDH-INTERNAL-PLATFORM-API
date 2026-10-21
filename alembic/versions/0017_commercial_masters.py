"""Typed commercial masters mirrored from the data warehouse.

Adds ``commercial_suppliers`` (ERP supplier code that groups products, the
``Cod. Proveedor`` of the supports workbook), ``commercial_supplier_nits``
(legal NITs bought from, suggested from purchase history), ``suppliers``
(legal supplier by branch), ``customers`` (client by branch), ``products``,
``product_suppliers`` (purchase-history bridge) and ``supplier_product_codes``
(a supplier's own SKU and pack factor for a JDH product).

Rows are filled by ``scripts/sync-dwh-masters.py`` from ``consumo.*`` and are
keyed by the warehouse surrogate keys, so a re-sync updates in place.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0017_commercial_masters"
down_revision: str | None = "0016_pqrsf_email"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TRIGRAM_COLUMNS = {
    "customers": ("legal_name", "branch_name"),
    "suppliers": ("legal_name",),
    "products": ("description", "sku"),
    "commercial_suppliers": ("name",),
}


def _uuid_pk() -> sa.Column:
    return sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True)


def _created_at() -> sa.Column:
    return sa.Column(
        "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
    )


def _trigram_available(connection: sa.engine.Connection) -> bool:
    available = connection.execute(
        sa.text("SELECT 1 FROM pg_available_extensions WHERE name = 'pg_trgm'")
    ).first()
    if available is None:
        return False
    try:
        with connection.begin_nested():
            connection.execute(sa.text("CREATE EXTENSION IF NOT EXISTS pg_trgm"))
    except sa.exc.DBAPIError:
        return False
    return True


def upgrade() -> None:
    """Create the commercial master tables."""

    op.create_table(
        "commercial_suppliers",
        _uuid_pk(),
        sa.Column("code", sa.Text, nullable=False),
        sa.Column("name", sa.Text, nullable=False),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default="true"),
        sa.Column("product_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("active_product_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("list_margin_pct", sa.Numeric(8, 6)),
        sa.Column("list_margin_notes", sa.Text),
        sa.Column("synced_at", sa.DateTime(timezone=True)),
        _created_at(),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.UniqueConstraint("code", name="uq_commercial_suppliers_code"),
    )
    op.create_table(
        "commercial_supplier_nits",
        _uuid_pk(),
        sa.Column(
            "commercial_supplier_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("commercial_suppliers.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("nit", sa.Text, nullable=False),
        sa.Column("product_links", sa.Integer, nullable=False, server_default="0"),
        sa.Column("is_primary", sa.Boolean, nullable=False, server_default="false"),
        sa.Column("confirmed", sa.Boolean, nullable=False, server_default="false"),
        _created_at(),
        sa.UniqueConstraint(
            "commercial_supplier_id", "nit", name="uq_commercial_supplier_nits_pair"
        ),
    )
    op.create_index("ix_commercial_supplier_nits_nit", "commercial_supplier_nits", ["nit"])

    op.create_table(
        "suppliers",
        _uuid_pk(),
        sa.Column("proveedor_key", sa.Text, nullable=False),
        sa.Column("company_id", sa.SmallInteger, nullable=False),
        sa.Column("nit", sa.Text, nullable=False),
        sa.Column("branch_id", sa.Text),
        sa.Column("legal_name", sa.Text),
        sa.Column("branch_name", sa.Text),
        sa.Column("establishment_name", sa.Text),
        sa.Column("supplier_class", sa.Text),
        sa.Column("supplier_type", sa.Text),
        sa.Column("city", sa.Text),
        sa.Column("payment_condition", sa.Text),
        sa.Column("state", sa.Text),
        sa.Column("branch_state", sa.Text),
        sa.Column("is_active", sa.Boolean, nullable=False),
        sa.Column("entry_date", sa.Date),
        sa.Column("synced_at", sa.DateTime(timezone=True), nullable=False),
        _created_at(),
        sa.UniqueConstraint("proveedor_key", name="uq_suppliers_proveedor_key"),
    )
    op.create_index("ix_suppliers_nit", "suppliers", ["nit"])
    op.create_index("ix_suppliers_is_active", "suppliers", ["is_active"])

    op.create_table(
        "customers",
        _uuid_pk(),
        sa.Column("cliente_key", sa.Text, nullable=False),
        sa.Column("company_id", sa.SmallInteger, nullable=False),
        sa.Column("nit", sa.Text, nullable=False),
        sa.Column("branch_id", sa.Text),
        sa.Column("legal_name", sa.Text),
        sa.Column("branch_name", sa.Text),
        sa.Column("establishment_name", sa.Text),
        sa.Column("channel_code", sa.Text),
        sa.Column("channel", sa.Text),
        sa.Column("business_unit_code", sa.Text),
        sa.Column("business_unit", sa.Text),
        sa.Column("business_group_code", sa.Text),
        sa.Column("business_group", sa.Text),
        sa.Column("billing_center_code", sa.Text),
        sa.Column("billing_center", sa.Text),
        sa.Column("logistics_zone", sa.Text),
        sa.Column("department", sa.Text),
        sa.Column("city", sa.Text),
        sa.Column("salesperson_code", sa.Text),
        sa.Column("salesperson_name", sa.Text),
        sa.Column("state", sa.Text),
        sa.Column("branch_state", sa.Text),
        sa.Column("is_active", sa.Boolean, nullable=False),
        sa.Column("synced_at", sa.DateTime(timezone=True), nullable=False),
        _created_at(),
        sa.UniqueConstraint("cliente_key", name="uq_customers_cliente_key"),
    )
    op.create_index("ix_customers_nit", "customers", ["nit"])
    op.create_index("ix_customers_channel", "customers", ["channel"])
    op.create_index("ix_customers_business_unit", "customers", ["business_unit"])
    op.create_index("ix_customers_business_group", "customers", ["business_group"])
    op.create_index("ix_customers_is_active", "customers", ["is_active"])

    op.create_table(
        "products",
        _uuid_pk(),
        sa.Column("producto_key", sa.Text, nullable=False),
        sa.Column("sku", sa.Text, nullable=False),
        sa.Column("supplier_sku", sa.Text),
        sa.Column("description", sa.Text),
        sa.Column("state", sa.Text),
        sa.Column("is_active", sa.Boolean, nullable=False),
        sa.Column("commercial_supplier_code", sa.Text),
        sa.Column("manufacturer", sa.Text),
        sa.Column("brand", sa.Text),
        sa.Column("category", sa.Text),
        sa.Column("subcategory", sa.Text),
        sa.Column("product_line", sa.Text),
        sa.Column("product_subline", sa.Text),
        sa.Column("product_group", sa.Text),
        sa.Column("pack_factor", sa.Numeric(14, 4)),
        sa.Column("inventory_unit", sa.Text),
        sa.Column("sales_vat_pct", sa.Numeric(8, 4)),
        sa.Column("main_barcode", sa.Text),
        sa.Column("synced_at", sa.DateTime(timezone=True), nullable=False),
        _created_at(),
        sa.UniqueConstraint("producto_key", name="uq_products_producto_key"),
        sa.UniqueConstraint("sku", name="uq_products_sku"),
    )
    op.create_index(
        "ix_products_commercial_supplier_code", "products", ["commercial_supplier_code"]
    )
    op.create_index("ix_products_brand", "products", ["brand"])
    op.create_index("ix_products_category", "products", ["category"])
    op.create_index("ix_products_is_active", "products", ["is_active"])

    op.create_table(
        "product_suppliers",
        _uuid_pk(),
        sa.Column("producto_key", sa.Text, nullable=False),
        sa.Column("proveedor_key", sa.Text, nullable=False),
        sa.Column("first_purchase", sa.Date),
        sa.Column("last_purchase", sa.Date),
        sa.Column("purchase_lines", sa.Integer, nullable=False, server_default="0"),
        sa.Column("synced_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("producto_key", "proveedor_key", name="uq_product_suppliers_pair"),
    )
    op.create_index("ix_product_suppliers_proveedor_key", "product_suppliers", ["proveedor_key"])

    op.create_table(
        "supplier_product_codes",
        _uuid_pk(),
        sa.Column(
            "commercial_supplier_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("commercial_suppliers.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("supplier_sku", sa.Text, nullable=False),
        sa.Column(
            "product_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("products.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("pack_factor", sa.Numeric(14, 4)),
        sa.Column("kg_per_unit", sa.Numeric(14, 4)),
        sa.Column("notes", sa.Text),
        _created_at(),
        sa.UniqueConstraint(
            "commercial_supplier_id",
            "supplier_sku",
            "product_id",
            name="uq_supplier_product_codes_triple",
        ),
    )
    op.create_index(
        "ix_supplier_product_codes_product_id", "supplier_product_codes", ["product_id"]
    )

    connection = op.get_bind()
    if _trigram_available(connection):
        for table, columns in _TRIGRAM_COLUMNS.items():
            for column in columns:
                op.create_index(
                    f"ix_{table}_{column}_trgm",
                    table,
                    [column],
                    postgresql_using="gin",
                    postgresql_ops={column: "gin_trgm_ops"},
                )
    else:
        for table, columns in _TRIGRAM_COLUMNS.items():
            for column in columns:
                op.create_index(f"ix_{table}_{column}_lower", table, [sa.text(f"lower({column})")])


def downgrade() -> None:
    """Drop the commercial master tables. The pg_trgm extension is left installed."""

    for table, columns in _TRIGRAM_COLUMNS.items():
        for column in columns:
            op.execute(f"DROP INDEX IF EXISTS ix_{table}_{column}_trgm")
            op.execute(f"DROP INDEX IF EXISTS ix_{table}_{column}_lower")
    op.drop_table("supplier_product_codes")
    op.drop_table("product_suppliers")
    op.drop_table("products")
    op.drop_table("customers")
    op.drop_table("suppliers")
    op.drop_table("commercial_supplier_nits")
    op.drop_table("commercial_suppliers")
