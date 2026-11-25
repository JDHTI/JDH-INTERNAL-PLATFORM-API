"""SIESA loads of pending increases (2026-11-25).

Whoever manages the history of increases generates the supplier quotation file (Impodatos
``0212``) of pending increases, loads it in SIESA and confirms it, which dates them.

- ``price_increase_siesa_loads``: a generated load, ``generado`` → ``cargado`` or ``anulado``.
- ``price_increase_siesa_load_rows``: each increase as it went to SIESA (supplier, branch,
  unit, price, activation), so the files are written again byte for byte.

Renumbered from ``0089`` and chained after ``0089_quotation_warehouse_groups`` (2026-11-25):
both were merged on top of ``0088`` and master had two heads. Production never applied it.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision: str = "0090_price_increase_siesa_loads"
down_revision: str | Sequence[str] | None = "0089_quotation_warehouse_groups"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "price_increase_siesa_loads",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("number", sa.Integer(), nullable=False, unique=True),
        sa.Column("status", sa.String(12), nullable=False),
        sa.Column("row_count", sa.Integer(), nullable=False),
        sa.Column("created_by", sa.String(320), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("confirmed_by", sa.String(320)),
        sa.Column("confirmed_at", sa.DateTime(timezone=True)),
        sa.Column("loaded_on", sa.Date()),
        sa.Column("voided_by", sa.String(320)),
        sa.Column("voided_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint(
            "status IN ('generado','cargado','anulado')",
            name="ck_price_increase_siesa_loads_status",
        ),
    )
    op.create_index(
        "ix_price_increase_siesa_loads_status",
        "price_increase_siesa_loads",
        ["status", "created_at"],
    )
    op.create_table(
        "price_increase_siesa_load_rows",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "load_id",
            UUID(as_uuid=True),
            sa.ForeignKey("price_increase_siesa_loads.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "increase_id",
            UUID(as_uuid=True),
            sa.ForeignKey("product_price_increases.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("sku", sa.Text(), nullable=False),
        sa.Column("description", sa.Text()),
        sa.Column("nit", sa.String(15), nullable=False),
        sa.Column("branch", sa.String(3), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("unit", sa.String(4), nullable=False),
        sa.Column("price", sa.Numeric(19, 4), nullable=False),
        sa.Column("activation_date", sa.Date(), nullable=False),
        sa.Column("delivery_days", sa.Integer(), nullable=False),
        sa.Column("increased_on", sa.Date()),
        sa.Column("other_nits", sa.Text()),
    )
    op.create_index(
        "ix_price_increase_siesa_load_rows_load",
        "price_increase_siesa_load_rows",
        ["load_id", "position"],
    )
    op.create_index(
        "ix_price_increase_siesa_load_rows_increase",
        "price_increase_siesa_load_rows",
        ["increase_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_price_increase_siesa_load_rows_increase", "price_increase_siesa_load_rows")
    op.drop_index("ix_price_increase_siesa_load_rows_load", "price_increase_siesa_load_rows")
    op.drop_table("price_increase_siesa_load_rows")
    op.drop_index("ix_price_increase_siesa_loads_status", "price_increase_siesa_loads")
    op.drop_table("price_increase_siesa_loads")
