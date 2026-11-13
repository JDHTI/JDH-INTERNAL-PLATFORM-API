"""Internal support requests from a customer quotation.

Whoever quotes asks for a supported margin on one or more SKUs of one supplier for the
quotation's customer; BI (or a designated reviewer) approves it for good (with a validity),
once (the order's date) or rejects it. Each approved SKU becomes a support
``excepcion_margen`` / ``margen_minimo`` that the order validator already applies.

- ``internal_support_requests`` and ``internal_support_request_items``.
- ``supplier_supports.internal_request_id``: the request a support was born from (a bulk load
  never closes those).
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0062_internal_support_requests"
down_revision: str | None = "0061_support_supported_margin"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TZ = sa.DateTime(timezone=True)
_UUID = postgresql.UUID(as_uuid=True)


def upgrade() -> None:
    """Create the requests, their items, the number sequence and the link on supports."""

    op.execute("CREATE SEQUENCE internal_support_request_number_seq")
    op.create_table(
        "internal_support_requests",
        sa.Column("id", _UUID, primary_key=True),
        sa.Column(
            "number",
            sa.Integer,
            nullable=False,
            unique=True,
            server_default=sa.text("nextval('internal_support_request_number_seq')"),
        ),
        sa.Column("status", sa.String(12), nullable=False, server_default="pendiente"),
        sa.Column("decision", sa.String(12)),
        sa.Column("quotation_id", _UUID, sa.ForeignKey("quotations.id", ondelete="SET NULL")),
        sa.Column("quotation_label", sa.Text),
        sa.Column("customer_nit", sa.Text, nullable=False),
        sa.Column("customer_name", sa.Text),
        sa.Column(
            "commercial_supplier_id",
            _UUID,
            sa.ForeignKey("commercial_suppliers.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("supplier_code", sa.Text, nullable=False),
        sa.Column("supplier_name", sa.Text),
        sa.Column("requested_for", sa.Date, nullable=False),
        sa.Column("agreed_with", sa.Text, nullable=False),
        sa.Column("reason", sa.Text, nullable=False),
        sa.Column("requested_by_id", _UUID, nullable=False),
        sa.Column("requested_by", sa.String(320), nullable=False),
        sa.Column("created_at", _TZ, nullable=False, server_default=sa.func.now()),
        sa.Column("version", sa.Integer, nullable=False, server_default="1"),
        sa.Column("valid_from", sa.Date),
        sa.Column("valid_to", sa.Date),
        sa.Column("decided_by", sa.String(320)),
        sa.Column("decided_at", _TZ),
        sa.Column("decision_reason", sa.Text),
        sa.CheckConstraint(
            "status IN ('pendiente', 'aprobada', 'rechazada')",
            name="ck_internal_support_requests_status",
        ),
        sa.CheckConstraint(
            "decision IS NULL OR decision IN ('persistente', 'unica_vez')",
            name="ck_internal_support_requests_decision",
        ),
        sa.CheckConstraint(
            "valid_from IS NULL OR valid_to IS NULL OR valid_to >= valid_from",
            name="ck_internal_support_requests_validity",
        ),
    )
    op.execute(
        "ALTER SEQUENCE internal_support_request_number_seq "
        "OWNED BY internal_support_requests.number"
    )
    op.create_index(
        "ix_internal_support_requests_status_created",
        "internal_support_requests",
        ["status", "created_at"],
    )
    op.create_index(
        "ix_internal_support_requests_owner_created",
        "internal_support_requests",
        ["requested_by_id", "created_at"],
    )
    op.create_index(
        "ix_internal_support_requests_quotation", "internal_support_requests", ["quotation_id"]
    )
    op.create_table(
        "internal_support_request_items",
        sa.Column("id", _UUID, primary_key=True),
        sa.Column(
            "request_id",
            _UUID,
            sa.ForeignKey("internal_support_requests.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("position", sa.Integer, nullable=False),
        sa.Column("sku", sa.Text, nullable=False),
        sa.Column("description", sa.Text),
        sa.Column("unit", sa.Text),
        sa.Column("cost", sa.Numeric(18, 4)),
        sa.Column("quoted_price", sa.Numeric(18, 4)),
        sa.Column("margin_pct", sa.Numeric(10, 6)),
        sa.Column("min_margin_pct", sa.Numeric(10, 6)),
        sa.Column("requested_margin_pct", sa.Numeric(10, 6), nullable=False),
        sa.Column("approved_margin_pct", sa.Numeric(10, 6)),
        sa.Column("support_id", _UUID, sa.ForeignKey("supplier_supports.id", ondelete="SET NULL")),
        sa.UniqueConstraint("request_id", "sku", name="uq_internal_support_request_items_sku"),
        sa.CheckConstraint(
            "requested_margin_pct >= 0 AND requested_margin_pct < 1",
            name="ck_internal_support_request_items_requested",
        ),
        sa.CheckConstraint(
            "approved_margin_pct IS NULL OR (approved_margin_pct >= 0 AND approved_margin_pct < 1)",
            name="ck_internal_support_request_items_approved",
        ),
    )
    op.create_index(
        "ix_internal_support_request_items_request",
        "internal_support_request_items",
        ["request_id", "position"],
    )
    op.add_column(
        "supplier_supports",
        sa.Column(
            "internal_request_id",
            _UUID,
            sa.ForeignKey("internal_support_requests.id", ondelete="SET NULL"),
        ),
    )
    op.create_index(
        "ix_supplier_supports_internal_request", "supplier_supports", ["internal_request_id"]
    )


def downgrade() -> None:
    """Drop them."""

    op.drop_index("ix_supplier_supports_internal_request", table_name="supplier_supports")
    op.drop_column("supplier_supports", "internal_request_id")
    op.drop_index(
        "ix_internal_support_request_items_request", table_name="internal_support_request_items"
    )
    op.drop_table("internal_support_request_items")
    op.drop_index("ix_internal_support_requests_quotation", table_name="internal_support_requests")
    op.drop_index(
        "ix_internal_support_requests_owner_created", table_name="internal_support_requests"
    )
    op.drop_index(
        "ix_internal_support_requests_status_created", table_name="internal_support_requests"
    )
    op.drop_table("internal_support_requests")
