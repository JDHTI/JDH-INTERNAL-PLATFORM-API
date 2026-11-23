"""Costos (Abastecimiento): reasons, novelties of the purchase entries and credit notes
(2026-11-23).

- ``cost_reasons``: the 26 reasons of the former «Control de Precios JDH». Four of them need
  the supplier's credit note to close the novelty (a proposal the area edits from the portal).
- ``cost_entry_novelties``: entry lines that did not match the quotation in force, keyed by the
  warehouse's ``compra_linea_key``.
- ``cost_credit_notes`` (one per supplier NIT and number) and ``cost_novelty_credit_notes``
  (the amount against each novelty).
- ``cost_novelty_events``: the history of each novelty.
- Area ``abastecimiento`` (Abastecimiento) when it does not exist, with three roles: the
  leader (everything), «Analista de costos» (manages the four Costos views) and «Comprador»
  (manages the novelties).
"""

from collections.abc import Sequence
from uuid import uuid4

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0080_costs_novelties"
# Chained after the Salsamentarias migration (2026-11-23): both were written on 0079 and the
# other one reached production first (API #109/#111). The id stays so the migrations already
# written on top of this one keep their chain.
down_revision: str | Sequence[str] | None = "0080_drop_salsamentarias_margin"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

AREA = "abastecimiento"
COSTS_MODULES = ("analisis-entradas", "novedades-compras", "alzas-proveedor", "acumulado-alzas")
ROLES = (
    ("Líder de área", True, ("*",)),
    ("Analista de costos", False, COSTS_MODULES),
    ("Comprador", False, ("novedades-compras",)),
)

# Name, whether it needs the supplier's credit note.
REASONS = (
    ("INCREMENTO NO ANUNCIADO", True),
    ("OK, NOVEDAD POR TEMAS DE CANAL", False),
    ("DIFERENCIA POR TEMAS DE FLETE", False),
    ("ERROR EN INGRESO", False),
    ("PRODUCTO IMPORTADO", False),
    ("PRODUCTO CON COMPRA NO CONTINUA", False),
    ("NEGOCIACION ESPECIAL", False),
    ("SOBRECOSTO", True),
    ("PRECIO AFECTADO POR EL VOLUMEN DE COMPRAS", False),
    ("ERROR EN RECIBO", False),
    ("ERROR EN DESPACHO", False),
    ("DESCUENTO NO APLICADO", True),
    ("ERROR FACTURACION PROVEEDOR", True),
    ("COSTO DIFERENTE ENTRE BODEGAS", False),
    ("PRODUCTO CON DESCUENTO", False),
    ("COMPRA ANTES DE INCREMENTO", False),
    ("LLEGA MAS COSTO A OTROS CEDIS", False),
    ("PRODUCTO COMODITIES", False),
    ("PROMOCIONAL", False),
    ("OBSEQUIO", False),
    ("AGREGAR COTIZACION", False),
    ("MODIFICAR COTIZACION", False),
    ("DESCUENTO ESPECIAL", False),
    ("COMPRA A BODEGA AUTOSERVICIOS", False),
    ("PRODUCTO CON TASA DE DESCUENTO POR EDI BOGOTA", False),
    ("COMPRA PUNTO EN ESTABLECIMIENTO DE CONSUMO", False),
)


def _code(name: str) -> str:
    """``"OK, NOVEDAD POR TEMAS DE CANAL"`` → ``"ok-novedad-por-temas-de-canal"``."""

    words = "".join(c.lower() if c.isalnum() else " " for c in name).split()
    return "-".join(words)


def _area() -> None:
    bind = op.get_bind()
    if bind.execute(sa.text("SELECT 1 FROM areas WHERE slug = :slug"), {"slug": AREA}).first():
        return
    tenant = bind.execute(
        sa.text("SELECT tenant_id FROM areas ORDER BY created_at LIMIT 1")
    ).scalar()
    if tenant is None:
        return  # an empty database: the area is created from the administration
    area = uuid4()
    bind.execute(
        sa.text(
            "INSERT INTO areas (id, tenant_id, name, slug, remit, icon, sort_order, created_at, "
            "updated_at) VALUES (:id, :tenant, 'Abastecimiento', :slug, :remit, 'Truck', "
            "(SELECT coalesce(max(sort_order), 0) + 1 FROM areas), now(), now())"
        ),
        {
            "id": area,
            "tenant": tenant,
            "slug": AREA,
            "remit": "Compras, costos de proveedores, alzas y notas crédito.",
        },
    )
    for name, is_leader, modules in ROLES:
        role = uuid4()
        bind.execute(
            sa.text(
                "INSERT INTO area_roles (id, area_id, name, is_leader, created_at, updated_at) "
                "VALUES (:id, :area, :name, :leader, now(), now())"
            ),
            {"id": role, "area": area, "name": name, "leader": is_leader},
        )
        for module in modules:
            bind.execute(
                sa.text(
                    "INSERT INTO area_role_permissions (id, role_id, module, action, created_at, "
                    "updated_at) VALUES (:id, :role, :module, 'manage', now(), now())"
                ),
                {"id": uuid4(), "role": role, "module": module},
            )


def upgrade() -> None:
    reasons = op.create_table(
        "cost_reasons",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("code", sa.String(80), nullable=False, unique=True),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("requires_credit_note", sa.Boolean(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("sort_order", sa.Integer(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )
    op.bulk_insert(
        reasons,
        [
            {
                "id": uuid4(),
                "code": _code(name),
                "name": name,
                "requires_credit_note": needs_note,
                "is_active": True,
                "sort_order": index * 10,
            }
            for index, (name, needs_note) in enumerate(REASONS, start=1)
        ],
    )
    op.create_table(
        "cost_entry_novelties",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("line_key", sa.Text(), nullable=False, unique=True),
        sa.Column("entry_date", sa.Date(), nullable=False),
        sa.Column("co", sa.Text()),
        sa.Column("regional_code", sa.Text()),
        sa.Column("regional_name", sa.Text()),
        sa.Column("warehouse", sa.Text()),
        sa.Column("document", sa.Text()),
        sa.Column("purchase_order", sa.Text()),
        sa.Column("invoice_ref", sa.Text()),
        sa.Column("sku", sa.Text(), nullable=False),
        sa.Column("description", sa.Text()),
        sa.Column("supplier_label", sa.Text()),
        sa.Column("supplier_code", sa.Text()),
        sa.Column("supplier_nit", sa.Text()),
        sa.Column("quantity", sa.Numeric(18, 4)),
        sa.Column("unit", sa.Text()),
        sa.Column("unit_price", sa.Numeric(18, 4), nullable=False),
        sa.Column("quotation", sa.Numeric(18, 4)),
        sa.Column("quotation_since", sa.Date()),
        sa.Column("discount_pct", sa.Numeric(9, 4)),
        sa.Column("difference", sa.Numeric(18, 4)),
        sa.Column("difference_pct", sa.Numeric(18, 6)),
        sa.Column("previous_price", sa.Numeric(18, 4)),
        sa.Column("previous_price_2", sa.Numeric(18, 4)),
        sa.Column("kind", sa.String(20), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("voided", sa.Boolean(), nullable=False),
        sa.Column(
            "reason_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("cost_reasons.id", ondelete="RESTRICT"),
        ),
        sa.Column("reason_by", sa.String(320)),
        sa.Column("reason_at", sa.DateTime(timezone=True)),
        sa.Column("detected_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("synced_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint(
            "status IN ('pendiente','espera_nota_credito','resuelta','anulada')",
            name="ck_cost_entry_novelties_status",
        ),
        sa.CheckConstraint(
            "kind IN ('diferencia','sin_cotizacion','sin_novedad')",
            name="ck_cost_entry_novelties_kind",
        ),
    )
    for column in ("entry_date", "status", "supplier_code", "sku", "reason_id"):
        op.create_index(f"ix_cost_entry_novelties_{column}", "cost_entry_novelties", [column])
    op.create_table(
        "cost_credit_notes",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("supplier_nit", sa.Text(), nullable=False),
        sa.Column("number", sa.String(60), nullable=False),
        sa.Column("original_filename", sa.Text(), nullable=False),
        sa.Column("content_type", sa.String(120), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("storage_key", sa.Text(), nullable=False, unique=True),
        sa.Column("uploaded_by", sa.String(320), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("supplier_nit", "number", name="uq_cost_credit_notes_supplier_number"),
    )
    op.create_table(
        "cost_novelty_credit_notes",
        sa.Column(
            "novelty_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("cost_entry_novelties.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "credit_note_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("cost_credit_notes.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("amount", sa.Numeric(18, 2), nullable=False),
        sa.Column("registered_by", sa.String(320), nullable=False),
        sa.Column("registered_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("amount > 0", name="ck_cost_novelty_credit_notes_amount"),
    )
    op.create_index(
        "ix_cost_novelty_credit_notes_credit_note_id",
        "cost_novelty_credit_notes",
        ["credit_note_id"],
    )
    op.create_table(
        "cost_novelty_events",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "novelty_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("cost_entry_novelties.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("event_type", sa.String(30), nullable=False),
        sa.Column("actor", sa.String(320)),
        sa.Column("data", postgresql.JSONB(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_cost_novelty_events_novelty_id", "cost_novelty_events", ["novelty_id", "created_at"]
    )
    _area()


def downgrade() -> None:
    """The tables go; the Abastecimiento area stays (it may have members and other modules).
    The credit note files in the bucket are not removed."""

    op.drop_index("ix_cost_novelty_events_novelty_id", table_name="cost_novelty_events")
    op.drop_table("cost_novelty_events")
    op.drop_index(
        "ix_cost_novelty_credit_notes_credit_note_id", table_name="cost_novelty_credit_notes"
    )
    op.drop_table("cost_novelty_credit_notes")
    op.drop_table("cost_credit_notes")
    for column in ("entry_date", "status", "supplier_code", "sku", "reason_id"):
        op.drop_index(f"ix_cost_entry_novelties_{column}", table_name="cost_entry_novelties")
    op.drop_table("cost_entry_novelties")
    op.drop_table("cost_reasons")
