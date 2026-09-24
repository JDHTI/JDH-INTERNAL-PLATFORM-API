"""Add the local user activity flag."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0005_user_activity"
down_revision: str | None = "0004_documents"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add a non-null activity flag, treating existing users as active."""

    op.add_column(
        "users",
        sa.Column("is_active", sa.Boolean(), server_default=sa.true(), nullable=False),
    )


def downgrade() -> None:
    """Remove the activity flag."""

    op.drop_column("users", "is_active")
