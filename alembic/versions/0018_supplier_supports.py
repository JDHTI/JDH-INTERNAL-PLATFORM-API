"""Supplier supports master: one normalized rule per support plus volume tiers.

A support is scope (supplier, client, product, region, unit, channel) + benefit
(how the supplier value is applied) + validity, with traceability and the data
issues found while migrating ``1. MAESTRO APOYOS PROVEEDORES.xlsx``. Margins and
net prices are derived by the API, never stored; reference cost and list price
are frozen snapshots taken when the support was agreed.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0018_supplier_supports"
down_revision: str | None = "0017_commercial_masters"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_KINDS = (
    "tactico_cliente",
    "actividad_temporal",
    "lista_especial",
    "rebate",
    "recuperacion_cliente",
    "liquidacion_inventario",
    "presupuesto",
    "excepcion_margen",
)
_FUNDINGS = ("proveedor", "jdh", "mixto")
_SETTLEMENTS = ("nota_credito", "pie_factura", "costo_neto_facturado", "rebate_periodico")
_CALC_METHODS = (
    "pct_costo",
    "pct_precio_lista",
    "valor_unidad",
    "costo_neto",
    "precio_neto",
    "puntos_margen",
    "pct_ventas",
    "margen_minimo",
)
_VALUE_UNITS = ("unidad", "caja", "kg")
_STATUSES = ("activo", "pendiente_revision", "anulado")
_THRESHOLDS = ("unidades", "cajas", "monto_ventas")


def _in(column: str, values: tuple[str, ...]) -> str:
    return f"{column} IN ({', '.join(repr(value) for value in values)})"


def _text_array(name: str) -> sa.Column:
    return sa.Column(
        name, postgresql.ARRAY(sa.Text), nullable=False, server_default=sa.text("'{}'::text[]")
    )


def upgrade() -> None:
    """Create the supports and tiers tables."""

    op.create_table(
        "supplier_supports",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "commercial_supplier_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("commercial_suppliers.id", ondelete="RESTRICT"),
        ),
        sa.Column("external_ref", sa.Text),
        sa.Column("program", sa.Text),
        sa.Column("kind", sa.String(30), nullable=False),
        sa.Column("funding", sa.String(10), nullable=False, server_default="proveedor"),
        sa.Column("settlement_method", sa.String(30)),
        sa.Column("settlement_frequency", sa.Text),
        sa.Column("customer_nit", sa.Text),
        sa.Column("customer_branch_id", sa.Text),
        sa.Column("customer_name", sa.Text),
        sa.Column("customer_group", sa.Text),
        sa.Column(
            "product_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("products.id", ondelete="RESTRICT"),
        ),
        sa.Column("product_sku", sa.Text),
        sa.Column("supplier_sku", sa.Text),
        sa.Column("brand", sa.Text),
        _text_array("regionals"),
        _text_array("business_units"),
        _text_array("channels"),
        sa.Column("warehouse", sa.Text),
        sa.Column("calc_method", sa.String(30), nullable=False),
        sa.Column("supplier_value", sa.Numeric(18, 6)),
        sa.Column("jdh_pct", sa.Numeric(10, 6)),
        sa.Column("value_unit", sa.String(10)),
        sa.Column("target_margin_pct", sa.Numeric(10, 6)),
        sa.Column("min_margin_pct", sa.Numeric(10, 6)),
        sa.Column("list_margin_pct", sa.Numeric(10, 6)),
        sa.Column("pack_factor", sa.Numeric(14, 4)),
        sa.Column("reference_cost", sa.Numeric(18, 4)),
        sa.Column("reference_list_price", sa.Numeric(18, 4)),
        sa.Column("reference_date", sa.Date),
        sa.Column("min_quantity", sa.Numeric(18, 4)),
        sa.Column("committed_volume", sa.Numeric(18, 4)),
        sa.Column("budget_amount", sa.Numeric(18, 2)),
        sa.Column("budget_units", sa.Numeric(18, 4)),
        sa.Column("units_used", sa.Numeric(18, 4)),
        sa.Column("stock_quantity", sa.Numeric(18, 4)),
        sa.Column("conditions", sa.Text),
        sa.Column("valid_from", sa.Date),
        sa.Column("valid_to", sa.Date),
        sa.Column("end_condition", sa.Text),
        sa.Column("status", sa.String(20), nullable=False, server_default="activo"),
        sa.Column("void_reason", sa.Text),
        sa.Column("voided_at", sa.DateTime(timezone=True)),
        sa.Column("authorized_by", sa.Text),
        sa.Column("supplier_contact", sa.Text),
        sa.Column("sales_rep", sa.Text),
        sa.Column("notes", sa.Text),
        sa.Column("source", sa.Text),
        sa.Column("source_key", sa.Text),
        sa.Column(
            "issues",
            postgresql.JSONB,
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column("created_by", sa.String(320), nullable=False),
        sa.Column("updated_by", sa.String(320), nullable=False),
        sa.Column("version", sa.Integer, nullable=False, server_default="1"),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint(_in("kind", _KINDS), name="ck_supplier_supports_kind"),
        sa.CheckConstraint(_in("funding", _FUNDINGS), name="ck_supplier_supports_funding"),
        sa.CheckConstraint(
            _in("calc_method", _CALC_METHODS), name="ck_supplier_supports_calc_method"
        ),
        sa.CheckConstraint(_in("status", _STATUSES), name="ck_supplier_supports_status"),
        sa.CheckConstraint(
            f"settlement_method IS NULL OR {_in('settlement_method', _SETTLEMENTS)}",
            name="ck_supplier_supports_settlement",
        ),
        sa.CheckConstraint(
            f"value_unit IS NULL OR {_in('value_unit', _VALUE_UNITS)}",
            name="ck_supplier_supports_value_unit",
        ),
        sa.CheckConstraint(
            "valid_from IS NULL OR valid_to IS NULL OR valid_to >= valid_from",
            name="ck_supplier_supports_valid_range",
        ),
        sa.CheckConstraint(
            "kind = 'excepcion_margen' OR commercial_supplier_id IS NOT NULL",
            name="ck_supplier_supports_supplier_required",
        ),
        sa.CheckConstraint(
            "(status = 'anulado') = (voided_at IS NOT NULL)",
            name="ck_supplier_supports_void_consistent",
        ),
    )
    op.create_index(
        "ix_supplier_supports_supplier", "supplier_supports", ["commercial_supplier_id"]
    )
    op.create_index("ix_supplier_supports_customer_nit", "supplier_supports", ["customer_nit"])
    op.create_index("ix_supplier_supports_product_id", "supplier_supports", ["product_id"])
    op.create_index("ix_supplier_supports_product_sku", "supplier_supports", ["product_sku"])
    op.create_index(
        "ix_supplier_supports_validity", "supplier_supports", ["valid_from", "valid_to"]
    )
    op.create_index("ix_supplier_supports_status", "supplier_supports", ["status"])
    op.create_index("ix_supplier_supports_kind", "supplier_supports", ["kind"])
    for column in ("regionals", "business_units", "channels", "issues"):
        op.create_index(
            f"ix_supplier_supports_{column}", "supplier_supports", [column], postgresql_using="gin"
        )
    op.create_index(
        "uq_supplier_supports_source_key",
        "supplier_supports",
        ["source_key"],
        unique=True,
        postgresql_where=sa.text("source_key IS NOT NULL"),
    )

    op.create_table(
        "supplier_support_tiers",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "support_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("supplier_supports.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("position", sa.Integer, nullable=False),
        sa.Column("threshold_type", sa.String(20), nullable=False),
        sa.Column("from_value", sa.Numeric(18, 4), nullable=False),
        sa.Column("to_value", sa.Numeric(18, 4)),
        sa.Column("value", sa.Numeric(18, 6), nullable=False),
        sa.CheckConstraint(
            _in("threshold_type", _THRESHOLDS), name="ck_supplier_support_tiers_type"
        ),
        sa.CheckConstraint(
            "to_value IS NULL OR to_value >= from_value", name="ck_supplier_support_tiers_range"
        ),
    )
    op.create_index(
        "ix_supplier_support_tiers_support", "supplier_support_tiers", ["support_id", "position"]
    )


def downgrade() -> None:
    """Drop the supports tables."""

    op.drop_table("supplier_support_tiers")
    op.drop_table("supplier_supports")
