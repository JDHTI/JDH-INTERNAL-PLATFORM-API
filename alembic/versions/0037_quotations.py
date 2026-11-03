"""Quoter: quotations of a customer's price list and their lines.

- ``quotations``: one customer (NIT), the sales window and the state of the price list update.
- ``quotation_lines``: one SKU each, with the snapshot it was priced with (last sale, cost,
  target margin, support) and the advisor's new price. ``origin`` tells a SKU from the sales
  window (``historico``) from one the advisor added (``agregado``, no last sale).
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0037_quotations"
down_revision: str | None = "0036_support_requests"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "quotations",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("customer_nit", sa.Text(), nullable=False),
        sa.Column("customer_name", sa.Text()),
        sa.Column("channel_code", sa.String(10)),
        sa.Column("channel", sa.Text()),
        sa.Column("price_list_id", sa.String(3), nullable=False),
        sa.Column("price_list_name", sa.Text()),
        sa.Column("price_list_customers", sa.Integer()),
        sa.Column("billing_center_code", sa.String(10)),
        sa.Column("salesperson_code", sa.String(20)),
        sa.Column("salesperson_name", sa.Text()),
        sa.Column("window_months", sa.SmallInteger(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("activation_date", sa.Date()),
        sa.Column("export_price_list_id", sa.String(3)),
        sa.Column("warnings", postgresql.ARRAY(sa.Text()), nullable=False),
        sa.Column("built_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("cost_cut_date", sa.Date()),
        sa.Column("max_points_below_target", sa.Numeric(8, 6), nullable=False),
        sa.Column("line_count", sa.Integer(), nullable=False),
        sa.Column("changed_count", sa.Integer(), nullable=False),
        sa.Column("below_rule_count", sa.Integer(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("created_by_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("created_by", sa.String(320), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_by", sa.String(320), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("generated_at", sa.DateTime(timezone=True)),
        sa.Column("generated_by", sa.String(320)),
        sa.CheckConstraint("status IN ('borrador', 'generado')", name="ck_quotations_status"),
        sa.CheckConstraint("window_months IN (6, 12)", name="ck_quotations_window"),
    )
    op.create_index("ix_quotations_owner_created", "quotations", ["created_by_id", "created_at"])
    op.create_index("ix_quotations_customer", "quotations", ["customer_nit"])
    op.create_index("ix_quotations_status", "quotations", ["status"])
    op.create_table(
        "quotation_lines",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "quotation_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("quotations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("sku", sa.Text(), nullable=False),
        sa.Column("description", sa.Text()),
        sa.Column("unit", sa.String(10)),
        sa.Column("pack_factor", sa.Numeric(14, 4)),
        sa.Column("supplier_code", sa.Text()),
        sa.Column("supplier_name", sa.Text()),
        sa.Column("last_price", sa.Numeric(18, 4)),
        sa.Column("last_gross_price", sa.Numeric(18, 4)),
        sa.Column("last_discount_pct", sa.Numeric(10, 6)),
        sa.Column("last_sale_date", sa.Date()),
        sa.Column("last_document", sa.Text()),
        sa.Column("last_sale_unit", sa.String(10)),
        sa.Column("last_quantity", sa.Numeric(18, 4)),
        sa.Column("sales_in_window", sa.Integer(), nullable=False),
        sa.Column("list_price", sa.Numeric(18, 4)),
        sa.Column("vat_pct", sa.Numeric(8, 4)),
        sa.Column("value_tax_key", sa.String(10)),
        sa.Column("value_tax_name", sa.Text()),
        sa.Column("value_tax_amount", sa.Numeric(18, 4)),
        sa.Column("cost", sa.Numeric(18, 4)),
        sa.Column("cost_is_global", sa.Boolean(), nullable=False),
        sa.Column("target_margin_pct", sa.Numeric(14, 6)),
        sa.Column("margin_basis", sa.String(20)),
        sa.Column("min_margin_pct", sa.Numeric(14, 6)),
        sa.Column("current_margin_pct", sa.Numeric(14, 6)),
        sa.Column("current_margin_with_support_pct", sa.Numeric(14, 6)),
        sa.Column("min_price", sa.Numeric(18, 4)),
        sa.Column("support", postgresql.JSONB()),
        sa.Column("support_candidates", postgresql.JSONB(), nullable=False),
        sa.Column("new_price", sa.Numeric(18, 4)),
        sa.Column("new_margin_pct", sa.Numeric(14, 6)),
        sa.Column("new_margin_with_support_pct", sa.Numeric(14, 6)),
        sa.Column("variation_pct", sa.Numeric(14, 6)),
        sa.Column("compliance", sa.String(20)),
        sa.Column("problems", postgresql.ARRAY(sa.Text()), nullable=False),
        sa.Column("origin", sa.String(20), nullable=False),
        sa.UniqueConstraint("quotation_id", "sku", name="uq_quotation_lines_sku"),
        sa.CheckConstraint("origin IN ('historico', 'agregado')", name="ck_quotation_lines_origin"),
    )
    op.create_index("ix_quotation_lines_position", "quotation_lines", ["quotation_id", "position"])


def downgrade() -> None:
    op.drop_index("ix_quotation_lines_position", table_name="quotation_lines")
    op.drop_table("quotation_lines")
    op.drop_index("ix_quotations_status", table_name="quotations")
    op.drop_index("ix_quotations_customer", table_name="quotations")
    op.drop_index("ix_quotations_owner_created", table_name="quotations")
    op.drop_table("quotations")
