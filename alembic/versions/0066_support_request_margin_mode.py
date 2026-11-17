"""JDH support requests ask for the authorized minimum margin (``mode = margen_minimo``).

Rules v2 (2026-11-17): JDH no longer adds a percentage or a net cost to a supplier's support;
it authorizes a minimum margin (``supplier_supports.supported_margin_pct``). The earlier modes
stay for the requests already recorded.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0066_support_request_margin_mode"
down_revision: str | None = "0065_support_list_price_amount"
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
        ELSIF ROW(OLD.jdh_pct,OLD.jdh_net_cost,{old_margin}OLD.calc_method,OLD.supplier_value,
                  OLD.is_enabled,OLD.status) IS DISTINCT FROM
                  ROW(NEW.jdh_pct,NEW.jdh_net_cost,{new_margin}NEW.calc_method,NEW.supplier_value,
                  NEW.is_enabled,NEW.status) THEN
            INSERT INTO supplier_support_jdh_events(id,support_id,event_type,actor,before,after)
            VALUES(gen_random_uuid(),NEW.id,'actualizacion_maestro',NEW.updated_by,
                   to_jsonb(OLD),to_jsonb(NEW));
        END IF;
        RETURN NEW;
    END $$"""


def upgrade() -> None:
    """Allow the margin mode; the JDH audit also watches the authorized minimum margin."""

    _modes("'porcentaje','costo_neto','margen_minimo'")
    op.execute(
        _AUDIT.format(
            old_margin="OLD.supported_margin_pct,", new_margin="NEW.supported_margin_pct,"
        )
    )


def downgrade() -> None:
    """Back to the two earlier modes (fails while a margin request exists)."""

    op.execute(_AUDIT.format(old_margin="", new_margin=""))
    _modes("'porcentaje','costo_neto'")
