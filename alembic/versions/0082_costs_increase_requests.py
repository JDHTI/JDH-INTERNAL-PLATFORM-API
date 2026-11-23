"""Costos: the price increases suppliers send, in review until approved (2026-11-23).

- ``cost_increase_requests``: one workbook («MAESTRA») or manual form of one supplier.
- ``cost_increase_request_lines``: one reference each, with the item as it was and the increase.
- ``cost_increase_line_discounts``: the discount of a regional when it differs from the
  regular one (the item is never repeated per regional).
- ``product_price_increases.source`` (``libro`` or ``solicitud``) and ``request_line_id``: an
  approved line lands in the history of increases and remembers where it came from.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0082_costs_increase_requests"
down_revision: str | Sequence[str] | None = "0081_costs_novelties"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "cost_increase_requests",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("supplier_code", sa.Text(), nullable=False),
        sa.Column("supplier_name", sa.Text()),
        sa.Column("source", sa.String(12), nullable=False),
        sa.Column("status", sa.String(12), nullable=False),
        sa.Column("file_name", sa.Text()),
        sa.Column("storage_key", sa.Text()),
        sa.Column("note", sa.Text()),
        sa.Column("created_by", sa.String(320), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("decided_by", sa.String(320)),
        sa.Column("decided_at", sa.DateTime(timezone=True)),
        sa.Column("decision_note", sa.Text()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "status IN ('en_revision','aprobada','rechazada')",
            name="ck_cost_increase_requests_status",
        ),
        sa.CheckConstraint(
            "source IN ('plantilla','manual')", name="ck_cost_increase_requests_source"
        ),
    )
    op.create_index(
        "ix_cost_increase_requests_status", "cost_increase_requests", ["status", "created_at"]
    )
    op.create_index(
        "ix_cost_increase_requests_supplier_code", "cost_increase_requests", ["supplier_code"]
    )
    op.create_table(
        "cost_increase_request_lines",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "request_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("cost_increase_requests.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("row_number", sa.Integer()),
        sa.Column("sku", sa.Text(), nullable=False),
        sa.Column("supplier_sku", sa.Text()),
        sa.Column("ean", sa.Text()),
        sa.Column("description", sa.Text()),
        sa.Column("pack_factor", sa.Numeric(14, 4)),
        sa.Column("category", sa.Text()),
        sa.Column("tax_group", sa.Text()),
        sa.Column("icui_pct", sa.Numeric(9, 6)),
        sa.Column("ibua", sa.Numeric(18, 4)),
        sa.Column("old_cost", sa.Numeric(18, 4)),
        sa.Column("old_cost_with_tax", sa.Numeric(18, 4)),
        sa.Column("new_cost", sa.Numeric(18, 4), nullable=False),
        sa.Column("new_cost_with_tax", sa.Numeric(18, 4), nullable=False),
        sa.Column("variation_amount", sa.Numeric(18, 4)),
        sa.Column("variation_pct", sa.Numeric(18, 6)),
        sa.Column("change", sa.String(10)),
        sa.Column("increased_on", sa.Date()),
        sa.Column("regular_discount_pct", sa.Numeric(9, 6)),
        sa.Column("observation", sa.Text()),
        sa.Column("accepted", sa.Boolean(), nullable=False),
        sa.Column("ledger_id", postgresql.UUID(as_uuid=True)),
        sa.UniqueConstraint("request_id", "sku", name="uq_cost_increase_request_lines_sku"),
        sa.CheckConstraint("new_cost > 0", name="ck_cost_increase_request_lines_new_cost"),
    )
    op.create_table(
        "cost_increase_line_discounts",
        sa.Column(
            "line_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("cost_increase_request_lines.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("regional", sa.String(20), primary_key=True),
        sa.Column("discount_pct", sa.Numeric(9, 6), nullable=False),
        sa.CheckConstraint(
            "discount_pct >= 0 AND discount_pct <= 1",
            name="ck_cost_increase_line_discounts_pct",
        ),
    )
    op.add_column(
        "product_price_increases",
        sa.Column("source", sa.String(12), nullable=False, server_default="libro"),
    )
    op.add_column(
        "product_price_increases",
        sa.Column("request_line_id", postgresql.UUID(as_uuid=True)),
    )


def downgrade() -> None:
    """The requests go; the increases they wrote stay in the history (as the workbook's)."""

    op.drop_column("product_price_increases", "request_line_id")
    op.drop_column("product_price_increases", "source")
    op.drop_table("cost_increase_line_discounts")
    op.drop_table("cost_increase_request_lines")
    op.drop_index("ix_cost_increase_requests_supplier_code", table_name="cost_increase_requests")
    op.drop_index("ix_cost_increase_requests_status", table_name="cost_increase_requests")
    op.drop_table("cost_increase_requests")
