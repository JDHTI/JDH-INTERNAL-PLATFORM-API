"""Ajuste de costos: the quoter's cost rules in one place (2026-11-25).

- ``cost_parameters``: the value BI set for a parameter (the cost factor of each list family,
  the negotiation threshold). A parameter without a row keeps its default (the code's and
  ``QUOTATIONS_MAX_POINTS_BELOW_TARGET``), so nothing changes with this migration.
- ``supplier_branch_rules``: which branch of a supplier quotes for each operation center.
  Two rules come in active:

  - Team Foods (860000006): C.O. 001 (Antioquia) with branch 002, its Antioquia list since
    2026-11-19 (4 references, 3.4 to 3.9 % under the general list); the rest with 001.
  - Lactalis (800245795): C.O. 003 (Centro) with branch 003; the rest with 001. It was fixed
    in the code until now; a reference branch 003 does not quote now takes branch 001's
    (it had no quotation cost in Centro).
- ``cost_settings_changes``: every change, before and after, who and when.
"""

from collections.abc import Sequence
from datetime import UTC, datetime
from uuid import uuid4

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0092_cost_settings"
down_revision: str | Sequence[str] | None = "0091_list_update_excluded_skus"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_ACTOR = "migracion-0092"
_RULES = (
    (
        "860000006",
        "TEAM FOODS COLOMBIA S.A.",
        {"001": "002"},
        "Lista de Antioquia en la sucursal 002 desde el 2026-11-19 (DWH, 2026-11-25). "
        "Pedido de Daniel.",
    ),
    (
        "800245795",
        "LACTALIS COLOMBIA S.A.S",
        {"003": "003"},
        "Centro (C.O. 003) cotiza en la sucursal 003; antes estaba fijo en el código.",
    ),
)


def upgrade() -> None:
    op.create_table(
        "cost_parameters",
        sa.Column("key", sa.String(60), primary_key=True),
        sa.Column("value", sa.Numeric(12, 6), nullable=False),
        sa.Column("updated_by", sa.String(320), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    rules = op.create_table(
        "supplier_branch_rules",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("supplier_nit", sa.String(20), nullable=False, unique=True),
        sa.Column("supplier_name", sa.String(200)),
        sa.Column("general_branch", sa.String(10), nullable=False),
        sa.Column("center_branches", postgresql.JSONB(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("note", sa.Text()),
        sa.Column("created_by", sa.String(320), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_by", sa.String(320), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    changes = op.create_table(
        "cost_settings_changes",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("entity", sa.String(30), nullable=False),
        sa.Column("ref", sa.String(60), nullable=False),
        sa.Column("before", postgresql.JSONB()),
        sa.Column("after", postgresql.JSONB()),
        sa.Column("note", sa.Text()),
        sa.Column("actor", sa.String(320), nullable=False),
        sa.Column("at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_cost_settings_changes_at", "cost_settings_changes", ["at"])

    now = datetime.now(UTC)
    rows, history = [], []
    for nit, name, centers, note in _RULES:
        rule = {
            "supplier_nit": nit,
            "supplier_name": name,
            "general_branch": "001",
            "center_branches": centers,
            "is_active": True,
            "note": note,
        }
        rows.append(
            {
                "id": uuid4(),
                **rule,
                "created_by": _ACTOR,
                "created_at": now,
                "updated_by": _ACTOR,
                "updated_at": now,
            }
        )
        history.append(
            {
                "id": uuid4(),
                "entity": "regla_sucursal",
                "ref": nit,
                "before": None,
                "after": rule,
                "note": note,
                "actor": _ACTOR,
                "at": now,
            }
        )
    op.bulk_insert(rules, rows)
    op.bulk_insert(changes, history)


def downgrade() -> None:
    op.drop_index("ix_cost_settings_changes_at", "cost_settings_changes")
    op.drop_table("cost_settings_changes")
    op.drop_table("supplier_branch_rules")
    op.drop_table("cost_parameters")
