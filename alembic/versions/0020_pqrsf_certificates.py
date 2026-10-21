"""Certificates sent automatically when a customer asks for them.

``pqrsf_certificates`` links a document type of the public form to a file of the quality
document manager. A request for a type that has an active certificate is answered with that
file and no case is opened; ``pqrsf_document_deliveries`` is the log of those answers.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0020_pqrsf_certificates"
down_revision: str | None = "0019_pqrsf_feedback"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create the certificate catalog and the delivery log."""

    op.create_table(
        "pqrsf_certificates",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "document_type_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("pqrsf_document_types.id", ondelete="CASCADE"),
            nullable=False,
        ),
        # SET NULL: a file removed from the manager leaves the certificate without a file
        # (it is not sent automatically) instead of blocking the removal.
        sa.Column(
            "document_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("documents.id", ondelete="SET NULL"),
        ),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("updated_by", postgresql.UUID(as_uuid=True)),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.UniqueConstraint("document_type_id", name="pqrsf_certificates_document_type_id_key"),
    )

    op.create_table(
        "pqrsf_document_deliveries",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "request_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("pqrsf_requests.id", ondelete="SET NULL"),
        ),
        sa.Column(
            "site_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("pqrsf_sites.id", ondelete="SET NULL"),
        ),
        sa.Column("full_name", sa.String(200), nullable=False),
        sa.Column("business_name", sa.String(200)),
        sa.Column("nit", sa.String(30)),
        sa.Column("email", sa.String(320), nullable=False),
        # [{document_type_id, name, document_id, filename, size_bytes}]: what was sent.
        sa.Column("items", postgresql.JSONB(), nullable=False, server_default="[]"),
        # Names of what the same request asked for that a person answers in a case.
        sa.Column("manual_items", postgresql.JSONB(), nullable=False, server_default="[]"),
        sa.Column("case_code", sa.String(20)),
        sa.Column("notification_ids", postgresql.JSONB(), nullable=False, server_default="[]"),
        sa.Column("read_at", sa.DateTime(timezone=True)),
        sa.Column("read_by_user_id", postgresql.UUID(as_uuid=True)),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.UniqueConstraint("request_id", name="pqrsf_document_deliveries_request_id_key"),
    )
    op.create_index(
        "ix_pqrsf_document_deliveries_created", "pqrsf_document_deliveries", ["created_at"]
    )
    op.create_index("ix_pqrsf_document_deliveries_email", "pqrsf_document_deliveries", ["email"])


def downgrade() -> None:
    """Drop the delivery log and the certificate catalog."""

    op.drop_index("ix_pqrsf_document_deliveries_email", table_name="pqrsf_document_deliveries")
    op.drop_index("ix_pqrsf_document_deliveries_created", table_name="pqrsf_document_deliveries")
    op.drop_table("pqrsf_document_deliveries")
    op.drop_table("pqrsf_certificates")
