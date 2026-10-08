"""Add PQRSF notifications: the email outbox, organization timers and daily job runs.

``pqrsf_notifications`` queues every email sent from the PQRSF mailbox (assignment
notices, the end-of-day summary, the overdue reminder) so a Graph outage never loses
one and a retry never sends it twice (``dedupe_key``). ``pqrsf_settings`` keeps the
organization-wide timers as JSON. ``pqrsf_job_runs`` has one row per job and day.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0017_pqrsf_notifications"
down_revision: str | None = "0016_pqrsf_email"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create the notification tables."""

    op.create_table(
        "pqrsf_notifications",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("kind", sa.String(30), nullable=False),
        sa.Column("recipient_email", sa.String(320), nullable=False),
        sa.Column("recipient_user_id", postgresql.UUID(as_uuid=True)),
        sa.Column("subject", sa.String(300), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column(
            "case_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("pqrsf_cases.id", ondelete="SET NULL"),
        ),
        sa.Column("dedupe_key", sa.String(200)),
        sa.Column("status", sa.String(10), server_default="pending", nullable=False),
        sa.Column("attempts", sa.Integer(), server_default="0", nullable=False),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True)),
        sa.Column("sent_at", sa.DateTime(timezone=True)),
        sa.Column("error", sa.Text()),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.UniqueConstraint("dedupe_key", name="pqrsf_notifications_dedupe_key_key"),
        sa.CheckConstraint(
            "status IN ('pending', 'sent', 'failed')", name="ck_pqrsf_notifications_status"
        ),
    )
    op.create_index(
        "ix_pqrsf_notifications_due", "pqrsf_notifications", ["status", "next_attempt_at"]
    )
    op.create_table(
        "pqrsf_settings",
        sa.Column("key", sa.String(60), primary_key=True),
        sa.Column("value", postgresql.JSONB(), nullable=False),
        sa.Column("updated_by", postgresql.UUID(as_uuid=True)),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )
    op.create_table(
        "pqrsf_job_runs",
        sa.Column("job", sa.String(40), primary_key=True),
        sa.Column("run_date", sa.Date(), primary_key=True),
        sa.Column(
            "ran_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )


def downgrade() -> None:
    """Drop the notification tables."""

    op.drop_table("pqrsf_job_runs")
    op.drop_table("pqrsf_settings")
    op.drop_index("ix_pqrsf_notifications_due", table_name="pqrsf_notifications")
    op.drop_table("pqrsf_notifications")
