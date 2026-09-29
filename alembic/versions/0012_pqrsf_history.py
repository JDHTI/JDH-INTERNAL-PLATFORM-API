"""Make the PQRSF timeline the module history: it outlives the cases.

Events keep the consecutive they refer to (``case_code``) and lose only the
link when a case is deleted, so the history shows who deleted what. Module
events, such as opening the inbox, have no case. Indexes serve the module-wide
history (newest first), the 30-minute repeat check of access records and the
90-day purge of those records.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0012_pqrsf_history"
down_revision: str | None = "0011_pqrsf_site_consecutives"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_FOREIGN_KEY = "pqrsf_case_events_case_id_fkey"


def upgrade() -> None:
    """Keep events after their case, copy each consecutive in and index the history."""

    op.add_column("pqrsf_case_events", sa.Column("case_code", sa.String(30)))
    op.execute(
        "UPDATE pqrsf_case_events e SET case_code = c.code "
        "FROM pqrsf_cases c WHERE c.id = e.case_id"
    )
    op.alter_column("pqrsf_case_events", "case_id", nullable=True)
    op.drop_constraint(_FOREIGN_KEY, "pqrsf_case_events", type_="foreignkey")
    op.create_foreign_key(
        _FOREIGN_KEY,
        "pqrsf_case_events",
        "pqrsf_cases",
        ["case_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index("ix_pqrsf_case_events_created", "pqrsf_case_events", ["created_at"])
    op.create_index(
        "ix_pqrsf_case_events_type_actor_created",
        "pqrsf_case_events",
        ["event_type", "actor_user_id", "created_at"],
    )


def downgrade() -> None:
    """Back to a per-case timeline: entries without a case are dropped."""

    op.drop_index("ix_pqrsf_case_events_type_actor_created", table_name="pqrsf_case_events")
    op.drop_index("ix_pqrsf_case_events_created", table_name="pqrsf_case_events")
    op.execute("DELETE FROM pqrsf_case_events WHERE case_id IS NULL")
    op.drop_constraint(_FOREIGN_KEY, "pqrsf_case_events", type_="foreignkey")
    op.create_foreign_key(
        _FOREIGN_KEY,
        "pqrsf_case_events",
        "pqrsf_cases",
        ["case_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.alter_column("pqrsf_case_events", "case_id", nullable=False)
    op.drop_column("pqrsf_case_events", "case_code")
