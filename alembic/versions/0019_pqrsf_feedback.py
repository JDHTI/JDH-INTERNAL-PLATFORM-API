"""Suggestions and compliments: kept apart from the cases and answered automatically.

``pqrsf_request_types.handling`` decides what a request of a type becomes: ``case`` opens a
follow-up case, ``acknowledge`` is stored in ``pqrsf_feedback`` and answered at once with a
generic thank-you. Suggestions and compliments become ``acknowledge``. Cases they already
opened stay as they are.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0019_pqrsf_feedback"
down_revision: str | None = "0018_pqrsf_client_replies"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add the handling of a type, the feedback table and its files."""

    op.add_column(
        "pqrsf_request_types",
        sa.Column("handling", sa.String(12), nullable=False, server_default="case"),
    )
    op.create_check_constraint(
        "ck_pqrsf_request_types_handling",
        "pqrsf_request_types",
        "handling IN ('case', 'acknowledge')",
    )
    op.execute(
        "UPDATE pqrsf_request_types SET handling = 'acknowledge', requires_follow_up = false, "
        "sla_days = NULL WHERE code IN ('sugerencia', 'felicitacion')"
    )

    op.create_table(
        "pqrsf_feedback",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "request_type_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("pqrsf_request_types.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("origin", sa.String(12), nullable=False),
        sa.Column(
            "request_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("pqrsf_requests.id", ondelete="SET NULL"),
        ),
        sa.Column(
            "email_message_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("pqrsf_email_messages.id", ondelete="SET NULL"),
        ),
        sa.Column(
            "site_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("pqrsf_sites.id", ondelete="SET NULL"),
        ),
        sa.Column("full_name", sa.String(200), nullable=False),
        sa.Column("business_name", sa.String(200)),
        sa.Column("email", sa.String(320), nullable=False),
        sa.Column("subject", sa.String(200)),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("reply_notification_id", postgresql.UUID(as_uuid=True)),
        sa.Column("reply_skipped_reason", sa.String(40)),
        sa.Column("read_at", sa.DateTime(timezone=True)),
        sa.Column("read_by_user_id", postgresql.UUID(as_uuid=True)),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.UniqueConstraint("request_id", name="pqrsf_feedback_request_id_key"),
        sa.UniqueConstraint("email_message_id", name="pqrsf_feedback_email_message_id_key"),
        sa.CheckConstraint("origin IN ('public_form', 'email')", name="ck_pqrsf_feedback_origin"),
    )
    op.create_index(
        "ix_pqrsf_feedback_type_created", "pqrsf_feedback", ["request_type_id", "created_at"]
    )
    op.create_index("ix_pqrsf_feedback_created", "pqrsf_feedback", ["created_at"])
    op.create_index("ix_pqrsf_feedback_email", "pqrsf_feedback", ["email"])

    op.create_table(
        "pqrsf_feedback_files",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "feedback_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("pqrsf_feedback.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("original_filename", sa.String(255), nullable=False),
        sa.Column("extension", sa.String(10), nullable=False),
        sa.Column("content_type", sa.String(127), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("checksum_sha256", sa.String(64), nullable=False),
        sa.Column("storage_key", sa.String(512), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )
    op.create_index("ix_pqrsf_feedback_files_feedback", "pqrsf_feedback_files", ["feedback_id"])
    op.create_index(
        "ix_pqrsf_notifications_recipient",
        "pqrsf_notifications",
        ["kind", "recipient_email", "created_at"],
    )


def downgrade() -> None:
    """Remove the feedback tables and the handling of a type."""

    op.drop_index("ix_pqrsf_notifications_recipient", table_name="pqrsf_notifications")
    op.drop_index("ix_pqrsf_feedback_files_feedback", table_name="pqrsf_feedback_files")
    op.drop_table("pqrsf_feedback_files")
    op.drop_index("ix_pqrsf_feedback_email", table_name="pqrsf_feedback")
    op.drop_index("ix_pqrsf_feedback_created", table_name="pqrsf_feedback")
    op.drop_index("ix_pqrsf_feedback_type_created", table_name="pqrsf_feedback")
    op.drop_table("pqrsf_feedback")
    op.drop_constraint("ck_pqrsf_request_types_handling", "pqrsf_request_types", type_="check")
    op.drop_column("pqrsf_request_types", "handling")
