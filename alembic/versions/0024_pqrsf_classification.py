"""Classify public requests: type descriptions, subjects and causes, and claimed products.

The public form stops asking for free text first: the requester picks a subject (the
"novedad" of the communication channels table of the PQR procedure) and a cause that
belongs to it, and the cause says which dependency attends the case. Complaints and
claims about a product carry one or more product lines (invoice, JDH reference, lot,
expiry date, affected quantity) and whether a pickup order was already requested.

The seeded subjects and causes are the ones named in the planning meeting of 2026-10-22;
they are provisional until Quality hands over the official SNC/PQR matrix. Suggestions
start accepting attachments, and the description of a request becomes optional (it is
required by the service only where it is the content or the requester picked "Otro").
"""

from collections.abc import Sequence
from uuid import NAMESPACE_URL, uuid5

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0024_pqrsf_classification"
down_revision: str | None = "0023_dwh_monitoring"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TYPE_DESCRIPTIONS = {
    "peticion": "Solicitudes a JDH: documentos, visitas, auditorías y otras relacionadas.",
    "queja": "Novedad en el momento de la entrega, antes de firmar la factura o cerrar el día.",
    "reclamo": "Novedad después de la entrega: factura firmada a satisfacción o ya pasó tiempo.",
    "sugerencia": "Propuesta para mejorar nuestros procesos o el servicio.",
    "felicitacion": "Reconocimiento a una acción destacada o a un servicio excepcional.",
}

_CLAIMS = ["queja", "reclamo"]

# (code, name, description, request types, dependency, is_other, causes)
# cause: (code, name, dependency, route, requires_product, allows_product, is_other)
_SUBJECTS = (
    (
        "calidad_producto",
        "Calidad del producto",
        "Estado, fecha o calidad del producto recibido.",
        _CLAIMS,
        "calidad",
        False,
        (
            (
                "averiado_calidad",
                "Averiado por calidad del fabricante",
                "calidad",
                "supplier",
                True,
                True,
                False,
            ),
            (
                "averiado_manipulacion",
                "Averiado por manipulación (aplastado, roto)",
                "almacenamiento",
                "internal",
                True,
                True,
                False,
            ),
            ("vencido", "Producto vencido", "almacenamiento", "internal", True, True, False),
            (
                "corta_fecha",
                "Producto con fecha corta",
                "almacenamiento",
                "internal",
                True,
                True,
                False,
            ),
            (
                "contaminacion_biologica",
                "Contaminación biológica (gusano, gorgojo)",
                "calidad",
                "supplier",
                True,
                True,
                False,
            ),
            (
                "contaminacion_cruzada",
                "Contaminación cruzada (químicos con alimentos)",
                "distribucion",
                "internal",
                True,
                True,
                False,
            ),
            (
                "empaque_defectuoso",
                "Empaque débil o mala costura",
                "calidad",
                "supplier",
                True,
                True,
                False,
            ),
            ("otro_calidad_producto", "Otro", "calidad", "none", False, True, True),
        ),
    ),
    (
        "entrega",
        "Comportamiento en la entrega",
        "Lo que pasó al recibir el pedido.",
        _CLAIMS,
        "distribucion",
        False,
        (
            (
                "producto_trocado",
                "Llegó un producto diferente (trocado)",
                "distribucion",
                "internal",
                True,
                True,
                False,
            ),
            ("error_cargue", "Error en el cargue", "almacenamiento", "internal", True, True, False),
            (
                "producto_no_solicitado",
                "Producto no solicitado",
                "comercial",
                "internal",
                True,
                True,
                False,
            ),
            ("otro_entrega", "Otro", "distribucion", "none", False, True, True),
        ),
    ),
    (
        "tiempo_entrega",
        "Tiempo de entrega",
        "Demoras o entregas fuera del horario acordado.",
        _CLAIMS,
        "distribucion",
        False,
        (
            (
                "entrega_tardia",
                "Entrega fuera del horario acordado",
                "distribucion",
                "internal",
                False,
                False,
                False,
            ),
            ("otro_tiempo_entrega", "Otro", "distribucion", "none", False, False, True),
        ),
    ),
    (
        "presentacion_personal",
        "Presentación personal",
        "Presentación del conductor o del auxiliar de ruta.",
        _CLAIMS,
        "distribucion",
        False,
        (
            (
                "sin_epp",
                "Personal sin elementos de protección (EPP)",
                "distribucion",
                "internal",
                False,
                False,
                False,
            ),
            (
                "presentacion_inadecuada",
                "Presentación o higiene inadecuada",
                "distribucion",
                "internal",
                False,
                False,
                False,
            ),
            ("otro_presentacion_personal", "Otro", "distribucion", "none", False, False, True),
        ),
    ),
    (
        "servicio_cliente",
        "Servicio al cliente",
        "Atención recibida de nuestro equipo.",
        _CLAIMS,
        "comercial",
        False,
        (
            (
                "actitud_servicio",
                "Actitud de servicio",
                "comercial",
                "internal",
                False,
                False,
                False,
            ),
            ("otro_servicio_cliente", "Otro", "comercial", "none", False, False, True),
        ),
    ),
    (
        "documentos_producto",
        "Documentos del producto",
        "Documentos que faltan o no corresponden al producto.",
        _CLAIMS,
        "calidad",
        False,
        (
            (
                "documento_faltante",
                "Documento faltante o incorrecto",
                "calidad",
                "none",
                False,
                True,
                False,
            ),
            ("otro_documentos_producto", "Otro", "calidad", "none", False, True, True),
        ),
    ),
    (
        "facturacion",
        "Facturación",
        "Errores de precio, cantidad o datos en la factura.",
        _CLAIMS,
        "comercial",
        False,
        (),
    ),
    ("otro_novedad", "Otro", "Una novedad que no está en la lista.", _CLAIMS, "calidad", True, ()),
    (
        "visita",
        "Visita",
        "Agendar una visita a una sede de JDH.",
        ["peticion"],
        "calidad",
        False,
        (),
    ),
    (
        "auditoria",
        "Auditoría",
        "Programar una auditoría a JDH.",
        ["peticion"],
        "calidad",
        False,
        (),
    ),
    ("otro_peticion", "Otro", "Otra solicitud.", ["peticion"], "calidad", True, ()),
)


def _seed_id(kind: str, key: str):
    return uuid5(NAMESPACE_URL, f"https://jdh.com.co/pqrsf/{kind}/{key}")


def _timestamps() -> list[sa.Column]:
    return [
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    ]


def upgrade() -> None:
    """Add the type descriptions, the subject and cause catalogs and the product lines."""

    op.add_column("pqrsf_request_types", sa.Column("description", sa.String(300)))
    for code, description in _TYPE_DESCRIPTIONS.items():
        op.execute(
            sa.text("UPDATE pqrsf_request_types SET description = :d WHERE code = :c").bindparams(
                d=description, c=code
            )
        )
    op.execute("UPDATE pqrsf_request_types SET allows_attachments = true WHERE code = 'sugerencia'")

    op.create_table(
        "pqrsf_subjects",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("code", sa.String(40), nullable=False, unique=True),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("description", sa.String(300)),
        sa.Column(
            "request_type_codes",
            postgresql.ARRAY(sa.String(30)),
            nullable=False,
            server_default="{}",
        ),
        sa.Column(
            "dependency_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("pqrsf_dependencies.id", ondelete="SET NULL"),
        ),
        sa.Column("is_other", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
        *_timestamps(),
    )
    op.create_table(
        "pqrsf_causes",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "subject_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("pqrsf_subjects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("code", sa.String(40), nullable=False, unique=True),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("description", sa.String(300)),
        sa.Column(
            "dependency_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("pqrsf_dependencies.id", ondelete="SET NULL"),
        ),
        sa.Column("route", sa.String(10), nullable=False, server_default="none"),
        sa.Column("requires_product", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("allows_product", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("is_other", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
        *_timestamps(),
        sa.CheckConstraint(
            "route IN ('supplier', 'internal', 'none')", name="ck_pqrsf_causes_route"
        ),
        sa.CheckConstraint(
            "NOT requires_product OR allows_product", name="ck_pqrsf_causes_product"
        ),
    )
    op.create_index("ix_pqrsf_causes_subject", "pqrsf_causes", ["subject_id", "sort_order"])

    op.alter_column("pqrsf_requests", "description", existing_type=sa.Text(), nullable=True)
    op.add_column(
        "pqrsf_requests",
        sa.Column(
            "subject_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("pqrsf_subjects.id", ondelete="SET NULL"),
        ),
    )
    op.add_column(
        "pqrsf_requests",
        sa.Column(
            "cause_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("pqrsf_causes.id", ondelete="SET NULL"),
        ),
    )
    op.add_column("pqrsf_requests", sa.Column("products", postgresql.JSONB()))
    op.add_column("pqrsf_requests", sa.Column("pickup_order_requested", sa.String(8)))
    op.add_column("pqrsf_requests", sa.Column("pickup_order_number", sa.String(40)))
    op.create_check_constraint(
        "ck_pqrsf_requests_pickup_order",
        "pqrsf_requests",
        "pickup_order_requested IS NULL OR pickup_order_requested IN ('yes', 'no', 'unknown')",
    )

    dependencies = {
        code: dependency_id
        for code, dependency_id in op.get_bind().execute(
            sa.text("SELECT code, id FROM pqrsf_dependencies")
        )
    }
    subjects = sa.table(
        "pqrsf_subjects",
        sa.column("id", postgresql.UUID(as_uuid=True)),
        sa.column("code", sa.String()),
        sa.column("name", sa.String()),
        sa.column("description", sa.String()),
        sa.column("request_type_codes", postgresql.ARRAY(sa.String())),
        sa.column("dependency_id", postgresql.UUID(as_uuid=True)),
        sa.column("is_other", sa.Boolean()),
        sa.column("sort_order", sa.Integer()),
    )
    causes = sa.table(
        "pqrsf_causes",
        sa.column("id", postgresql.UUID(as_uuid=True)),
        sa.column("subject_id", postgresql.UUID(as_uuid=True)),
        sa.column("code", sa.String()),
        sa.column("name", sa.String()),
        sa.column("dependency_id", postgresql.UUID(as_uuid=True)),
        sa.column("route", sa.String()),
        sa.column("requires_product", sa.Boolean()),
        sa.column("allows_product", sa.Boolean()),
        sa.column("is_other", sa.Boolean()),
        sa.column("sort_order", sa.Integer()),
    )
    subject_rows, cause_rows = [], []
    for position, (code, name, description, types, dependency, is_other, items) in enumerate(
        _SUBJECTS, start=1
    ):
        subject_id = _seed_id("subjects", code)
        subject_rows.append(
            {
                "id": subject_id,
                "code": code,
                "name": name,
                "description": description,
                "request_type_codes": types,
                "dependency_id": dependencies.get(dependency),
                "is_other": is_other,
                "sort_order": position,
            }
        )
        for order, (
            cause,
            cause_name,
            cause_dependency,
            route,
            requires,
            allows,
            other,
        ) in enumerate(items, start=1):
            cause_rows.append(
                {
                    "id": _seed_id("causes", cause),
                    "subject_id": subject_id,
                    "code": cause,
                    "name": cause_name,
                    "dependency_id": dependencies.get(cause_dependency),
                    "route": route,
                    "requires_product": requires,
                    "allows_product": allows,
                    "is_other": other,
                    "sort_order": order,
                }
            )
    op.bulk_insert(subjects, subject_rows)
    op.bulk_insert(causes, cause_rows)


def downgrade() -> None:
    """Drop the catalogs and the new request fields.

    Requests stored without a description get an empty one back so the column can be
    required again.
    """

    op.drop_constraint("ck_pqrsf_requests_pickup_order", "pqrsf_requests", type_="check")
    op.drop_column("pqrsf_requests", "pickup_order_number")
    op.drop_column("pqrsf_requests", "pickup_order_requested")
    op.drop_column("pqrsf_requests", "products")
    op.drop_column("pqrsf_requests", "cause_id")
    op.drop_column("pqrsf_requests", "subject_id")
    op.execute("UPDATE pqrsf_requests SET description = '' WHERE description IS NULL")
    op.alter_column("pqrsf_requests", "description", existing_type=sa.Text(), nullable=False)
    op.drop_index("ix_pqrsf_causes_subject", table_name="pqrsf_causes")
    op.drop_table("pqrsf_causes")
    op.drop_table("pqrsf_subjects")
    op.execute(
        "UPDATE pqrsf_request_types SET allows_attachments = false WHERE code = 'sugerencia'"
    )
    op.drop_column("pqrsf_request_types", "description")
