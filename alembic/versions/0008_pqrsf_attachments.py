"""Add sanitized PQRSF image metadata."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0008_pqrsf_attachments"
down_revision: str | None = "0007_pqrsf_requests"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create the attachment metadata table."""

    op.create_table(
        "pqrsf_attachments",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "request_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("pqrsf_requests.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("original_filename", sa.String(255), nullable=False),
        sa.Column("extension", sa.String(8), nullable=False),
        sa.Column("content_type", sa.String(64), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("checksum_sha256", sa.String(64), nullable=False),
        sa.Column("storage_key", sa.String(512), nullable=False, unique=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint("size_bytes > 0", name="ck_pqrsf_attachments_size_positive"),
    )
    op.create_index("ix_pqrsf_attachments_request_id", "pqrsf_attachments", ["request_id"])


def downgrade() -> None:
    """Drop attachment metadata; private bucket objects require separate cleanup."""

    op.drop_index("ix_pqrsf_attachments_request_id", table_name="pqrsf_attachments")
    op.drop_table("pqrsf_attachments")
