"""Create local identity, roles, and organization areas."""

from collections.abc import Sequence
from datetime import UTC, datetime
from uuid import UUID

import sqlalchemy as sa
from alembic import op

revision: str = "0002_identity"
down_revision: str | None = "0001_initial"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

ADMIN_ROLE_ID = UUID("8b2f5c0d-311f-49fa-9c84-7b9d8be55d1a")
USER_ROLE_ID = UUID("f15e1f8f-2b76-4c1d-86f7-54c9d3b8ae7e")


def upgrade() -> None:
    """Create identity tables and seed the two initial platform roles."""

    op.create_table(
        "roles",
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("code", sa.String(length=50), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("code"),
    )
    op.create_table(
        "areas",
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("tenant_id", sa.String(length=100), nullable=False),
        sa.Column("name", sa.String(length=150), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "name", name="uq_areas_tenant_name"),
    )
    op.create_index("ix_areas_tenant_id", "areas", ["tenant_id"], unique=False)
    op.create_table(
        "users",
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("tenant_id", sa.String(length=100), nullable=False),
        sa.Column("entra_object_id", sa.String(length=100), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("email", sa.String(length=320), nullable=True),
        sa.Column("job_title", sa.String(length=200), nullable=True),
        sa.Column("role_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("area_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("photo_key", sa.String(length=500), nullable=True),
        sa.Column("photo_content_type", sa.String(length=100), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["area_id"], ["areas.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["role_id"], ["roles.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "entra_object_id", name="uq_users_tenant_entra_object"),
    )
    op.create_index("ix_users_tenant_id", "users", ["tenant_id"], unique=False)
    op.create_index("ix_users_email", "users", ["email"], unique=False)
    roles = sa.table(
        "roles",
        sa.column("id", sa.Uuid(as_uuid=True)),
        sa.column("code", sa.String()),
        sa.column("name", sa.String()),
        sa.column("created_at", sa.DateTime(timezone=True)),
    )
    op.bulk_insert(
        roles,
        [
            {
                "id": ADMIN_ROLE_ID,
                "code": "admin",
                "name": "Administrator",
                "created_at": datetime.now(UTC),
            },
            {
                "id": USER_ROLE_ID,
                "code": "user",
                "name": "User",
                "created_at": datetime.now(UTC),
            },
        ],
    )


def downgrade() -> None:
    """Drop users, areas, and roles in foreign-key-safe order."""

    op.drop_index("ix_users_email", table_name="users")
    op.drop_index("ix_users_tenant_id", table_name="users")
    op.drop_table("users")
    op.drop_index("ix_areas_tenant_id", table_name="areas")
    op.drop_table("areas")
    op.drop_table("roles")
