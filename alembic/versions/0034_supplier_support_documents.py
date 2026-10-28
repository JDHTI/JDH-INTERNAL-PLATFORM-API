"""Supporting documents of supplier supports.

- ``supplier_support_documents``: links a support to a document of the document manager (the
  proof of the agreement, mostly screenshots of e-mails). Many to many; deleting either side
  removes the link, never the other side.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0034_supplier_support_documents"
down_revision: str | None = "0033_supplier_support_profiles"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create the links table."""

    op.create_table(
        "supplier_support_documents",
        sa.Column(
            "support_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("supplier_supports.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "document_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("documents.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("linked_by", sa.String(320), nullable=False),
        sa.Column(
            "linked_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )
    op.create_index(
        "ix_supplier_support_documents_document", "supplier_support_documents", ["document_id"]
    )


def downgrade() -> None:
    """Drop the links; the documents stay in the document manager."""

    op.drop_index("ix_supplier_support_documents_document", table_name="supplier_support_documents")
    op.drop_table("supplier_support_documents")
