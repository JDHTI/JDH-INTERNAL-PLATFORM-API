"""Workspace permissions: owner and moderator roles, area role grants and their log.

- ``roles`` gains ``owner`` (exactly one, enforced by the partial unique index
  ``uq_users_single_owner``) and ``moderator``.
- ``area_role_permissions``: one row per (area role, module) with ``read`` or ``manage``;
  module ``*`` is the whole area.
- ``permission_events``: who changed roles, grants, members and global roles.

Data:
- Every area gets the role "Acceso completo" (``*`` manage) when it has none by that name.
- **Every existing area role gets ``*`` manage.** Until now any member managed every
  feature of their area (``AreaManagerPolicy`` was "admin or member"), so this keeps the
  deploy a no-op; administrators then narrow the roles from the permissions panel.
- The user with ``PLATFORM_OWNER_EMAIL`` becomes the owner when it exists; otherwise
  run ``scripts/set-owner.py`` after their first sign-in or directory sync.
"""

from collections.abc import Sequence
from uuid import uuid4

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

from app.core.config import settings

revision: str = "0041_permissions"
down_revision: str | None = "0040_directory"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TZ = sa.DateTime(timezone=True)
_UUID = postgresql.UUID(as_uuid=True)

# Same values as app/modules/identity/domain/roles.py (a migration never imports app code
# that may change later, except settings).
ADMIN_ROLE_ID = "8b2f5c0d-311f-49fa-9c84-7b9d8be55d1a"
USER_ROLE_ID = "f15e1f8f-2b76-4c1d-86f7-54c9d3b8ae7e"
OWNER_ROLE_ID = "5d0b6a52-7f8e-4c63-9d1a-0b3f4e2c8a71"
MODERATOR_ROLE_ID = "c47e9f1d-28a3-4b5e-8f60-6d2a91b3e4c5"
FULL_ACCESS_ROLE = "Acceso completo"


def upgrade() -> None:
    """Create the roles, the grants and the log, then backfill them."""

    bind = op.get_bind()
    bind.execute(
        sa.text(
            "INSERT INTO roles (id, code, name, created_at) VALUES "
            "(:owner, 'owner', 'Propietario', now()), "
            "(:moderator, 'moderator', 'Moderador', now()) "
            "ON CONFLICT (code) DO NOTHING"
        ),
        {"owner": OWNER_ROLE_ID, "moderator": MODERATOR_ROLE_ID},
    )
    owner_id = bind.execute(sa.text("SELECT id FROM roles WHERE code = 'owner'")).scalar_one()
    op.create_index(
        "uq_users_single_owner",
        "users",
        ["role_id"],
        unique=True,
        postgresql_where=sa.text(f"role_id = '{owner_id}'"),
    )

    op.create_table(
        "area_role_permissions",
        sa.Column("id", _UUID, primary_key=True),
        sa.Column(
            "role_id", _UUID, sa.ForeignKey("area_roles.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("module", sa.String(60), nullable=False),
        sa.Column("action", sa.String(10), nullable=False),
        sa.Column("created_at", _TZ, nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", _TZ, nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("role_id", "module", name="uq_area_role_permissions_role_module"),
        sa.CheckConstraint("action IN ('read', 'manage')", name="ck_area_role_permissions_action"),
    )
    op.create_index("ix_area_role_permissions_role_id", "area_role_permissions", ["role_id"])

    op.create_table(
        "permission_events",
        sa.Column("id", _UUID, primary_key=True),
        sa.Column("at", _TZ, nullable=False, server_default=sa.func.now()),
        sa.Column("actor_user_id", _UUID, sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("action", sa.String(40), nullable=False),
        sa.Column("area_id", _UUID, sa.ForeignKey("areas.id", ondelete="SET NULL")),
        sa.Column("target_user_id", _UUID, sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("role_id", _UUID),
        sa.Column(
            "detail", postgresql.JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")
        ),
    )
    op.create_index("ix_permission_events_at", "permission_events", ["at"])
    op.create_index("ix_permission_events_target_user_id", "permission_events", ["target_user_id"])

    # Every area gets its "Acceso completo" role.
    areas = bind.execute(sa.text("SELECT id FROM areas")).scalars().all()
    for area_id in areas:
        exists = bind.execute(
            sa.text(
                "SELECT 1 FROM area_roles WHERE area_id = :area AND lower(name) = lower(:name)"
            ),
            {"area": area_id, "name": FULL_ACCESS_ROLE},
        ).first()
        if exists is None:
            bind.execute(
                sa.text(
                    "INSERT INTO area_roles (id, area_id, name, created_at, updated_at) "
                    "VALUES (:id, :area, :name, now(), now())"
                ),
                {"id": uuid4(), "area": area_id, "name": FULL_ACCESS_ROLE},
            )
    # Every existing role keeps what members could do until now: everything in the area.
    for role_id in bind.execute(sa.text("SELECT id FROM area_roles")).scalars().all():
        bind.execute(
            sa.text(
                "INSERT INTO area_role_permissions (id, role_id, module, action, created_at, "
                "updated_at) VALUES (:id, :role, '*', 'manage', now(), now())"
            ),
            {"id": uuid4(), "role": role_id},
        )

    email = (settings.PLATFORM_OWNER_EMAIL or "").strip().lower()
    if email:
        owner = bind.execute(
            sa.text(
                "SELECT id FROM users WHERE lower(email) = :email AND is_active "
                "ORDER BY created_at LIMIT 1"
            ),
            {"email": email},
        ).scalar()
        if owner is not None:
            bind.execute(
                sa.text("UPDATE users SET role_id = :role WHERE id = :user"),
                {"role": owner_id, "user": owner},
            )
        else:
            print(f"0041_permissions: no user {email} yet; run scripts/set-owner.py later")


def downgrade() -> None:
    """Drop the log and the grants, and fold owner into admin and moderator into user."""

    bind = op.get_bind()
    op.drop_table("permission_events")
    op.drop_table("area_role_permissions")
    op.drop_index("uq_users_single_owner", table_name="users")
    admin = bind.execute(sa.text("SELECT id FROM roles WHERE code = 'admin'")).scalar_one()
    user = bind.execute(sa.text("SELECT id FROM roles WHERE code = 'user'")).scalar_one()
    bind.execute(
        sa.text(
            "UPDATE users SET role_id = :admin WHERE role_id IN "
            "(SELECT id FROM roles WHERE code = 'owner')"
        ),
        {"admin": admin},
    )
    bind.execute(
        sa.text(
            "UPDATE users SET role_id = :user WHERE role_id IN "
            "(SELECT id FROM roles WHERE code = 'moderator')"
        ),
        {"user": user},
    )
    bind.execute(sa.text("DELETE FROM roles WHERE code IN ('owner', 'moderator')"))
    # The "Acceso completo" roles stay: they are ordinary area roles.
