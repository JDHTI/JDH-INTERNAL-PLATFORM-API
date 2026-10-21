"""Answer a requested document of a case with a file, sent to the client by email.

``pqrsf_document_types.allows_direct_upload`` says which documents may be answered with a
file that goes straight to the case and the email without passing through the document
manager (those of the category answered by a person start with it on). ``pqrsf_item_deliveries``
records each answer: the manager file that was linked, or the file that was uploaded.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0021_pqrsf_item_deliveries"
down_revision: str | None = "0020_pqrsf_certificates"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add the flag of a document type and the log of item answers."""

    op.add_column(
        "pqrsf_document_types",
        sa.Column("allows_direct_upload", sa.Boolean(), nullable=False, server_default="false"),
    )
    op.execute(
        "UPDATE pqrsf_document_types SET allows_direct_upload = true WHERE category_id IN "
        "(SELECT id FROM pqrsf_document_categories WHERE code = 'otro')"
    )
    op.create_table(
        "pqrsf_item_deliveries",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "case_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("pqrsf_cases.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "item_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("pqrsf_case_items.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("source", sa.String(10), nullable=False),
        sa.Column(
            "document_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("documents.id", ondelete="SET NULL"),
        ),
        sa.Column(
            "attachment_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("pqrsf_case_attachments.id", ondelete="SET NULL"),
        ),
        sa.Column("filename", sa.String(255), nullable=False),
        sa.Column("notification_id", postgresql.UUID(as_uuid=True)),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint("source IN ('vault', 'upload')", name="ck_pqrsf_item_deliveries_source"),
    )
    op.create_index("ix_pqrsf_item_deliveries_case", "pqrsf_item_deliveries", ["case_id"])
    op.create_index("ix_pqrsf_item_deliveries_item", "pqrsf_item_deliveries", ["item_id"])


def downgrade() -> None:
    """Drop the log and the flag."""

    op.drop_index("ix_pqrsf_item_deliveries_item", table_name="pqrsf_item_deliveries")
    op.drop_index("ix_pqrsf_item_deliveries_case", table_name="pqrsf_item_deliveries")
    op.drop_table("pqrsf_item_deliveries")
    op.drop_column("pqrsf_document_types", "allows_direct_upload")
