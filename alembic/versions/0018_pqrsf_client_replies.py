"""Link case files to the comment they came with and carry client-reply data in the outbox.

``pqrsf_case_attachments.comment_event_id`` ties a file to the timeline comment it was
attached to (empty for files that arrived by email). ``pqrsf_notifications.payload``
holds what a partial reply to the client needs beyond its text: the comment event, the
files to attach and the message of the client's conversation to answer inside of.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0018_pqrsf_client_replies"
down_revision: str | None = "0017_pqrsf_notifications"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add the comment link and the outbox payload."""

    op.add_column(
        "pqrsf_case_attachments",
        sa.Column(
            "comment_event_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("pqrsf_case_events.id", ondelete="SET NULL"),
        ),
    )
    op.create_index(
        "ix_pqrsf_case_attachments_comment", "pqrsf_case_attachments", ["comment_event_id"]
    )
    op.add_column(
        "pqrsf_notifications",
        sa.Column("payload", postgresql.JSONB(), server_default="{}", nullable=False),
    )
    op.create_index("ix_pqrsf_notifications_case", "pqrsf_notifications", ["case_id", "kind"])


def downgrade() -> None:
    """Remove the comment link and the outbox payload."""

    op.drop_index("ix_pqrsf_notifications_case", table_name="pqrsf_notifications")
    op.drop_column("pqrsf_notifications", "payload")
    op.drop_index("ix_pqrsf_case_attachments_comment", table_name="pqrsf_case_attachments")
    op.drop_column("pqrsf_case_attachments", "comment_event_id")
