"""Price increase history of the products, and quotations that start empty.

- ``product_price_increases``: the purchasing area's ``Acumulado Alza Precios``, one row per
  cost change, upserted by ``row_key`` (reference, increase date, notification date). A row
  without ``system_updated_on`` is an increase SIESA does not have yet: the quoter compares
  its new cost.
- ``quotation_lines.increase_cost``: that new cost, when it applied to the line.
- ``quotations.with_history``: false for a SIESA customer's quotation that started empty (a
  refresh prices its added references and reads no sales). Existing ones keep true.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# alembic_version.version_num is VARCHAR(32): keep revision ids short.
revision: str = "0055_price_increases"
down_revision: str | Sequence[str] | None = "0054_quotation_cost_centers"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "product_price_increases",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("row_key", sa.Text(), nullable=False),
        sa.Column("sku", sa.Text(), nullable=False),
        sa.Column("notified_on", sa.Date()),
        sa.Column("system_updated_on", sa.Date()),
        sa.Column("increased_on", sa.Date()),
        sa.Column("supplier_sku", sa.Text()),
        sa.Column("ean", sa.Text()),
        sa.Column("regional", sa.Text()),
        sa.Column("icui_pct", sa.Numeric(14, 6)),
        sa.Column("ibua", sa.Numeric(18, 4)),
        sa.Column("old_cost_with_tax", sa.Numeric(18, 4)),
        sa.Column("old_cost", sa.Numeric(18, 4)),
        sa.Column("new_cost", sa.Numeric(18, 4)),
        sa.Column("new_cost_with_tax", sa.Numeric(18, 4)),
        sa.Column("variation_amount", sa.Numeric(18, 4)),
        sa.Column("variation_pct", sa.Numeric(18, 6)),
        sa.Column("status", sa.String(10)),
        sa.Column("observations", sa.Text()),
        sa.Column("margin_pct", sa.Numeric(18, 6)),
        sa.Column("price", sa.Numeric(18, 4)),
        sa.Column("notified", sa.Boolean(), nullable=False),
        sa.Column("siesa_changed", sa.Boolean(), nullable=False),
        sa.Column("updated_by", sa.String(320), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint("row_key", name="product_price_increases_row_key_key"),
    )
    op.create_index(
        "ix_product_price_increases_sku", "product_price_increases", ["sku", "increased_on"]
    )
    op.create_index("ix_product_price_increases_status", "product_price_increases", ["status"])
    op.create_index(
        "ix_product_price_increases_pending",
        "product_price_increases",
        ["sku"],
        postgresql_where=sa.text("system_updated_on IS NULL"),
    )
    op.add_column("quotation_lines", sa.Column("increase_cost", sa.Numeric(18, 4)))
    op.add_column(
        "quotations",
        sa.Column("with_history", sa.Boolean(), nullable=False, server_default=sa.true()),
    )
    op.alter_column("quotations", "with_history", server_default=None)


def downgrade() -> None:
    op.drop_column("quotations", "with_history")
    op.drop_column("quotation_lines", "increase_cost")
    op.drop_index("ix_product_price_increases_pending", table_name="product_price_increases")
    op.drop_index("ix_product_price_increases_status", table_name="product_price_increases")
    op.drop_index("ix_product_price_increases_sku", table_name="product_price_increases")
    op.drop_table("product_price_increases")
