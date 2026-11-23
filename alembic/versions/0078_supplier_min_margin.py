"""% Margen Garantizado: the supplier's minimum margin and its requests (2026-11-23).

- ``supplier_supports.supplier_min_margin_pct``: the margin the supported price is set at
  (cost / (1 − margin)); the supplier pays up to the guaranteed margin. It may be negative:
  it is the supplier's exception, not profitability JDH gives up.
- ``internal_support_requests.kind``: ``jdh`` (every request so far) or ``proveedor``, a
  supplier margin request asked from a quotation line, under the supplier's minimum, for that
  quotation's customer; its items may ask a negative margin and keep the support they lower
  (``base_support_id``), which approving copies with the approved minimum.
- The JDH audit also watches the supplier's minimum margin.

Existing rows stay as they are.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0078_supplier_min_margin"
down_revision: str | Sequence[str] | None = "0077_quotation_cost_warehouses"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_ITEMS = "internal_support_request_items"

_AUDIT = """CREATE OR REPLACE FUNCTION audit_supplier_support_jdh() RETURNS trigger
LANGUAGE plpgsql AS $$
    BEGIN
        IF TG_OP = 'INSERT' THEN
            INSERT INTO supplier_support_jdh_events(id,support_id,event_type,actor,after)
            VALUES(gen_random_uuid(),NEW.id,'creacion',NEW.created_by,to_jsonb(NEW));
        ELSIF ROW(OLD.jdh_pct,OLD.jdh_net_cost,OLD.supported_margin_pct,{old_supplier}
                  OLD.calc_method,OLD.supplier_value,OLD.is_enabled,OLD.status)
                  IS DISTINCT FROM
                  ROW(NEW.jdh_pct,NEW.jdh_net_cost,NEW.supported_margin_pct,{new_supplier}
                  NEW.calc_method,NEW.supplier_value,NEW.is_enabled,NEW.status) THEN
            INSERT INTO supplier_support_jdh_events(id,support_id,event_type,actor,before,after)
            VALUES(gen_random_uuid(),NEW.id,'actualizacion_maestro',NEW.updated_by,
                   to_jsonb(OLD),to_jsonb(NEW));
        END IF;
        RETURN NEW;
    END $$"""


def _margin_checks(low: str) -> None:
    for name, column, nullable in (
        ("ck_internal_support_request_items_requested", "requested_margin_pct", False),
        ("ck_internal_support_request_items_approved", "approved_margin_pct", True),
    ):
        op.drop_constraint(name, _ITEMS, type_="check")
        rule = f"{column} {low} AND {column} < 1"
        op.create_check_constraint(
            name, _ITEMS, f"{column} IS NULL OR ({rule})" if nullable else rule
        )


def upgrade() -> None:
    op.add_column("supplier_supports", sa.Column("supplier_min_margin_pct", sa.Numeric(10, 6)))
    op.create_check_constraint(
        "ck_supplier_supports_supplier_min_margin",
        "supplier_supports",
        "supplier_min_margin_pct IS NULL OR "
        "(supplier_min_margin_pct > -1 AND supplier_min_margin_pct < 1)",
    )
    op.add_column(
        "internal_support_requests",
        sa.Column("kind", sa.String(10), nullable=False, server_default="jdh"),
    )
    op.create_check_constraint(
        "ck_internal_support_requests_kind",
        "internal_support_requests",
        "kind IN ('jdh', 'proveedor')",
    )
    op.add_column(
        _ITEMS,
        sa.Column(
            "base_support_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("supplier_supports.id", ondelete="SET NULL"),
        ),
    )
    _margin_checks("> -1")
    op.execute(
        _AUDIT.format(
            old_supplier="OLD.supplier_min_margin_pct,", new_supplier="NEW.supplier_min_margin_pct,"
        )
    )


def downgrade() -> None:
    """Back to JDH requests only (fails while a negative margin is stored)."""

    op.execute(_AUDIT.format(old_supplier="", new_supplier=""))
    _margin_checks(">= 0")
    op.drop_column(_ITEMS, "base_support_id")
    op.drop_constraint(
        "ck_internal_support_requests_kind", "internal_support_requests", type_="check"
    )
    op.drop_column("internal_support_requests", "kind")
    op.drop_constraint(
        "ck_supplier_supports_supplier_min_margin", "supplier_supports", type_="check"
    )
    op.drop_column("supplier_supports", "supplier_min_margin_pct")
