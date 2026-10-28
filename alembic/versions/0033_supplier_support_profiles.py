"""Supplier support profiles: per-supplier defaults and file column mapping.

- ``supplier_support_profiles``: one row per commercial supplier with the defaults that
  pre-fill a new support (calc method, unit, regionals...) and the headers of the supplier's own
  file for each field, so a bulk load reads the file as the supplier sends it.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0033_supplier_support_profiles"
down_revision: str | None = "0032_liquor_tax_reason"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create the profiles table."""

    op.create_table(
        "supplier_support_profiles",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "commercial_supplier_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("commercial_suppliers.id", ondelete="CASCADE"),
            nullable=False,
            unique=True,
        ),
        sa.Column(
            "defaults",
            postgresql.JSONB,
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "column_map",
            postgresql.JSONB,
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("sheet", sa.Text),
        sa.Column("notes", sa.Text),
        sa.Column("created_by", sa.String(320), nullable=False),
        sa.Column("updated_by", sa.String(320), nullable=False),
        sa.Column("version", sa.Integer, nullable=False, server_default="1"),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )


def downgrade() -> None:
    """Drop the profiles table; the profiles are not recoverable."""

    op.drop_table("supplier_support_profiles")
