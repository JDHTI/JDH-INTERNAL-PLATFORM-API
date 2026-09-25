"""Per-area tag colors for the document manager."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0006_document_tag_colors"
down_revision: str | None = "0005_user_activity"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create document_tag_colors, keyed by (area, tag)."""

    op.create_table(
        "document_tag_colors",
        sa.Column(
            "area_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("areas.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("tag", sa.String(50), primary_key=True),
        sa.Column("color", sa.String(7), nullable=False),
        sa.Column("updated_by", sa.String(320), nullable=False),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint("color ~ '^#[0-9a-f]{6}$'", name="ck_document_tag_colors_hex"),
    )


def downgrade() -> None:
    """Drop document_tag_colors; tags on documents are untouched."""

    op.drop_table("document_tag_colors")
