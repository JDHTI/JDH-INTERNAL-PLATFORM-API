"""Publication to the DWH (``dwh_outbox``), support loads and the DWH cost of each support.

- ``dwh_outbox``: every change the platform publishes to the warehouse, written in the same
  transaction as the change. A worker sends it with retries; rows are kept after they are
  published, so the table is also the local version history (the backup in the platform).
- ``supplier_support_loads``: each bulk load (file, hash, who, when, what it did).
- ``supplier_supports.last_load_id`` / ``last_origin``: which load or action last wrote it.
- ``supplier_support_costs``: the cost of each support from the DWH with the quoter's rule.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0057_dwh_outbox_support_loads"
down_revision: str | None = "0056_price_increase_dates"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TZ = sa.DateTime(timezone=True)
_UUID = postgresql.UUID(as_uuid=True)


def upgrade() -> None:
    """Create the outbox, the loads, the cost snapshot and the trace columns."""

    op.create_table(
        "dwh_outbox",
        sa.Column("id", sa.BigInteger, sa.Identity(always=False), primary_key=True),
        sa.Column("topic", sa.String(60), nullable=False),
        sa.Column("entity_id", _UUID, nullable=False),
        sa.Column("version", sa.Integer, nullable=False),
        sa.Column("payload", postgresql.JSONB, nullable=False),
        sa.Column("status", sa.String(12), nullable=False, server_default="pendiente"),
        sa.Column("attempts", sa.Integer, nullable=False, server_default="0"),
        sa.Column("next_attempt_at", _TZ, nullable=False, server_default=sa.func.now()),
        sa.Column("locked_until", _TZ),
        sa.Column("last_error", sa.Text),
        sa.Column("created_at", _TZ, nullable=False, server_default=sa.func.now()),
        sa.Column("published_at", _TZ),
        sa.CheckConstraint(
            "status IN ('pendiente', 'publicado', 'fallido')", name="ck_dwh_outbox_status"
        ),
        sa.UniqueConstraint("topic", "entity_id", "version", name="uq_dwh_outbox_version"),
    )
    op.create_index(
        "ix_dwh_outbox_due",
        "dwh_outbox",
        ["next_attempt_at", "id"],
        postgresql_where=sa.text("status = 'pendiente'"),
    )
    op.create_index("ix_dwh_outbox_entity", "dwh_outbox", ["topic", "entity_id", "version"])

    op.create_table(
        "supplier_support_loads",
        sa.Column("id", _UUID, primary_key=True),
        sa.Column("file_name", sa.Text, nullable=False),
        sa.Column("file_sha256", sa.String(64), nullable=False),
        sa.Column("supplier_code", sa.Text),
        sa.Column("sheet", sa.Text),
        sa.Column("actor", sa.String(320), nullable=False),
        sa.Column("created_at", _TZ, nullable=False, server_default=sa.func.now()),
        sa.Column("rows_read", sa.Integer, nullable=False),
        sa.Column("created", sa.Integer, nullable=False),
        sa.Column("updated", sa.Integer, nullable=False),
        sa.Column("unchanged", sa.Integer, nullable=False),
        sa.Column("missing", sa.Integer, nullable=False),
        sa.Column("expired", sa.Integer, nullable=False),
    )
    op.create_index("ix_supplier_support_loads_created", "supplier_support_loads", ["created_at"])

    op.add_column(
        "supplier_supports",
        sa.Column(
            "last_load_id",
            _UUID,
            sa.ForeignKey("supplier_support_loads.id", ondelete="SET NULL"),
        ),
    )
    op.add_column("supplier_supports", sa.Column("last_origin", sa.String(20)))

    op.create_table(
        "supplier_support_costs",
        sa.Column(
            "support_id",
            _UUID,
            sa.ForeignKey("supplier_supports.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("product_sku", sa.Text, nullable=False),
        sa.Column("plan_key", sa.Text, nullable=False),
        sa.Column("plan_label", sa.Text, nullable=False),
        sa.Column("unit_cost", sa.Numeric(18, 4)),
        sa.Column("source", sa.String(12)),
        sa.Column("quote_cost", sa.Numeric(18, 4)),
        sa.Column("inventory_cost", sa.Numeric(18, 4)),
        sa.Column("snapshot_date", sa.Date),
        sa.Column("computed_at", _TZ, nullable=False),
    )


def downgrade() -> None:
    """Drop them (the outbox history is lost)."""

    op.drop_table("supplier_support_costs")
    op.drop_column("supplier_supports", "last_origin")
    op.drop_column("supplier_supports", "last_load_id")
    op.drop_index("ix_supplier_support_loads_created", table_name="supplier_support_loads")
    op.drop_table("supplier_support_loads")
    op.drop_index("ix_dwh_outbox_entity", table_name="dwh_outbox")
    op.drop_index("ix_dwh_outbox_due", table_name="dwh_outbox")
    op.drop_table("dwh_outbox")
