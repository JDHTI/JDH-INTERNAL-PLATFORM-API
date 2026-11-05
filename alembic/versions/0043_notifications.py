"""Notifications of the general view: notices, who received them and push subscriptions.

- ``notifications``: one row per Strapi publication (and per event reminder), unique by
  ``(source, source_id, idempotency_key)`` so a retry never notifies twice.
- ``notification_recipients``: a notice in one employee's bell, with ``read_at``.
- ``push_subscriptions``: browsers subscribed to Web Push, one per endpoint.

Recipients and subscriptions point to ``directory_employees``: whoever leaves the
directory takes their bell with them.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0043_notifications"
down_revision: str | None = "0042_directory_contact"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TZ = sa.DateTime(timezone=True)
_UUID = postgresql.UUID(as_uuid=True)


def upgrade() -> None:
    """Create the notifications, their recipients and the push subscriptions."""

    op.create_table(
        "notifications",
        sa.Column("id", _UUID, primary_key=True),
        sa.Column("source", sa.String(20), nullable=False),
        sa.Column("source_id", sa.String(255), nullable=False),
        sa.Column("source_slug", sa.String(255)),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("summary", sa.String(500)),
        sa.Column("idempotency_key", sa.String(255), nullable=False),
        sa.Column("audience_type", sa.String(20), nullable=False),
        sa.Column(
            "audience_values",
            postgresql.JSONB,
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column("trigger", sa.String(20), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("scheduled_for", _TZ),
        sa.Column("sent_at", _TZ),
        sa.Column("created_by", sa.String(255)),
        sa.Column("failure_reason", sa.Text),
        sa.Column("created_at", _TZ, nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint(
            "source", "source_id", "idempotency_key", name="uq_notifications_source_key"
        ),
    )
    op.create_index(
        "ix_notifications_status_scheduled_for", "notifications", ["status", "scheduled_for"]
    )
    op.create_table(
        "notification_recipients",
        sa.Column("id", _UUID, primary_key=True),
        sa.Column(
            "notification_id",
            _UUID,
            sa.ForeignKey("notifications.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "employee_id",
            _UUID,
            sa.ForeignKey("directory_employees.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("read_at", _TZ),
        sa.Column("created_at", _TZ, nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint(
            "notification_id", "employee_id", name="uq_notification_recipients_pair"
        ),
    )
    op.create_index(
        "ix_notification_recipients_employee_read",
        "notification_recipients",
        ["employee_id", "read_at"],
    )
    op.create_index(
        "ix_notification_recipients_employee_created",
        "notification_recipients",
        ["employee_id", "created_at"],
    )
    op.create_table(
        "push_subscriptions",
        sa.Column("id", _UUID, primary_key=True),
        sa.Column(
            "employee_id",
            _UUID,
            sa.ForeignKey("directory_employees.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("endpoint", sa.Text, nullable=False, unique=True),
        sa.Column("p256dh", sa.String(255), nullable=False),
        sa.Column("auth", sa.String(255), nullable=False),
        sa.Column("expires_at", _TZ),
        sa.Column("user_agent", sa.String(300)),
        sa.Column("created_at", _TZ, nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", _TZ, nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_push_subscriptions_employee_id", "push_subscriptions", ["employee_id"])


def downgrade() -> None:
    """Drop the push subscriptions, the recipients and the notifications."""

    op.drop_table("push_subscriptions")
    op.drop_table("notification_recipients")
    op.drop_table("notifications")
