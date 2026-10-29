"""JDH requests, independent net cost and append-only audit."""

from alembic import op

revision = "0036_support_requests"
down_revision = "0035_full_margin_reason"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE supplier_supports ADD COLUMN jdh_net_cost numeric(18,6)")
    op.execute(
        """ALTER TABLE supplier_supports ADD CONSTRAINT ck_support_jdh_net_cost
        CHECK (jdh_net_cost IS NULL OR (calc_method = 'costo_neto' AND jdh_net_cost >= 0))"""
    )
    op.execute(
        """CREATE TABLE supplier_support_requests (
        id uuid PRIMARY KEY,
        support_id uuid NOT NULL REFERENCES supplier_supports(id) ON DELETE RESTRICT,
        mode varchar(20) NOT NULL, value numeric(18,6) NOT NULL, reason text NOT NULL,
        requested_by_id uuid NOT NULL, requested_by varchar(320) NOT NULL,
        created_at timestamptz NOT NULL DEFAULT now(),
        status varchar(20) NOT NULL DEFAULT 'pendiente',
        version integer NOT NULL DEFAULT 1, support_version integer NOT NULL,
        snapshot jsonb NOT NULL,
        decided_by varchar(320), decided_at timestamptz, decision_reason text,
        CONSTRAINT ck_support_request_status CHECK (status IN ('pendiente','aprobada','rechazada')),
        CONSTRAINT ck_support_request_mode CHECK (mode IN ('porcentaje','costo_neto'))
    )"""
    )
    op.execute(
        "CREATE INDEX ix_support_requests_owner_created "
        "ON supplier_support_requests(requested_by_id,created_at)"
    )
    op.execute(
        "CREATE INDEX ix_support_requests_status_created "
        "ON supplier_support_requests(status,created_at)"
    )
    op.execute("CREATE INDEX ix_support_requests_support ON supplier_support_requests(support_id)")
    op.execute(
        """CREATE TABLE supplier_support_jdh_events (
        id uuid PRIMARY KEY,
        support_id uuid NOT NULL REFERENCES supplier_supports(id) ON DELETE RESTRICT,
        request_id uuid REFERENCES supplier_support_requests(id) ON DELETE RESTRICT,
        event_type varchar(40) NOT NULL, actor varchar(320) NOT NULL,
        created_at timestamptz NOT NULL DEFAULT now(), reason text, before jsonb, after jsonb
    )"""
    )
    op.execute(
        "CREATE INDEX ix_support_jdh_events_support "
        "ON supplier_support_jdh_events(support_id,created_at)"
    )
    op.execute(
        """INSERT INTO supplier_support_jdh_events(id,support_id,event_type,actor,reason,after)
        SELECT gen_random_uuid(),id,'migracion','migracion:0036',
        'Línea base; no representa una solicitud histórica',to_jsonb(s)
        FROM supplier_supports s"""
    )
    op.execute(
        """CREATE FUNCTION audit_supplier_support_jdh() RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN
        IF TG_OP = 'INSERT' THEN
            INSERT INTO supplier_support_jdh_events(id,support_id,event_type,actor,after)
            VALUES(gen_random_uuid(),NEW.id,'creacion',NEW.created_by,to_jsonb(NEW));
        ELSIF ROW(OLD.jdh_pct,OLD.jdh_net_cost,OLD.calc_method,OLD.supplier_value,
                  OLD.is_enabled,OLD.status) IS DISTINCT FROM
                  ROW(NEW.jdh_pct,NEW.jdh_net_cost,NEW.calc_method,NEW.supplier_value,
                  NEW.is_enabled,NEW.status) THEN
            INSERT INTO supplier_support_jdh_events(id,support_id,event_type,actor,before,after)
            VALUES(gen_random_uuid(),NEW.id,'actualizacion_maestro',NEW.updated_by,to_jsonb(OLD),to_jsonb(NEW));
        END IF;
        RETURN NEW;
    END $$"""
    )
    op.execute(
        """CREATE TRIGGER audit_supplier_support_jdh AFTER INSERT OR UPDATE ON supplier_supports
        FOR EACH ROW EXECUTE FUNCTION audit_supplier_support_jdh()"""
    )
    op.execute(
        """CREATE FUNCTION protect_support_jdh_history() RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN
        RAISE EXCEPTION 'El historial de apoyos JDH es inmutable';
    END $$"""
    )
    op.execute(
        """CREATE TRIGGER protect_support_jdh_history
        BEFORE UPDATE OR DELETE ON supplier_support_jdh_events
        FOR EACH ROW EXECUTE FUNCTION protect_support_jdh_history()"""
    )
    op.execute(
        """CREATE FUNCTION protect_support_request() RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN
        IF TG_OP = 'DELETE' THEN
            RAISE EXCEPTION 'Las solicitudes no se eliminan';
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
    )
    op.execute(
        """CREATE TRIGGER protect_support_request
        BEFORE UPDATE OR DELETE ON supplier_support_requests
        FOR EACH ROW EXECUTE FUNCTION protect_support_request()"""
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER audit_supplier_support_jdh ON supplier_supports")
    op.execute("DROP FUNCTION audit_supplier_support_jdh()")
    op.execute("DROP TABLE supplier_support_jdh_events")
    op.execute("DROP TABLE supplier_support_requests")
    op.execute("DROP FUNCTION protect_support_jdh_history()")
    op.execute("DROP FUNCTION protect_support_request()")
    op.execute("ALTER TABLE supplier_supports DROP CONSTRAINT ck_support_jdh_net_cost")
    op.execute("ALTER TABLE supplier_supports DROP COLUMN jdh_net_cost")
