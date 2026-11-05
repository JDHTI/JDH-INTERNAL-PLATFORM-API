"""Who holds each SIESA salesperson or supervisor code (``salesperson_links``).

One row per ``(kind, code)``: ``auto`` (by e-mail), ``manual`` (an administrator) or
``excluded`` (unlinked by hand, so the e-mail never links it again; no user). The rows are
filled by ``POST /salespeople/sync`` and the panel, not by this migration.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0045_salesperson_links"
down_revision: str | None = "0044_area_leader_role"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TZ = sa.DateTime(timezone=True)
_UUID = postgresql.UUID(as_uuid=True)


def upgrade() -> None:
    """Create the links table."""

    op.create_table(
        "salesperson_links",
        sa.Column("id", _UUID, primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("kind", sa.String(12), nullable=False),
        sa.Column("code", sa.String(20), nullable=False),
        sa.Column("user_id", _UUID, sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=True),
        sa.Column("source", sa.String(10), nullable=False),
        sa.Column(
            "created_by_id", _UUID, sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True
        ),
        sa.Column("created_at", _TZ, server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", _TZ, server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("kind", "code", name="uq_salesperson_links_kind_code"),
        sa.CheckConstraint("kind IN ('seller', 'supervisor')", name="ck_salesperson_links_kind"),
        sa.CheckConstraint(
            "source IN ('auto', 'manual', 'excluded')", name="ck_salesperson_links_source"
        ),
        sa.CheckConstraint(
            "(source = 'excluded') = (user_id IS NULL)", name="ck_salesperson_links_user"
        ),
    )
    op.create_index("ix_salesperson_links_user_id", "salesperson_links", ["user_id"])


def downgrade() -> None:
    """Drop the links table."""

    op.drop_index("ix_salesperson_links_user_id", table_name="salesperson_links")
    op.drop_table("salesperson_links")
