"""Add PQRSF by email: received messages, case attachments and the sync cursor.

``pqrsf_email_messages`` holds every message read from the PQRSF mailbox; its
unique Internet message id is what keeps a message from opening two cases.
``pqrsf_case_attachments`` keeps the metadata of files stored in the bucket under
their case. ``pqrsf_mail_sync_state`` stores the Graph delta cursor per mailbox.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0016_pqrsf_email"
down_revision: str | None = "0015_projects"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Allow the ``email`` origin and create the email intake tables."""

    op.drop_constraint("ck_pqrsf_cases_origin", "pqrsf_cases", type_="check")
    op.create_check_constraint(
        "ck_pqrsf_cases_origin",
        "pqrsf_cases",
        "origin IN ('public_form', 'internal', 'import', 'email')",
    )

    op.create_table(
        "pqrsf_email_messages",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("internet_message_id", sa.String(998), nullable=False),
        sa.Column("graph_id", sa.String(512), nullable=False),
        sa.Column("conversation_id", sa.String(512)),
        sa.Column("mailbox", sa.String(320), nullable=False),
        sa.Column("sender_address", sa.String(320), nullable=False),
        sa.Column("sender_name", sa.String(320)),
        sa.Column("subject", sa.Text(), server_default="", nullable=False),
        sa.Column("body_text", sa.Text(), server_default="", nullable=False),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column(
            "case_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("pqrsf_cases.id", ondelete="SET NULL"),
        ),
        sa.Column("attempts", sa.Integer(), server_default="0", nullable=False),
        sa.Column("error", sa.Text()),
        sa.Column("detail", postgresql.JSONB(), server_default="{}", nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.UniqueConstraint("internet_message_id", name="pqrsf_email_messages_internet_id_key"),
        sa.CheckConstraint(
            "status IN ('received', 'created', 'threaded', 'ignored', 'failed')",
            name="ck_pqrsf_email_messages_status",
        ),
    )
    op.create_index(
        "ix_pqrsf_email_messages_conversation", "pqrsf_email_messages", ["conversation_id"]
    )
    op.create_index(
        "ix_pqrsf_email_messages_case", "pqrsf_email_messages", ["case_id", "received_at"]
    )
    op.create_index("ix_pqrsf_email_messages_status", "pqrsf_email_messages", ["status"])

    op.create_table(
        "pqrsf_case_attachments",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "case_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("pqrsf_cases.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "message_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("pqrsf_email_messages.id", ondelete="SET NULL"),
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
        sa.UniqueConstraint("storage_key", name="pqrsf_case_attachments_storage_key_key"),
    )
    op.create_index(
        "ix_pqrsf_case_attachments_case", "pqrsf_case_attachments", ["case_id", "created_at"]
    )

    op.create_table(
        "pqrsf_mail_sync_state",
        sa.Column("mailbox", sa.String(320), primary_key=True),
        sa.Column("delta_link", sa.Text(), nullable=False),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )


def downgrade() -> None:
    """Drop the email intake tables; cases opened by email become internal ones."""

    op.execute("UPDATE pqrsf_cases SET origin = 'internal' WHERE origin = 'email'")
    op.drop_constraint("ck_pqrsf_cases_origin", "pqrsf_cases", type_="check")
    op.create_check_constraint(
        "ck_pqrsf_cases_origin", "pqrsf_cases", "origin IN ('public_form', 'internal', 'import')"
    )
    op.drop_table("pqrsf_mail_sync_state")
    op.drop_index("ix_pqrsf_case_attachments_case", table_name="pqrsf_case_attachments")
    op.drop_table("pqrsf_case_attachments")
    op.drop_index("ix_pqrsf_email_messages_status", table_name="pqrsf_email_messages")
    op.drop_index("ix_pqrsf_email_messages_case", table_name="pqrsf_email_messages")
    op.drop_index("ix_pqrsf_email_messages_conversation", table_name="pqrsf_email_messages")
    op.drop_table("pqrsf_email_messages")
