"""% Margen Garantizado: the supplier's minimum margin and its requests (2026-11-23).

- ``supplier_supports.supplier_min_margin_pct``: the margin the supported price is set at
  (cost / (1 − margin)); the supplier pays up to the guaranteed margin. It may be negative:
  it is the supplier's exception, not profitability JDH gives up.
- ``supplier_support_requests.mode`` takes ``margen_proveedor``: a request for a margin under
  that minimum, decided in the requests module like the JDH ones.
- The JDH audit also watches the supplier's minimum margin.

Existing supports keep it empty, so nothing changes until it is written.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0078_supplier_min_margin"
down_revision: str | Sequence[str] | None = "0077_quotation_cost_warehouses"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _modes(modes: str) -> None:
    op.drop_constraint("ck_support_request_mode", "supplier_support_requests", type_="check")
    op.create_check_constraint(
        "ck_support_request_mode", "supplier_support_requests", f"mode IN ({modes})"
    )


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


def upgrade() -> None:
    op.add_column("supplier_supports", sa.Column("supplier_min_margin_pct", sa.Numeric(10, 6)))
    op.create_check_constraint(
        "ck_supplier_supports_supplier_min_margin",
        "supplier_supports",
        "supplier_min_margin_pct IS NULL OR "
        "(supplier_min_margin_pct > -1 AND supplier_min_margin_pct < 1)",
    )
    _modes("'porcentaje','costo_neto','margen_minimo','margen_proveedor'")
    op.execute(
        _AUDIT.format(
            old_supplier="OLD.supplier_min_margin_pct,", new_supplier="NEW.supplier_min_margin_pct,"
        )
    )


def downgrade() -> None:
    """Back to the JDH modes (fails while a supplier margin request exists)."""

    op.execute(_AUDIT.format(old_supplier="", new_supplier=""))
    _modes("'porcentaje','costo_neto','margen_minimo'")
    op.drop_constraint(
        "ck_supplier_supports_supplier_min_margin", "supplier_supports", type_="check"
    )
    op.drop_column("supplier_supports", "supplier_min_margin_pct")
