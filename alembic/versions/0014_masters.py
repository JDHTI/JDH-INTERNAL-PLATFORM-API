"""Add the shared master catalog: definitions, declared fields and records.

Masters come from the DWH and are normalized onto one shape; anything specific
to a master lives in ``master_records.attributes``. This migration seeds the 11
master definitions with their base fields. Records arrive later through sync.
"""

from collections.abc import Sequence
from uuid import NAMESPACE_URL, UUID, uuid5

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0014_masters"
down_revision: str | None = "0013_pqrsf_public_intake"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_KEY = "Clave de origen"


def _f(
    name: str,
    label: str,
    data_type: str = "text",
    role: str = "attribute",
    *,
    nullable: bool = True,
    pii: bool = False,
    ref: str | None = None,
) -> tuple:
    return (name, label, data_type, role, nullable, pii, ref)


def _key(name: str) -> tuple:
    return _f(name, _KEY, role="key", nullable=False)


def _company(name: str) -> tuple:
    return _f(name, "Compañía", "integer", "company", nullable=False)


_STATE = _f("estado", "Estado (A/I)", role="active", nullable=False)

# Fields use the source column names of the consumo views (terceros: dim_tercero),
# verified against the live DWH on 2026-09-29. Sync maps estado 'A' to is_active.
# slug, name, description, source_system, source_object, grain, natural_key, fields
_MASTERS = (
    (
        "terceros",
        "Terceros",
        "Personas y empresas del ERP. El NIT no es único.",
        "siesa",
        "mart_maestro.dim_tercero",
        "tercero",
        ["tercero_key"],
        [
            _key("tercero_key"),
            _company("id_cia"),
            _f("identificacion", "Identificación", role="code"),
            _f("razon_social", "Razón social", role="name"),
            _f("nit", "NIT"),
            _f("nombre_comercial", "Nombre comercial"),
            _f("tipo_identificacion_id", "Código de tipo de identificación"),
            _f("tipo_identificacion", "Tipo de identificación"),
            _f("actividad_economica_ciiu", "Código CIIU"),
            _f("actividad_economica", "Actividad económica"),
            _f("es_cliente", "Es cliente", "boolean", nullable=False),
            _f("es_proveedor", "Es proveedor", "boolean", nullable=False),
            _f("es_empleado", "Es empleado", "boolean", nullable=False),
            _f("es_accionista", "Es accionista", "boolean", nullable=False),
            _f("es_otros", "Es otros", "boolean", nullable=False),
            _f("es_interno", "Es interno", "boolean", nullable=False),
            _f("estado_activo", "Activo", "boolean", "active", nullable=False),
            _f("direccion1", "Dirección", pii=True),
            _f("telefono", "Teléfono", pii=True),
            _f("celular", "Celular", pii=True),
            _f("email", "Correo electrónico", pii=True),
            _f("id_pais", "Código de país"),
            _f("id_depto", "Código de departamento"),
            _f("id_ciudad", "Código de ciudad"),
        ],
    ),
    (
        "clientes",
        "Clientes",
        "Clientes por sucursal.",
        "siesa",
        "consumo.clientes",
        "cliente × sucursal",
        ["cliente_key"],
        [
            _key("cliente_key"),
            _company("cia"),
            _f("nit", "NIT", role="code"),
            _f("razon_social", "Razón social", role="name"),
            _f("id_sucursal", "Sucursal", nullable=False),
            _f("sucursal", "Descripción de sucursal"),
            _f("nombre_establecimiento", "Establecimiento"),
            _f("cod_zona_logistica", "Código de zona logística"),
            _f("zona_logistica", "Zona logística"),
            _f("pais", "País"),
            _f("departamento", "Departamento"),
            _f("ciudad", "Ciudad"),
            _f("cod_vendedor", "Código de vendedor", role="reference", ref="vendedores"),
            _f("nombre_vendedor", "Vendedor", pii=True),
            _f("telefono", "Teléfono", pii=True),
            _f("celular", "Celular", pii=True),
            _f("direccion", "Dirección", pii=True),
            _f("correo", "Correo electrónico", pii=True),
            _f(
                "cod_unidad_negocio",
                "Código de unidad de negocio",
                role="reference",
                ref="unidades-negocio",
            ),
            _f("unidad_negocio", "Unidad de negocio"),
            _f("cod_canal", "Código de canal"),
            _f("canal", "Canal"),
            _STATE,
            _f("estado_sucursal", "Estado de sucursal (A/I)", nullable=False),
            _f("condicion_pago", "Condición de pago"),
            _f("desc_condicion_pago", "Descripción de condición de pago"),
            _f("cod_grupo_empresarial", "Código de grupo empresarial"),
            _f("grupo_empresarial", "Grupo empresarial"),
        ],
    ),
    (
        "proveedores",
        "Proveedores",
        "Proveedores por sucursal. Los datos bancarios se excluyen.",
        "siesa",
        "consumo.proveedores",
        "proveedor × sucursal",
        ["proveedor_key"],
        [
            _key("proveedor_key"),
            _company("cia"),
            _f("nit", "NIT", role="code"),
            _f("razon_social", "Razón social", role="name"),
            _f("cod_tipo_identificacion", "Código de tipo de identificación"),
            _f("tipo_identificacion", "Tipo de identificación"),
            _f("nombre_establecimiento", "Establecimiento"),
            _f("cod_actividad_economica", "Código de actividad económica"),
            _f("actividad_economica", "Actividad económica"),
            _f("id_sucursal", "Sucursal", nullable=False),
            _f("sucursal", "Descripción de sucursal"),
            _f("pais", "País"),
            _f("departamento", "Departamento"),
            _f("ciudad", "Ciudad"),
            _f("direccion", "Dirección", pii=True),
            _f("telefono", "Teléfono", pii=True),
            _f("celular", "Celular", pii=True),
            _f("correo", "Correo electrónico", pii=True),
            _f("cod_clase_proveedor", "Código de clase de proveedor"),
            _f("clase_proveedor", "Clase de proveedor"),
            _f("cod_tipo_proveedor", "Código de tipo de proveedor"),
            _f("tipo_proveedor", "Tipo de proveedor"),
            _STATE,
            _f("estado_sucursal", "Estado de sucursal (A/I)", nullable=False),
            _f("condicion_pago", "Condición de pago"),
            _f("desc_condicion_pago", "Descripción de condición de pago"),
            _f("dias_gracia", "Días de gracia", "integer"),
            _f("cupo_credito_otorgado", "Cupo de crédito otorgado", "numeric"),
            _f("moneda", "Moneda"),
            _f("bloqueo_pago", "Bloqueo de pago", "boolean", nullable=False),
            _f("maneja_consignacion", "Maneja consignación", "boolean", nullable=False),
            _f("es_declarante_renta", "Declarante de renta", "boolean", nullable=False),
            _f("fecha_ingreso", "Fecha de ingreso", "date"),
        ],
    ),
    (
        "vendedores",
        "Vendedores",
        "La vista consumo no expone la compañía; solo existe en dim_vendedor.",
        "siesa",
        "consumo.vendedores",
        "vendedor",
        ["id_cia", "codigo"],
        [
            _key("vendedor_key"),
            _f("codigo", "Código de vendedor", role="code"),
            _f("nombre", "Nombre", role="name", pii=True),
            _STATE,
            _f("supervisor", "Supervisor", pii=True),
            _f("equipo", "Equipo"),
            _f("clase_equipo", "Clase de equipo"),
            _f("regional", "Regional"),
            _f("unidad_negocio", "Unidad de negocio"),
            _f(
                "cod_centro_costo",
                "Código de centro de costo",
                role="reference",
                ref="centros-costo",
            ),
            _f("centro_costo", "Centro de costo"),
            _f("correo", "Correo corporativo", pii=True),
            _f("celular", "Celular", pii=True),
        ],
    ),
    (
        "supervisores",
        "Supervisores",
        "Origen Excel; sin compañía.",
        "excel",
        "consumo.supervisores",
        "supervisor",
        ["nit"],
        [
            _key("supervisor_key"),
            _f("nit", "NIT", role="code", pii=True),
            _f("nombre", "Nombre", role="name", pii=True),
            _f("correo", "Correo corporativo", pii=True),
            _f("celular", "Celular", pii=True),
            _f("rol", "Rol"),
            _f("equipo_key", "Equipo", role="reference", ref="equipos"),
            _f("equipo", "Nombre de equipo"),
            _f("regional_key", "Regional", role="reference", ref="regionales"),
            _f("regional", "Nombre de regional"),
            _f(
                "unidad_negocio_key",
                "Unidad de negocio",
                role="reference",
                ref="unidades-negocio",
            ),
            _f("unidad_negocio", "Nombre de unidad de negocio"),
        ],
    ),
    (
        "equipos",
        "Equipos",
        "Origen Excel; sin código.",
        "excel",
        "consumo.equipos",
        "equipo",
        ["id_cia", "nombre"],
        [
            _key("equipo_key"),
            _company("cia"),
            _f("nombre", "Nombre", role="name", nullable=False),
            _f("clase", "Clase"),
        ],
    ),
    (
        "productos",
        "Productos",
        "El código es el SKU (referencia del ERP), no el identificador interno.",
        "siesa",
        "consumo.productos",
        "producto",
        ["id_cia", "sku"],
        [
            _key("producto_key"),
            _f("sku", "SKU", role="code"),
            _f("sku_proveedor", "SKU del proveedor"),
            _f("descripcion", "Descripción", role="name"),
            _f("fecha_creacion", "Fecha de creación", "timestamp"),
            _STATE,
            _f("tipo_item", "Tipo de ítem"),
            _f("cod_proveedor", "Código de proveedor"),
            _f("proveedor", "Proveedor"),
            _f("fabricante", "Fabricante"),
            _f("marca", "Marca"),
            _f("categoria", "Categoría"),
            _f("subcategoria", "Subcategoría"),
            _f("linea", "Línea"),
            _f("sublinea", "Sublínea"),
            _f("grupo", "Grupo"),
            _f("origen", "Origen"),
            _f("peso", "Peso", "numeric"),
            _f("factor_empaque", "Factor de empaque", "numeric"),
        ],
    ),
    (
        "centros-operacion",
        "Centros de operación",
        None,
        "siesa",
        "consumo.centros_operacion",
        "centro de operación",
        ["id_cia", "codigo"],
        [
            _key("centro_operacion_key"),
            _company("cia"),
            _f("codigo", "Código", role="code", nullable=False),
            _f("descripcion", "Descripción", role="name"),
            _f("cod_regional", "Código de regional"),
            _f("desc_regional", "Regional"),
            _f("cod_portafolio", "Código de portafolio"),
            _STATE,
        ],
    ),
    (
        "centros-costo",
        "Centros de costo",
        None,
        "siesa",
        "consumo.centros_costo",
        "centro de costo",
        ["id_cia", "codigo"],
        [
            _key("centro_costo_key"),
            _company("cia"),
            _f("codigo", "Código", role="code", nullable=False),
            _f("descripcion", "Descripción", role="name"),
            _f(
                "cod_centro_operacion",
                "Código de centro de operación",
                role="reference",
                ref="centros-operacion",
            ),
            _f("centro_operacion", "Centro de operación"),
            _f("responsable", "Responsable", pii=True),
            _STATE,
            _f(
                "cod_unidad_negocio",
                "Código de unidad de negocio",
                role="reference",
                ref="unidades-negocio",
            ),
            _f("unidad_negocio", "Unidad de negocio"),
        ],
    ),
    (
        "unidades-negocio",
        "Unidades de negocio",
        None,
        "siesa",
        "consumo.unidades_negocio",
        "unidad de negocio",
        ["id_cia", "codigo"],
        [
            _key("unidad_negocio_key"),
            _company("cia"),
            _f("codigo", "Código", role="code", nullable=False),
            _f("descripcion", "Descripción", role="name"),
            _STATE,
        ],
    ),
    (
        "regionales",
        "Regionales",
        "La vista consumo solo expone la clave y el nombre.",
        "siesa",
        "consumo.regionales",
        "regional",
        ["regional_key"],
        [
            _key("regional_key"),
            _f("nombre", "Nombre", role="name", nullable=False),
        ],
    ),
)


def _seed_id(kind: str, key: str) -> UUID:
    return uuid5(NAMESPACE_URL, f"https://jdh.com.co/masters/{kind}/{key}")


def _timestamps() -> list[sa.Column]:
    return [
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    ]


def _trigram_available(connection: sa.engine.Connection) -> bool:
    available = connection.execute(
        sa.text("SELECT 1 FROM pg_available_extensions WHERE name = 'pg_trgm'")
    ).first()
    if available is None:
        return False
    try:
        with connection.begin_nested():
            connection.execute(sa.text("CREATE EXTENSION IF NOT EXISTS pg_trgm"))
    except sa.exc.DBAPIError:
        return False
    return True


def upgrade() -> None:
    """Create the master tables, search indexes and seed the definitions."""

    op.create_table(
        "master_definitions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("slug", sa.String(80), nullable=False, unique=True),
        sa.Column("name", sa.String(150), nullable=False),
        sa.Column("description", sa.Text()),
        sa.Column("source_system", sa.String(20), nullable=False),
        sa.Column("source_object", sa.String(200)),
        sa.Column("grain", sa.String(200)),
        sa.Column("natural_key", postgresql.ARRAY(sa.Text()), nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column("sort_order", sa.Integer(), server_default="0", nullable=False),
        sa.Column("last_synced_at", sa.DateTime(timezone=True)),
        *_timestamps(),
        sa.CheckConstraint(
            "source_system IN ('siesa', 'excel', 'platform')",
            name="ck_master_definitions_source_system",
        ),
    )
    op.create_table(
        "master_fields",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "master_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("master_definitions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("label", sa.String(150), nullable=False),
        sa.Column("data_type", sa.String(20), nullable=False),
        sa.Column("role", sa.String(20), nullable=False),
        sa.Column("is_nullable", sa.Boolean(), nullable=False),
        sa.Column("is_pii", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("references_slug", sa.String(80)),
        sa.Column("sort_order", sa.Integer(), nullable=False),
        sa.UniqueConstraint("master_id", "name", name="uq_master_fields_master_name"),
        sa.CheckConstraint(
            "data_type IN ('text', 'integer', 'numeric', 'boolean', 'date', 'timestamp')",
            name="ck_master_fields_data_type",
        ),
        sa.CheckConstraint(
            "role IN ('key', 'company', 'code', 'name', 'active', 'reference', 'attribute')",
            name="ck_master_fields_role",
        ),
    )
    op.create_table(
        "master_records",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "master_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("master_definitions.id"),
            nullable=False,
        ),
        sa.Column("company_id", sa.SmallInteger()),
        sa.Column("code", sa.Text()),
        sa.Column("name", sa.Text()),
        sa.Column("is_active", sa.Boolean()),
        sa.Column("source_key", sa.Text(), nullable=False),
        sa.Column("attributes", postgresql.JSONB(), server_default="{}", nullable=False),
        sa.Column("synced_at", sa.DateTime(timezone=True)),
        *_timestamps(),
        sa.UniqueConstraint("master_id", "source_key", name="uq_master_records_master_source_key"),
    )
    op.create_index(
        "ix_master_records_master_company_code",
        "master_records",
        ["master_id", "company_id", "code"],
    )
    connection = op.get_bind()
    if _trigram_available(connection):
        for column in ("code", "name"):
            op.create_index(
                f"ix_master_records_{column}_trgm",
                "master_records",
                [column],
                postgresql_using="gin",
                postgresql_ops={column: "gin_trgm_ops"},
            )
    else:
        for column in ("code", "name"):
            op.create_index(
                f"ix_master_records_{column}_lower",
                "master_records",
                [sa.text(f"lower({column})")],
            )

    definitions, fields = [], []
    for position, master in enumerate(_MASTERS, start=1):
        slug, name, description, system, source_object, grain, natural_key, specs = master
        master_id = _seed_id("definitions", slug)
        definitions.append(
            {
                "id": master_id,
                "slug": slug,
                "name": name,
                "description": description,
                "source_system": system,
                "source_object": source_object,
                "grain": grain,
                "natural_key": natural_key,
                "sort_order": position * 10,
            }
        )
        for order, spec in enumerate(specs, start=1):
            field_name, label, data_type, role, nullable, pii, references = spec
            fields.append(
                {
                    "id": _seed_id("fields", f"{slug}/{field_name}"),
                    "master_id": master_id,
                    "name": field_name,
                    "label": label,
                    "data_type": data_type,
                    "role": role,
                    "is_nullable": nullable,
                    "is_pii": pii,
                    "references_slug": references,
                    "sort_order": order * 10,
                }
            )
    op.bulk_insert(
        sa.table(
            "master_definitions",
            sa.column("id", postgresql.UUID(as_uuid=True)),
            sa.column("slug", sa.String()),
            sa.column("name", sa.String()),
            sa.column("description", sa.Text()),
            sa.column("source_system", sa.String()),
            sa.column("source_object", sa.String()),
            sa.column("grain", sa.String()),
            sa.column("natural_key", postgresql.ARRAY(sa.Text())),
            sa.column("sort_order", sa.Integer()),
        ),
        definitions,
    )
    op.bulk_insert(
        sa.table(
            "master_fields",
            sa.column("id", postgresql.UUID(as_uuid=True)),
            sa.column("master_id", postgresql.UUID(as_uuid=True)),
            sa.column("name", sa.String()),
            sa.column("label", sa.String()),
            sa.column("data_type", sa.String()),
            sa.column("role", sa.String()),
            sa.column("is_nullable", sa.Boolean()),
            sa.column("is_pii", sa.Boolean()),
            sa.column("references_slug", sa.String()),
            sa.column("sort_order", sa.Integer()),
        ),
        fields,
    )


def downgrade() -> None:
    """Drop the master tables. The pg_trgm extension is left installed."""

    op.execute("DROP INDEX IF EXISTS ix_master_records_code_trgm")
    op.execute("DROP INDEX IF EXISTS ix_master_records_name_trgm")
    op.execute("DROP INDEX IF EXISTS ix_master_records_code_lower")
    op.execute("DROP INDEX IF EXISTS ix_master_records_name_lower")
    op.drop_index("ix_master_records_master_company_code", table_name="master_records")
    op.drop_table("master_records")
    op.drop_table("master_fields")
    op.drop_table("master_definitions")
