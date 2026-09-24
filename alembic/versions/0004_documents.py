"""Documents module: folders and the document index (bytes live in S3)."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0004_documents"
down_revision: str | None = "0003_areas"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _timestamps() -> list[sa.Column]:
    return [
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    ]


def upgrade() -> None:
    """Create document_folders and documents."""

    op.create_table(
        "document_folders",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "area_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("areas.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("name", sa.String(150), nullable=False),
        sa.Column("description", sa.Text),
        sa.Column("created_by", sa.String(320), nullable=False),
        *_timestamps(),
        sa.UniqueConstraint("area_id", "id", name="uq_document_folders_area_id_id"),
    )
    op.create_index(
        "uq_document_folders_area_name_lower",
        "document_folders",
        ["area_id", sa.text("lower(name)")],
        unique=True,
    )

    op.create_table(
        "documents",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("area_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("folder_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("original_filename", sa.String(255), nullable=False),
        sa.Column("extension", sa.String(16), nullable=False),
        sa.Column("content_type", sa.String(255), nullable=False),
        sa.Column("size_bytes", sa.BigInteger, nullable=False),
        sa.Column("checksum_sha256", sa.String(64), nullable=False),
        sa.Column("storage_key", sa.String(512), nullable=False),
        sa.Column("description", sa.Text),
        sa.Column(
            "tags",
            postgresql.ARRAY(sa.Text),
            server_default=sa.text("'{}'::text[]"),
            nullable=False,
        ),
        sa.Column("uploaded_by", sa.String(320), nullable=False),
        *_timestamps(),
        sa.UniqueConstraint("storage_key", name="documents_storage_key_key"),
        sa.ForeignKeyConstraint(
            ["area_id", "folder_id"],
            ["document_folders.area_id", "document_folders.id"],
            name="fk_documents_folder_same_area",
        ),
        sa.CheckConstraint("size_bytes >= 0", name="ck_documents_size_non_negative"),
    )
    op.create_index(
        "uq_documents_folder_name_lower",
        "documents",
        ["folder_id", sa.text("lower(name)")],
        unique=True,
    )
    op.create_index("ix_documents_area_id", "documents", ["area_id"])
    op.create_index("ix_documents_tags", "documents", ["tags"], postgresql_using="gin")


def downgrade() -> None:
    """Drop documents and document_folders. Bucket objects are left untouched."""

    op.drop_index("ix_documents_tags", table_name="documents")
    op.drop_index("ix_documents_area_id", table_name="documents")
    op.drop_index("uq_documents_folder_name_lower", table_name="documents")
    op.drop_table("documents")
    op.drop_index("uq_document_folders_area_name_lower", table_name="document_folders")
    op.drop_table("document_folders")
