"""Every area has one leader role: all its permissions, never deleted, never narrowed.

- ``area_roles.is_leader`` marks it; a partial unique index keeps one per area.
- Existing data: the role whose name says "líder" or "director" becomes the leader
  (BI's "Director BI"); otherwise the "Acceso completo" role, renamed "Líder de área";
  otherwise a new "Líder de área". Its grants become exactly ``*`` manage.
- The other roles keep their grants: they stay editable.
"""

from collections.abc import Sequence
from uuid import uuid4

import sqlalchemy as sa
from alembic import op

revision: str = "0044_area_leader_role"
down_revision: str | None = "0043_notifications"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

LEADER_ROLE = "Líder de área"
FULL_ACCESS_ROLE = "Acceso completo"


def upgrade() -> None:
    """Add the leader flag and give every area its leader role."""

    op.add_column(
        "area_roles",
        sa.Column("is_leader", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.create_index(
        "uq_area_roles_one_leader",
        "area_roles",
        ["area_id"],
        unique=True,
        postgresql_where=sa.text("is_leader"),
    )

    bind = op.get_bind()
    for area_id in bind.execute(sa.text("SELECT id FROM areas")).scalars().all():
        roles = bind.execute(
            sa.text("SELECT id, name FROM area_roles WHERE area_id = :area ORDER BY created_at"),
            {"area": area_id},
        ).all()
        by_name = {name.lower(): role_id for role_id, name in roles}
        leader = next(
            (
                role_id
                for role_id, name in roles
                if any(word in name.lower() for word in ("líder", "lider", "director"))
            ),
            None,
        )
        if leader is None and FULL_ACCESS_ROLE.lower() in by_name:
            leader = by_name[FULL_ACCESS_ROLE.lower()]
            if LEADER_ROLE.lower() not in by_name:
                bind.execute(
                    sa.text("UPDATE area_roles SET name = :name WHERE id = :id"),
                    {"name": LEADER_ROLE, "id": leader},
                )
        if leader is None:
            leader = uuid4()
            bind.execute(
                sa.text(
                    "INSERT INTO area_roles (id, area_id, name, created_at, updated_at) "
                    "VALUES (:id, :area, :name, now(), now())"
                ),
                {"id": leader, "area": area_id, "name": LEADER_ROLE},
            )
        bind.execute(
            sa.text("UPDATE area_roles SET is_leader = true WHERE id = :id"), {"id": leader}
        )
        bind.execute(
            sa.text("DELETE FROM area_role_permissions WHERE role_id = :id"), {"id": leader}
        )
        bind.execute(
            sa.text(
                "INSERT INTO area_role_permissions (id, role_id, module, action, created_at, "
                "updated_at) VALUES (:id, :role, '*', 'manage', now(), now())"
            ),
            {"id": uuid4(), "role": leader},
        )


def downgrade() -> None:
    """Drop the flag; the leader roles stay as ordinary roles with their grants."""

    op.drop_index("uq_area_roles_one_leader", table_name="area_roles")
    op.drop_column("area_roles", "is_leader")
