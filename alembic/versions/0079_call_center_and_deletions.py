"""Call center, the increase date of a line, and controlled deletions (2026-11-23).

- ``quotation_lines.increase_on``: the date of the SKU's latest supplier price increase.
- Area ``call-center`` (Call Center), with its leader role and the role «Operador call
  center», which manages the quoter: it quotes any customer and sees only its own
  quotations; the leader sees those of the whole area.
- The append-only guards of the supports' requests and JDH history let a row go only inside
  a transaction that sets ``app.allow_support_delete = 'on'``: the deletion the area leader or
  a platform administrator makes (bulk delete of supports, delete of requests). Updates stay
  forbidden as before.
"""

from collections.abc import Sequence
from uuid import uuid4

import sqlalchemy as sa
from alembic import op

revision: str = "0079_call_center_and_deletions"
down_revision: str | Sequence[str] | None = "0078_supplier_min_margin"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

CALL_CENTER = "call-center"
LEADER_ROLE = "Líder de área"
OPERATOR_ROLE = "Operador call center"

_HISTORY = """CREATE OR REPLACE FUNCTION protect_support_jdh_history() RETURNS trigger
LANGUAGE plpgsql AS $$
    BEGIN
        {allow}RAISE EXCEPTION 'El historial de apoyos JDH es inmutable';
    END $$"""

_REQUEST = """CREATE OR REPLACE FUNCTION protect_support_request() RETURNS trigger
LANGUAGE plpgsql AS $$
    BEGIN
        IF TG_OP = 'DELETE' THEN
            {allow}RAISE EXCEPTION 'Las solicitudes no se eliminan';
        END IF;
        IF OLD.status <> 'pendiente' OR NEW.status NOT IN ('aprobada','rechazada') OR
           (to_jsonb(OLD) - ARRAY['status','version','decided_by','decided_at','decision_reason'])
           IS DISTINCT FROM
           (to_jsonb(NEW) - ARRAY['status','version','decided_by','decided_at','decision_reason'])
        THEN
            RAISE EXCEPTION 'La propuesta es inmutable y solo admite una decisión';
        END IF;
        RETURN NEW;
    END $$"""

_ALLOW_DELETE = (
    "IF TG_OP = 'DELETE' AND current_setting('app.allow_support_delete', true) = 'on' THEN\n"
    "            RETURN OLD;\n"
    "        END IF;\n        "
)


def _call_center() -> None:
    bind = op.get_bind()
    if bind.execute(
        sa.text("SELECT 1 FROM areas WHERE slug = :slug"), {"slug": CALL_CENTER}
    ).first():
        return
    tenant = bind.execute(
        sa.text("SELECT tenant_id FROM areas ORDER BY created_at LIMIT 1")
    ).scalar()
    if tenant is None:
        return  # an empty database: the area is created from the administration
    area, leader, operator = uuid4(), uuid4(), uuid4()
    bind.execute(
        sa.text(
            "INSERT INTO areas (id, tenant_id, name, slug, remit, icon, sort_order, created_at, "
            "updated_at) VALUES (:id, :tenant, 'Call Center', :slug, :remit, 'Headset', "
            "(SELECT coalesce(max(sort_order), 0) + 1 FROM areas), now(), now())"
        ),
        {
            "id": area,
            "tenant": tenant,
            "slug": CALL_CENTER,
            "remit": "Cotizaciones a cualquier cliente desde el call center.",
        },
    )
    for role, name, is_leader, module in (
        (leader, LEADER_ROLE, True, "*"),
        (operator, OPERATOR_ROLE, False, "cotizador"),
    ):
        bind.execute(
            sa.text(
                "INSERT INTO area_roles (id, area_id, name, is_leader, created_at, updated_at) "
                "VALUES (:id, :area, :name, :leader, now(), now())"
            ),
            {"id": role, "area": area, "name": name, "leader": is_leader},
        )
        bind.execute(
            sa.text(
                "INSERT INTO area_role_permissions (id, role_id, module, action, created_at, "
                "updated_at) VALUES (:id, :role, :module, 'manage', now(), now())"
            ),
            {"id": uuid4(), "role": role, "module": module},
        )


def upgrade() -> None:
    op.add_column("quotation_lines", sa.Column("increase_on", sa.Date()))
    _call_center()
    op.execute(_HISTORY.format(allow=_ALLOW_DELETE))
    op.execute(_REQUEST.format(allow=_ALLOW_DELETE))


def downgrade() -> None:
    """The guards forbid every deletion again; the call center area stays (it may have
    members and quotations)."""

    op.execute(_REQUEST.format(allow=""))
    op.execute(_HISTORY.format(allow=""))
    op.drop_column("quotation_lines", "increase_on")
