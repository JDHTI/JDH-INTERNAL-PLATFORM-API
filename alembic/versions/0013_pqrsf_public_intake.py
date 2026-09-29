"""Public intake: subject, document requests and document categories.

The public form asks for a subject instead of a reason, so ``reason`` becomes
``subject`` and the per-type ``allows_reason`` switch goes away: every type
takes a subject. A petition can be flagged as a document request; the
customer then picks a category and the documents in it, which the case opens
with as items (with their response times) and, when the category has one, its
dependency.

Categories group the document types. A type without category is not offered on
the public form, which is what keeps JDH's own formal answers out of it. The
seeded groups and dependencies are defaults, editable as data.
"""

from collections.abc import Sequence
from uuid import NAMESPACE_URL, UUID, uuid5

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0013_pqrsf_public_intake"
down_revision: str | None = "0012_pqrsf_history"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# code, name, default dependency code, sort order
_CATEGORIES: list[tuple[str, str, str | None, int]] = [
    ("producto", "Producto y fabricante", "calidad", 1),
    ("legal", "Legal y financiero de JDH", None, 2),
    ("sanitario", "Sanitario y operación", "almacenamiento", 3),
    ("sistema", "Sistema de gestión", "calidad", 4),
    ("personal", "Personal y capacitación", "calidad", 5),
    ("otro", "Otro", None, 6),
]

_DOCUMENTS: dict[str, list[str]] = {
    "producto": [
        "Certificado Análisis (COA)",
        "Certificado Análisis Bromatológico",
        "Certificado Análisis Especiales",
        "Certificado Análisis Migración",
        "Certificado Calidad",
        "Certificado Contaminante",
        "Certificado Declaración de Alérgenos",
        "Certificado Empaque/Envase",
        "Certificado Especificación de PT",
        "Certificado Fisicoquímico (FQ)",
        "Certificado Formulaciones",
        "Certificado Libre de GMO Fabricante",
        "Certificado Metales Pesados",
        "Certificado Microbiológico (MQ)",
        "Certificado Pesticidas",
        "Ficha Técnica",
        "Hoja de Seguridad",
        "Registro Sanitario",
        "Carta de Garantía Alérgenos Fabricante",
        "Carta de Garantía Materiales Extraños Fabricante",
        "Comunicado-Carta Aclaratoria Fabricante",
        "Concepto Sanitario Proveedor u Fabricante",
    ],
    "legal": [
        "Certificación Bancaria JDH",
        "Cámara de Comercio JDH",
        "Copia Cédula Representante Legal JDH",
        "Estados Financieros JDH",
        "RUT JDH",
        "Certificaciones Proveedor JDH",
        "Carta de Garantía JDH (Relación Lotes Despachados)",
    ],
    "sanitario": [
        "Concepto Sanitario Cedi JDH",
        "Concepto Sanitario Vehículo JDH",
        "Certificado Fumigación Cedi JDH",
        "Certificado Fumigación Vehículo JDH",
        "Certificado Limpieza y Desinfección Vehículo JDH",
        "Carta de Solicitud de Visita Concepto Sanitario",
        "Formato Autorización Orden de Recogida",
        "Documentos Plan de Saneamiento Básico",
    ],
    "sistema": [
        "Certificado SIG (ISO 9001, 14001, 45001)",
        "Política Integrada del Sistema de Gestión",
        "Política Inventarios",
        "Política Manejo de Devoluciones",
        "Instructivo Manejo Devoluciones JDH",
        "Documento Mapa de Procesos",
        "Procedimiento Abastecimiento Agua Potable",
        "Procedimiento Capacitación, Formación y Toma de Conciencia",
        "Procedimiento Gestión Limpieza y Desinfección",
        "Procedimiento Gestión de Recall",
        "Procedimiento Manejo Integrado de Plagas",
        "Procedimiento Manejo Integral Residuos Sólidos",
        "Procedimiento Recibo y Almacenamiento",
        "Procedimiento de Gestión de PQRSF",
        "Programa Control Integrado de Plagas",
        "Programa Trazabilidad",
        "Certificación Ejercicio de Trazabilidad",
    ],
    "personal": [
        "Copia Carnet Curso Manipulación Alimentos",
        "Copia Carnet Curso Manipulación Alimentos Trans Entregamos",
        "Copia Examen Aptitud para Manipular Alimentos",
        "Copia Registro de Capacitaciones",
        "Registro de Asistencia Capacitación",
    ],
    "otro": ["Información"],
}


def _category_id(code: str) -> UUID:
    return uuid5(NAMESPACE_URL, f"https://jdh.com.co/pqrsf/document-categories/{code}")


def upgrade() -> None:
    """Rename reason to subject, add the document request fields and the categories."""

    op.alter_column("pqrsf_requests", "reason", new_column_name="subject")
    op.add_column(
        "pqrsf_requests",
        sa.Column("document_request", sa.Boolean(), nullable=False, server_default="false"),
    )
    op.add_column("pqrsf_requests", sa.Column("document_category_id", postgresql.UUID()))
    op.add_column("pqrsf_requests", sa.Column("document_items", postgresql.JSONB()))

    op.drop_column("pqrsf_request_types", "allows_reason")
    op.add_column(
        "pqrsf_request_types",
        sa.Column("allows_document_request", sa.Boolean(), nullable=False, server_default="false"),
    )
    op.execute(
        "UPDATE pqrsf_request_types SET allows_document_request = true WHERE code = 'peticion'"
    )

    categories = op.create_table(
        "pqrsf_document_categories",
        sa.Column("id", postgresql.UUID(), primary_key=True),
        sa.Column("code", sa.String(40), nullable=False, unique=True),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column(
            "dependency_id",
            postgresql.UUID(),
            sa.ForeignKey("pqrsf_dependencies.id", ondelete="SET NULL"),
        ),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )
    op.create_foreign_key(
        "pqrsf_requests_document_category_id_fkey",
        "pqrsf_requests",
        "pqrsf_document_categories",
        ["document_category_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.add_column(
        "pqrsf_document_types",
        sa.Column(
            "category_id",
            postgresql.UUID(),
            sa.ForeignKey("pqrsf_document_categories.id", ondelete="SET NULL"),
        ),
    )
    op.create_index("ix_pqrsf_document_types_category", "pqrsf_document_types", ["category_id"])

    connection = op.get_bind()
    dependencies = {
        code: dependency_id
        for dependency_id, code in connection.execute(
            sa.text("SELECT id, code FROM pqrsf_dependencies")
        )
    }
    op.bulk_insert(
        categories,
        [
            {
                "id": _category_id(code),
                "code": code,
                "name": name,
                "dependency_id": dependencies.get(dependency),
                "sort_order": order,
            }
            for code, name, dependency, order in _CATEGORIES
        ],
    )
    for code, names in _DOCUMENTS.items():
        connection.execute(
            sa.text(
                "UPDATE pqrsf_document_types SET category_id = :category "
                "WHERE lower(name) = ANY(:names)"
            ),
            {"category": _category_id(code), "names": [name.lower() for name in names]},
        )


def downgrade() -> None:
    """Restore the reason column and drop the categories."""

    op.drop_index("ix_pqrsf_document_types_category", table_name="pqrsf_document_types")
    op.drop_column("pqrsf_document_types", "category_id")
    op.drop_constraint(
        "pqrsf_requests_document_category_id_fkey", "pqrsf_requests", type_="foreignkey"
    )
    op.drop_table("pqrsf_document_categories")

    op.add_column(
        "pqrsf_request_types",
        sa.Column("allows_reason", sa.Boolean(), nullable=False, server_default="false"),
    )
    op.execute(
        "UPDATE pqrsf_request_types SET allows_reason = true "
        "WHERE code IN ('peticion', 'queja', 'reclamo')"
    )
    op.alter_column("pqrsf_request_types", "allows_reason", server_default=None)
    op.drop_column("pqrsf_request_types", "allows_document_request")

    op.drop_column("pqrsf_requests", "document_items")
    op.drop_column("pqrsf_requests", "document_category_id")
    op.drop_column("pqrsf_requests", "document_request")
    op.alter_column("pqrsf_requests", "subject", new_column_name="reason")
