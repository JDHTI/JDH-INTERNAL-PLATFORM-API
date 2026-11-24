"""Liquidación de apoyos (2026-11-24): what each supplier owes for a month of sales.

- ``support_settlements``: one per month (``YYYY-MM``), draft or closed, with its totals.
- ``support_settlement_refs``: every supported reference of the month, all its sales and what
  its supports pay (the recomposed margin).
- ``support_settlement_sales``: the sale lines a support covered, with the dimensions of the
  «reintegro» (regional, business unit, channel, city, salesperson, customer).
- ``support_settlement_items``: each support applied to a line, as it was when calculated.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0085_support_settlements"
down_revision: str | Sequence[str] | None = "0084_costs_regional_scope"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_MONEY = sa.Numeric(20, 4)
_QTY = sa.Numeric(18, 4)
_UUID = postgresql.UUID(as_uuid=True)


def _settlement_fk() -> sa.Column:
    return sa.Column(
        "settlement_id",
        _UUID,
        sa.ForeignKey("support_settlements.id", ondelete="CASCADE"),
        nullable=False,
    )


def upgrade() -> None:
    op.create_table(
        "support_settlements",
        sa.Column("id", _UUID, primary_key=True),
        sa.Column("period", sa.String(7), nullable=False, unique=True),
        sa.Column("status", sa.String(12), nullable=False, server_default="borrador"),
        sa.Column("amount", _MONEY, nullable=False, server_default="0"),
        sa.Column("flagged_amount", _MONEY, nullable=False, server_default="0"),
        sa.Column("net", _MONEY, nullable=False, server_default="0"),
        sa.Column("cost", _MONEY, nullable=False, server_default="0"),
        sa.Column("sales_read", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("supported_lines", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("suppliers", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("calculated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("calculated_by", sa.String(320), nullable=False),
        sa.Column("closed_at", sa.DateTime(timezone=True)),
        sa.Column("closed_by", sa.String(320)),
        sa.Column("reopened_at", sa.DateTime(timezone=True)),
        sa.Column("reopened_by", sa.String(320)),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.CheckConstraint(
            "status IN ('borrador','cerrado')", name="ck_support_settlements_status"
        ),
        sa.CheckConstraint(
            "period ~ '^[0-9]{4}-(0[1-9]|1[0-2])$'", name="ck_support_settlements_period"
        ),
    )
    op.create_table(
        "support_settlement_refs",
        sa.Column("id", _UUID, primary_key=True),
        _settlement_fk(),
        sa.Column("supplier_code", sa.Text()),
        sa.Column("supplier_name", sa.Text()),
        sa.Column("sku", sa.Text(), nullable=False),
        sa.Column("description", sa.Text()),
        sa.Column("quantity", _QTY, nullable=False),
        sa.Column("net", _MONEY, nullable=False),
        sa.Column("cost", _MONEY, nullable=False),
        sa.Column("amount", _MONEY, nullable=False),
        sa.Column("flagged_amount", _MONEY, nullable=False),
        sa.Column("supported_lines", sa.Integer(), nullable=False),
        sa.UniqueConstraint("settlement_id", "sku", name="uq_support_settlement_refs_sku"),
    )
    op.create_index(
        "ix_support_settlement_refs_supplier",
        "support_settlement_refs",
        ["settlement_id", "supplier_code"],
    )
    op.create_table(
        "support_settlement_sales",
        sa.Column("id", _UUID, primary_key=True),
        _settlement_fk(),
        sa.Column("line_key", sa.Text(), nullable=False),
        sa.Column("sale_date", sa.Date(), nullable=False),
        sa.Column("document", sa.Text()),
        sa.Column("co", sa.Text()),
        sa.Column("co_name", sa.Text()),
        sa.Column("regional", sa.Text()),
        sa.Column("business_unit", sa.Text()),
        sa.Column("channel", sa.Text()),
        sa.Column("city", sa.Text()),
        sa.Column("salesperson", sa.Text()),
        sa.Column("nit", sa.Text()),
        sa.Column("branch_id", sa.Text()),
        sa.Column("customer_name", sa.Text()),
        sa.Column("business_group", sa.Text()),
        sa.Column("supplier_code", sa.Text()),
        sa.Column("supplier_name", sa.Text()),
        sa.Column("sku", sa.Text(), nullable=False),
        sa.Column("description", sa.Text()),
        sa.Column("warehouse", sa.Text()),
        sa.Column("is_return", sa.Boolean(), nullable=False),
        sa.Column("reason", sa.Text()),
        sa.Column("quantity", _QTY, nullable=False),
        sa.Column("gross", _MONEY, nullable=False),
        sa.Column("net", _MONEY, nullable=False),
        sa.Column("cost", _MONEY, nullable=False),
        sa.Column("base_unit_cost", _MONEY),
        sa.Column("cost_source", sa.String(12), nullable=False),
        sa.Column("amount", _MONEY, nullable=False),
        sa.Column("flags", postgresql.ARRAY(sa.Text()), nullable=False, server_default="{}"),
        sa.UniqueConstraint("settlement_id", "line_key", name="uq_support_settlement_sales_line"),
    )
    op.create_index(
        "ix_support_settlement_sales_supplier",
        "support_settlement_sales",
        ["settlement_id", "supplier_code", "sku"],
    )
    op.create_index(
        "ix_support_settlement_sales_date",
        "support_settlement_sales",
        ["settlement_id", "sale_date"],
    )
    op.create_index(
        "ix_support_settlement_sales_nit", "support_settlement_sales", ["settlement_id", "nit"]
    )
    op.create_table(
        "support_settlement_items",
        sa.Column("id", _UUID, primary_key=True),
        _settlement_fk(),
        sa.Column(
            "sale_id",
            _UUID,
            sa.ForeignKey("support_settlement_sales.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("support_id", _UUID, nullable=False),
        sa.Column("support_version", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(30), nullable=False),
        sa.Column("calc_method", sa.String(30), nullable=False),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("supplier_value", sa.Numeric(18, 6)),
        sa.Column("value_unit", sa.String(10)),
        sa.Column("valid_from", sa.Date()),
        sa.Column("valid_to", sa.Date()),
        sa.Column("negotiated_pct", sa.Numeric(12, 6)),
        sa.Column("settled_pct", sa.Numeric(12, 6)),
        sa.Column("unit_amount", _MONEY, nullable=False),
        sa.Column("amount", _MONEY, nullable=False),
        sa.Column("detail", sa.Text(), nullable=False, server_default=""),
    )
    op.create_index("ix_support_settlement_items_sale", "support_settlement_items", ["sale_id"])
    op.create_index(
        "ix_support_settlement_items_status",
        "support_settlement_items",
        ["settlement_id", "status"],
    )
    op.create_index(
        "ix_support_settlement_items_support",
        "support_settlement_items",
        ["settlement_id", "support_id"],
    )


def downgrade() -> None:
    op.drop_table("support_settlement_items")
    op.drop_table("support_settlement_sales")
    op.drop_table("support_settlement_refs")
    op.drop_table("support_settlements")
