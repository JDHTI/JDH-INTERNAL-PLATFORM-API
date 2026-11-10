-- Contract of the platform's supplier supports in the DWH (schema ``plataforma``).
--
-- Reference for the dwh-platform migration (the DWH team owns and applies it as svc_etl;
-- the platform never runs DDL in the warehouse). Idempotent: dwh-platform re-applies every
-- migration on each deploy. No ALTER in the normal path (it would take ACCESS EXCLUSIVE
-- against the platform's writes): new fields travel inside ``datos`` (JSONB).
--
-- Writer: the platform's worker as svc_plataforma_apoyos (group dwh_escritor_plataforma),
-- SELECT/INSERT/UPDATE only, never DELETE/TRUNCATE/DDL. Readers (BI, MCP, svc_bi) read the
-- dbt view consumo.apoyos_proveedor, never this schema.
--
-- The platform's integration test applies this same file to a scratch database
-- (tests/modules/dwh_publish/test_publish_postgres.py), so the contract and the writer's
-- SQL are checked together.

CREATE SCHEMA IF NOT EXISTS plataforma;

-- Current version of each support (upsert by id, only when the version is newer).
CREATE TABLE IF NOT EXISTS plataforma.apoyo_proveedor (
    id                      uuid        PRIMARY KEY,
    version                 bigint      NOT NULL CHECK (version > 0),
    proveedor_codigo        text,
    proveedor_nombre        text,
    tipo                    text        NOT NULL,
    metodo_calculo          text        NOT NULL,
    financia                text,
    programa                text,
    referencia_externa      text,
    nit_cliente             text,
    cliente                 text,
    grupo_cliente           text,
    sku                     text,
    sku_proveedor           text,
    marca                   text,
    regionales              text[]      NOT NULL DEFAULT '{}',
    unidades_negocio        text[]      NOT NULL DEFAULT '{}',
    canales                 text[]      NOT NULL DEFAULT '{}',
    valor_proveedor         numeric(18, 6),
    apoyo_jdh_pct           numeric(10, 6),
    apoyo_jdh_costo_neto    numeric(18, 6),
    unidad_valor            text,
    margen_objetivo         numeric(10, 6),
    margen_minimo           numeric(10, 6),
    margen_lista            numeric(10, 6),
    factor_empaque          numeric(14, 4),
    costo_referencia        numeric(18, 4),
    precio_lista_referencia numeric(18, 4),
    costo_dwh_unidad        numeric(18, 4),
    costo_dwh_fuente        text,
    costo_dwh_fecha         date,
    vigencia_desde          date,
    vigencia_hasta          date,
    estado                  text        NOT NULL,
    habilitado              boolean     NOT NULL,
    origen                  text,
    carga_id                uuid,
    creado_en               timestamptz NOT NULL,
    creado_por              text        NOT NULL,
    modificado_en           timestamptz NOT NULL,
    modificado_por          text        NOT NULL,
    anulado_en              timestamptz,
    motivo_anulacion        text,
    datos                   jsonb       NOT NULL,
    publicado_en            timestamptz NOT NULL DEFAULT now(),
    actualizado_en          timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_apoyo_proveedor_proveedor ON plataforma.apoyo_proveedor (proveedor_codigo);
CREATE INDEX IF NOT EXISTS ix_apoyo_proveedor_cliente ON plataforma.apoyo_proveedor (nit_cliente);
CREATE INDEX IF NOT EXISTS ix_apoyo_proveedor_sku ON plataforma.apoyo_proveedor (sku);
CREATE INDEX IF NOT EXISTS ix_apoyo_proveedor_vigencia
    ON plataforma.apoyo_proveedor (vigencia_desde, vigencia_hasta);

-- Every version of every support, append-only (ON CONFLICT (id, version) DO NOTHING).
CREATE TABLE IF NOT EXISTS plataforma.apoyo_proveedor_historial (
    id                      uuid        NOT NULL,
    version                 bigint      NOT NULL CHECK (version > 0),
    proveedor_codigo        text,
    proveedor_nombre        text,
    tipo                    text        NOT NULL,
    metodo_calculo          text        NOT NULL,
    financia                text,
    programa                text,
    referencia_externa      text,
    nit_cliente             text,
    cliente                 text,
    grupo_cliente           text,
    sku                     text,
    sku_proveedor           text,
    marca                   text,
    regionales              text[]      NOT NULL DEFAULT '{}',
    unidades_negocio        text[]      NOT NULL DEFAULT '{}',
    canales                 text[]      NOT NULL DEFAULT '{}',
    valor_proveedor         numeric(18, 6),
    apoyo_jdh_pct           numeric(10, 6),
    apoyo_jdh_costo_neto    numeric(18, 6),
    unidad_valor            text,
    margen_objetivo         numeric(10, 6),
    margen_minimo           numeric(10, 6),
    margen_lista            numeric(10, 6),
    factor_empaque          numeric(14, 4),
    costo_referencia        numeric(18, 4),
    precio_lista_referencia numeric(18, 4),
    costo_dwh_unidad        numeric(18, 4),
    costo_dwh_fuente        text,
    costo_dwh_fecha         date,
    vigencia_desde          date,
    vigencia_hasta          date,
    estado                  text        NOT NULL,
    habilitado              boolean     NOT NULL,
    origen                  text,
    carga_id                uuid,
    creado_en               timestamptz NOT NULL,
    creado_por              text        NOT NULL,
    modificado_en           timestamptz NOT NULL,
    modificado_por          text        NOT NULL,
    anulado_en              timestamptz,
    motivo_anulacion        text,
    datos                   jsonb       NOT NULL,
    registrado_en           timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (id, version)
);

-- Each bulk load of supports (inserted once).
CREATE TABLE IF NOT EXISTS plataforma.carga_apoyos (
    id               uuid        PRIMARY KEY,
    archivo          text        NOT NULL,
    archivo_sha256   text        NOT NULL,
    proveedor_codigo text,
    hoja             text,
    usuario          text        NOT NULL,
    creado_en        timestamptz NOT NULL,
    filas            integer     NOT NULL,
    creados          integer     NOT NULL,
    actualizados     integer     NOT NULL,
    sin_cambio       integer     NOT NULL,
    ausentes         integer     NOT NULL,
    vencidos         integer     NOT NULL,
    publicado_en     timestamptz NOT NULL DEFAULT now()
);

-- Ownership and grants, only where the roles exist (a GRANT to a missing role would abort
-- the whole deploy). The roles themselves are created by hand by a superuser.
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'svc_etl') THEN
        ALTER SCHEMA plataforma OWNER TO svc_etl;
        ALTER TABLE plataforma.apoyo_proveedor OWNER TO svc_etl;
        ALTER TABLE plataforma.apoyo_proveedor_historial OWNER TO svc_etl;
        ALTER TABLE plataforma.carga_apoyos OWNER TO svc_etl;
    END IF;
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'dwh_escritor_plataforma') THEN
        GRANT USAGE ON SCHEMA plataforma TO dwh_escritor_plataforma;
        GRANT SELECT, INSERT, UPDATE ON
            plataforma.apoyo_proveedor,
            plataforma.apoyo_proveedor_historial,
            plataforma.carga_apoyos
        TO dwh_escritor_plataforma;
    END IF;
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'adm_plataforma') THEN
        GRANT USAGE ON SCHEMA plataforma TO adm_plataforma;
        GRANT SELECT ON ALL TABLES IN SCHEMA plataforma TO adm_plataforma;
    END IF;
END $$;
