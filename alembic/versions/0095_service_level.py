"""Nivel de servicio (Abastecimiento): the lines of the fulfilled sales orders, why a line was not
served, and the ABCD classes (2026-11-26).

- ``service_level_reasons``: the area's 46 reasons (``Motivos.xlsx``) with who answers for each.
- ``service_level_lines``: every line of the orders SIESA closed as «Cumplido», copied from the
  warehouse (``consumo.pedidos_venta_lineas``) by the daily job; empty until it runs.
- ``service_level_line_reasons``: the reason of a pending line.
- ``service_level_classes`` and ``service_level_class_runs``: the ABCD class of each product by
  C.O. and of each customer by NIT, and every calculation.
"""

from collections.abc import Sequence
from uuid import uuid4

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0095_service_level"
down_revision: str | Sequence[str] | None = "0094_main_list_stock"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TS = sa.DateTime(timezone=True)
_QTY = sa.Numeric(18, 4)

# The area's catalog (Motivos.xlsx, 2026-11-26): reason, who answers for it.
REASONS = (
    ("AGOTADO PROVEEDOR", "PROVEEDOR"),
    ("AVERIA EN TRANSPORTE PROVEEEDOR", "PROVEEDOR"),
    ("BAJA ROTACION", "COMERCIAL"),
    ("CAMBIAR POR OTRO CODIGO (HOMOLOGACION)", "COMERCIAL"),
    ("CAMBIO DE BODEGA", "LOGISTICA"),
    ("CAMBIO EXPONENCIAL DE PRECIO", "PROVEEDOR"),
    ("COMPROMETIDO PARA OTRAS FECHA DE ENTREGA", "COMERCIAL"),
    ("CORTA FECHA DE VENCIMIENTO", "LOGISTICA"),
    ("CORTA FECHA PROVEEDOR", "PROVEEDOR"),
    ("DESABASTECIMIENTO DE COLFRIOS A CAVA", "LOGISTICA"),
    ("DESCODIFICADO", "COMERCIAL"),
    ("DIFERENCIA DE INVENTARIOS", "LOGISTICA"),
    ("DONACION JDH", "LOGISTICA"),
    ("EN PROCESO DE ENSAMBLE", "OPERADOR LOGISTICO"),
    ("EN PROCESO DE IMPORTACION", "FACTOR EXTERNO"),
    ("EN PROCESO DE TRASLADO DESDE OTRA SUCURSAL", "LOGISTICA"),
    ("ERROR CLIENTE B2B", "COMERCIAL"),
    ("ERROR CODIFICACION", "INVENTARIOS"),
    ("ERROR DIGITACION", "COMERCIAL"),
    ("ERROR EN ENTREGA DEL PROVEEDOR", "PROVEEDOR"),
    ("ERROR EN IMPUESTOS", "PROVEEDOR"),
    ("ERROR EN PRECIO POR LA UNIDAD DE MEDIDA", "COMPRAS"),
    ("ERROR EN RECIBO", "LOGISTICA"),
    ("ERROR EN SISTEMA B2B", "FACTOR EXTERNO"),
    ("ERROR INVENTARIO ENTERPRISE", "FACTOR EXTERNO"),
    ("ESTRATEGIA GERENCIA", "COMERCIAL"),
    ("FACTURACION", "FACTOR EXTERNO"),
    ("GESTION COMPRAS", "COMPRAS"),
    ("INCUMPLIMIENTO TIEMPO DE ENTREGA (PROV)", "PROVEEDOR"),
    ("INGRESO DESPUES DE ASIGNACION DE PEDIDO", "LOGISTICA"),
    ("INVENTARIO POR ASIGNACION", "PROVEEDOR"),
    ("MANEJO AREA DE LOGISTICA", "INVENTARIOS"),
    ("NO CUMPLE PARAMETROS DE CALIDAD", "PROVEEDOR"),
    ("NO CUMPLE PEDIDO MINIMO", "PROVEEDOR"),
    ("NO CUMPLE TOPE MINIMO DE TRASLADO", "LOGISTICA"),
    ("PAGO PROVEEDOR", "TESORERIA"),
    ("PEDIDO ANTIGUO COMPROMETIENDO MERCANCIA", "COMERCIAL"),
    ("PEDIDO DUPLICADO", "COMERCIAL"),
    ("PENDIENTE POR ENCARGO (MTO)", "COMERCIAL"),
    ("PLANEACION DEMANDA", "COMPRAS"),
    ("PROBLEMAS DE ORDEN PUBLICO", "FACTOR EXTERNO"),
    ("PRODUCTO NO CONFORME", "LOGISTICA"),
    ("ROMPIMIENTO INVENTARIOS", "COMERCIAL"),
    ("ROMPIMIENTO INVENTARIOS POR PUNTO DE VENTA", "COMERCIAL"),
    ("SOLO CANAL RETAIL", "COMERCIAL"),
    ("TERMINO PROMOCION DEL PROVEEDOR", "COMERCIAL"),
)


def _code(name: str) -> str:
    """``"CAMBIAR POR OTRO CODIGO (HOMOLOGACION)"`` → ``"cambiar-por-otro-codigo-homologacion"``."""

    words = "".join(c.lower() if c.isalnum() else " " for c in name).split()
    return "-".join(words)[:80]


def upgrade() -> None:
    reasons = op.create_table(
        "service_level_reasons",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("code", sa.String(80), nullable=False, unique=True),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("responsible", sa.String(60), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("sort_order", sa.Integer(), nullable=False),
        sa.Column("created_at", _TS, server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", _TS, server_default=sa.func.now(), nullable=False),
    )
    op.bulk_insert(
        reasons,
        [
            {
                "id": uuid4(),
                "code": _code(name),
                "name": name,
                "responsible": responsible,
                "is_active": True,
                "sort_order": index * 10,
            }
            for index, (name, responsible) in enumerate(REASONS, start=1)
        ],
    )
    op.create_table(
        "service_level_lines",
        sa.Column("line_key", sa.Text(), primary_key=True),
        sa.Column("order_key", sa.Text(), nullable=False),
        sa.Column("document", sa.Text()),
        sa.Column("co", sa.Text()),
        sa.Column("co_name", sa.Text()),
        sa.Column("order_date", sa.Date(), nullable=False),
        sa.Column("erp_updated_at", _TS),
        sa.Column("warehouse_code", sa.Text()),
        sa.Column("warehouse_name", sa.Text()),
        sa.Column("sku", sa.Text(), nullable=False),
        sa.Column("description", sa.Text()),
        sa.Column("supplier_code", sa.Text()),
        sa.Column("supplier_name", sa.Text()),
        sa.Column("customer_key", sa.Text()),
        sa.Column("customer_nit", sa.Text()),
        sa.Column("customer_name", sa.Text()),
        sa.Column("customer_branch", sa.Text()),
        sa.Column("seller_key", sa.Text()),
        sa.Column("seller_name", sa.Text()),
        sa.Column("qty_ordered", _QTY, nullable=False),
        sa.Column("qty_dispatched", _QTY, nullable=False),
        sa.Column("qty_pending", _QTY, nullable=False),
        sa.Column("value_ordered", _QTY, nullable=False),
        sa.Column("value_pending", _QTY, nullable=False),
        sa.Column("voided", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("synced_at", _TS, nullable=False),
    )
    op.create_index(
        "ix_service_level_lines_order_date_co", "service_level_lines", ["order_date", "co"]
    )
    op.create_index(
        "ix_service_level_lines_pending",
        "service_level_lines",
        ["order_date"],
        postgresql_where=sa.text("qty_pending > 0"),
    )
    for column in ("order_key", "sku", "customer_nit", "supplier_code", "seller_key"):
        op.create_index(f"ix_service_level_lines_{column}", "service_level_lines", [column])
    op.create_table(
        "service_level_line_reasons",
        sa.Column(
            "line_key",
            sa.Text(),
            sa.ForeignKey("service_level_lines.line_key", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "reason_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("service_level_reasons.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("note", sa.Text()),
        sa.Column("assigned_by", sa.String(320), nullable=False),
        sa.Column("assigned_at", _TS, nullable=False),
    )
    op.create_index(
        "ix_service_level_line_reasons_reason_id", "service_level_line_reasons", ["reason_id"]
    )
    op.create_table(
        "service_level_classes",
        sa.Column("kind", sa.String(10), primary_key=True),
        sa.Column("co", sa.String(20), primary_key=True),
        sa.Column("key", sa.Text(), primary_key=True),
        sa.Column("value", sa.Numeric(20, 4), nullable=False),
        sa.Column("share", sa.Numeric(14, 12), nullable=False),
        sa.Column("cumulative", sa.Numeric(14, 12), nullable=False),
        sa.Column("abcd", sa.String(1), nullable=False),
        sa.CheckConstraint("kind IN ('producto','cliente')", name="ck_service_level_classes_kind"),
        sa.CheckConstraint("abcd IN ('A','B','C','D')", name="ck_service_level_classes_abcd"),
    )
    op.create_index("ix_service_level_classes_kind_key", "service_level_classes", ["kind", "key"])
    op.create_table(
        "service_level_class_runs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("period_from", sa.Date(), nullable=False),
        sa.Column("period_to", sa.Date(), nullable=False),
        sa.Column("computed_at", _TS, nullable=False),
        sa.Column("computed_by", sa.String(320)),
        sa.Column("counts", postgresql.JSONB(), nullable=False),
    )
    op.create_index(
        "ix_service_level_class_runs_computed_at", "service_level_class_runs", ["computed_at"]
    )


def downgrade() -> None:
    op.drop_table("service_level_class_runs")
    op.drop_table("service_level_classes")
    op.drop_table("service_level_line_reasons")
    op.drop_table("service_level_lines")
    op.drop_table("service_level_reasons")
