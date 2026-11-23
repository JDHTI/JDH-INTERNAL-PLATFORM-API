"""The roles of Comercial (2026-11-23): «Coordinador» and «Director».

- **Coordinador:** for now it only coordinates. It reads the customer quotations of its team
  and the rest of the team's work (customers, supports, requests, orders, map), and quotes,
  validates or edits nothing. Its team comes from its SIESA supervisor code, linked in
  ``/ti/administracion/vendedores``.
- **Director:** sees everything in Comercial (``manage`` on ``comercial-director``, granted by
  name) and reads the same modules. It edits nothing.
- **Líder de área:** keeps ``*``, and ``comercial-director`` is written out, so the leader's
  role says it in the administration too.

Idempotent: a role that exists keeps its permissions, and only the missing ones are added. A
database without the Comercial area is left alone.
"""

from collections.abc import Sequence
from uuid import uuid4

import sqlalchemy as sa
from alembic import op

revision: str = "0081_commercial_roles"
down_revision: str | Sequence[str] | None = "0080_costs_novelties"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

AREA = "comercial"
DIRECTOR_KEY = "comercial-director"
# What a coordinator and a director read: the quoter, the supports and their requests, the
# order validator, the commercial map, the masters and the area's documents.
READ_MODULES = (
    "cotizador",
    "apoyos",
    "solicitudes-apoyos",
    "validador-pedidos",
    "mapa-comercial",
    "maestros",
    "documentos",
)
ROLES = {
    "Coordinador": {module: "read" for module in READ_MODULES},
    "Director": {**{module: "read" for module in READ_MODULES}, DIRECTOR_KEY: "manage"},
}


def _role(bind, area_id, name: str):
    return bind.execute(
        sa.text("SELECT id FROM area_roles WHERE area_id = :area AND lower(name) = lower(:name)"),
        {"area": area_id, "name": name},
    ).scalar()


def _grant(bind, role_id, module: str, action: str) -> None:
    bind.execute(
        sa.text(
            "INSERT INTO area_role_permissions (id, role_id, module, action, created_at, "
            "updated_at) VALUES (:id, :role, :module, :action, now(), now()) "
            "ON CONFLICT ON CONSTRAINT uq_area_role_permissions_role_module DO NOTHING"
        ),
        {"id": uuid4(), "role": role_id, "module": module, "action": action},
    )


def upgrade() -> None:
    bind = op.get_bind()
    area_id = bind.execute(
        sa.text("SELECT id FROM areas WHERE slug = :slug"), {"slug": AREA}
    ).scalar()
    if area_id is None:
        return  # created from the administration, with its roles
    for name, grants in ROLES.items():
        role_id = _role(bind, area_id, name)
        if role_id is None:
            role_id = uuid4()
            bind.execute(
                sa.text(
                    "INSERT INTO area_roles (id, area_id, name, is_leader, created_at, "
                    "updated_at) VALUES (:id, :area, :name, false, now(), now())"
                ),
                {"id": role_id, "area": area_id, "name": name},
            )
        for module, action in grants.items():
            _grant(bind, role_id, module, action)
    leader = bind.execute(
        sa.text("SELECT id FROM area_roles WHERE area_id = :area AND is_leader"),
        {"area": area_id},
    ).scalar()
    if leader is not None:
        _grant(bind, leader, DIRECTOR_KEY, "manage")


def downgrade() -> None:
    """The two roles go when nobody holds them; the leader's explicit key goes too (it keeps
    ``*``)."""

    bind = op.get_bind()
    area_id = bind.execute(
        sa.text("SELECT id FROM areas WHERE slug = :slug"), {"slug": AREA}
    ).scalar()
    if area_id is None:
        return
    for name in ROLES:
        bind.execute(
            sa.text(
                "DELETE FROM area_roles r WHERE r.area_id = :area AND lower(r.name) = lower(:name) "
                "AND NOT EXISTS (SELECT 1 FROM area_members m WHERE m.role_id = r.id)"
            ),
            {"area": area_id, "name": name},
        )
    bind.execute(
        sa.text(
            "DELETE FROM area_role_permissions p USING area_roles r WHERE p.role_id = r.id "
            "AND r.area_id = :area AND r.is_leader AND p.module = :module"
        ),
        {"area": area_id, "module": DIRECTOR_KEY},
    )
