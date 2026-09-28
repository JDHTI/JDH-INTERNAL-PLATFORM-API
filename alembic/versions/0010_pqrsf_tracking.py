"""Add PQRSF follow-up: statuses, dependencies, document types, cases and timeline.

Request types get their consecutive prefix and default response time. Every
existing request receives its case in the initial status so the inbox starts
complete. The document type catalog and its response times come from the
P.13.F.01 spreadsheet.
"""

from collections.abc import Sequence
from datetime import date, datetime, timedelta
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5
from zoneinfo import ZoneInfo

import holidays
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0010_pqrsf_tracking"
down_revision: str | None = "0009_document_tag_colors"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TIMEZONE = ZoneInfo("America/Bogota")

# code: (prefix, requires_follow_up, default business days)
_TYPE_SETTINGS = {
    "peticion": ("P", True, 15),
    "queja": ("Q", True, 15),
    "reclamo": ("R", True, 15),
    "sugerencia": ("S", True, 15),
    "felicitacion": ("F", False, None),
}

# code, name, color, sort_order, is_initial, is_terminal
_STATUSES = (
    ("recibida", "Recibida", "#64748b", 10, True, False),
    ("leida", "Leída", "#0284c7", 20, False, False),
    ("en_curso", "En curso", "#d97706", 30, False, False),
    ("finalizada", "Finalizada", "#16a34a", 40, False, True),
)

_DEPENDENCIES = (
    ("comercial", "Comercial"),
    ("distribucion", "Distribución"),
    ("almacenamiento", "Almacenamiento"),
    ("abastecimiento", "Abastecimiento"),
    ("calidad", "Calidad"),
    ("food_market", "Food Market"),
)

# Name and "Tiempo de respuesta Establecido" observed in P.13.F.01.
_DOCUMENT_TYPES = (
    ("Certificado Calidad", 3),
    ("Carta de Garantía JDH (Relación Lotes Despachados)", 2),
    ("Ficha Técnica", 5),
    ("Concepto Sanitario Cedi JDH", 2),
    ("Registro Sanitario", 5),
    ("Copia Carnet Curso Manipulación Alimentos", 3),
    ("Certificado SIG (ISO 9001, 14001, 45001)", 1),
    ("Hoja de Seguridad", 5),
    ("Concepto Sanitario Vehículo JDH", 3),
    ("Concepto Sanitario Proveedor u Fabricante", 5),
    ("Formato Autorización Orden de Recogida", 2),
    ("Certificado Microbiológico (MQ)", 5),
    ("Procedimiento Abastecimiento Agua Potable", 2),
    ("Procedimiento Gestión Limpieza y Desinfección", 2),
    ("Certificado Fumigación Vehículo JDH", 2),
    ("Certificado Libre de GMO Fabricante", 5),
    ("Certificado Análisis Especiales", 5),
    ("Certificado Limpieza y Desinfección Vehículo JDH", 2),
    ("Certificado Análisis Migración", 5),
    ("Procedimiento Manejo Integral Residuos Sólidos", 2),
    ("Certificación Ejercicio de Trazabilidad", 5),
    ("Respuesta Formal Certificación", 3),
    ("Copia Examen Aptitud para Manipular Alimentos", 3),
    ("Política Integrada del Sistema de Gestión", 1),
    ("Procedimiento Capacitación, Formación y Toma de Conciencia", 2),
    ("Programa Control Integrado de Plagas", 2),
    ("Documentos Plan de Saneamiento Básico", 3),
    ("Certificado Análisis (COA)", 5),
    ("Procedimiento Manejo Integrado de Plagas", 2),
    ("Copia Carnet Curso Manipulación Alimentos Trans Entregamos", 3),
    ("Información", 3),
    ("Certificaciones Proveedor JDH", 5),
    ("Carta de Garantía Alérgenos Fabricante", 5),
    ("RUT JDH", 1),
    ("Certificado Fisicoquímico (FQ)", 5),
    ("Certificado Empaque/Envase", 5),
    ("Cámara de Comercio JDH", 3),
    ("Carta de Solicitud de Visita Concepto Sanitario", 3),
    ("Copia Registro de Capacitaciones", 3),
    ("Certificado Declaración de Alérgenos", 5),
    ("Certificado Pesticidas", 5),
    ("Certificado Fumigación Cedi JDH", 2),
    ("Certificado Especificación de PT", 5),
    ("Certificado Contaminante", 5),
    ("Procedimiento Gestión de Recall", 2),
    ("Respuesta Formal con Análisis de Causa y Plan de Acción", 5),
    ("Procedimiento de Gestión de PQRSF", 2),
    ("Procedimiento Recibo y Almacenamiento", 2),
    ("Registro de Asistencia Capacitación", 1),
    ("Comunicado-Carta Aclaratoria Fabricante", 5),
    ("Documento Mapa de Procesos", 1),
    ("Copia Cédula Representante Legal JDH", 3),
    ("Estados Financieros JDH", 2),
    ("Carta de Garantía Materiales Extraños Fabricante", 5),
    ("Certificación Bancaria JDH", 2),
    ("Certificado Análisis Bromatológico", 5),
    ("Programa Trazabilidad", 5),
    ("Política Inventarios", 1),
    ("Política Manejo de Devoluciones", 2),
    ("Instructivo Manejo Devoluciones JDH", 1),
    ("Certificado Formulaciones", 5),
    ("Certificado Metales Pesados", 5),
)


def _seed_id(kind: str, key: str) -> UUID:
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


def _due_on(start: date, days: int) -> date:
    """First date whose inclusive business-day count reaches ``days``."""

    non_working = holidays.country_holidays("CO", years=range(start.year, start.year + 2))
    count, day = 0, start
    while True:
        if day.weekday() < 5 and day not in non_working:
            count += 1
            if count == days:
                return day
        day += timedelta(days=1)


def upgrade() -> None:
    """Create the follow-up tables, seed catalogs and open cases for existing requests."""

    op.add_column("pqrsf_request_types", sa.Column("prefix", sa.String(5)))
    op.add_column(
        "pqrsf_request_types",
        sa.Column("requires_follow_up", sa.Boolean(), server_default=sa.true(), nullable=False),
    )
    op.add_column("pqrsf_request_types", sa.Column("sla_days", sa.Integer()))
    connection = op.get_bind()
    for code, (prefix, follow_up, sla_days) in _TYPE_SETTINGS.items():
        connection.execute(
            sa.text(
                "UPDATE pqrsf_request_types SET prefix = :prefix, "
                "requires_follow_up = :follow_up, sla_days = :sla_days WHERE code = :code"
            ),
            {"prefix": prefix, "follow_up": follow_up, "sla_days": sla_days, "code": code},
        )
    connection.execute(
        sa.text("UPDATE pqrsf_request_types SET prefix = upper(left(code, 5)) WHERE prefix IS NULL")
    )
    op.alter_column("pqrsf_request_types", "prefix", nullable=False)
    op.create_unique_constraint("pqrsf_request_types_prefix_key", "pqrsf_request_types", ["prefix"])

    op.create_table(
        "pqrsf_case_statuses",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("code", sa.String(40), nullable=False, unique=True),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("color", sa.String(7)),
        sa.Column("sort_order", sa.Integer(), server_default="0", nullable=False),
        sa.Column("is_initial", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("is_terminal", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default=sa.true(), nullable=False),
        *_timestamps(),
    )
    op.create_index(
        "uq_pqrsf_case_statuses_single_initial",
        "pqrsf_case_statuses",
        ["is_initial"],
        unique=True,
        postgresql_where=sa.text("is_initial"),
    )
    op.create_table(
        "pqrsf_dependencies",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("code", sa.String(40), nullable=False, unique=True),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("sort_order", sa.Integer(), server_default="0", nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default=sa.true(), nullable=False),
        *_timestamps(),
    )
    op.create_table(
        "pqrsf_document_types",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("sla_days", sa.Integer(), nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default=sa.true(), nullable=False),
        *_timestamps(),
        sa.CheckConstraint("sla_days >= 0", name="ck_pqrsf_document_types_sla_days"),
    )
    op.create_index(
        "uq_pqrsf_document_types_name",
        "pqrsf_document_types",
        [sa.text("lower(name)")],
        unique=True,
    )
    op.create_table(
        "pqrsf_case_sequences",
        sa.Column(
            "request_type_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("pqrsf_request_types.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("year", sa.Integer(), nullable=False),
        sa.Column("last_value", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("request_type_id", "year", name="pk_pqrsf_case_sequences"),
    )
    op.create_table(
        "pqrsf_cases",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("code", sa.String(30), nullable=False),
        sa.Column("number", sa.Integer(), nullable=False),
        sa.Column("year", sa.Integer(), nullable=False),
        sa.Column(
            "request_type_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("pqrsf_request_types.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "status_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("pqrsf_case_statuses.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("origin", sa.String(20), nullable=False),
        sa.Column("subject", sa.String(200)),
        sa.Column("description", sa.Text()),
        sa.Column("received_on", sa.Date(), nullable=False),
        sa.Column("due_on", sa.Date()),
        sa.Column(
            "site_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("pqrsf_sites.id", ondelete="RESTRICT"),
        ),
        sa.Column(
            "dependency_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("pqrsf_dependencies.id", ondelete="RESTRICT"),
        ),
        sa.Column("assignee_user_id", postgresql.UUID(as_uuid=True)),
        sa.Column("customer_name", sa.String(200)),
        sa.Column("customer_nit", sa.String(30)),
        sa.Column("supplier_name", sa.String(200)),
        sa.Column("sales_executive_name", sa.String(200)),
        sa.Column("requester_name", sa.String(200)),
        sa.Column("requester_email", sa.String(320)),
        sa.Column(
            "request_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("pqrsf_requests.id", ondelete="SET NULL"),
        ),
        sa.Column("created_by_user_id", postgresql.UUID(as_uuid=True)),
        sa.Column("first_read_at", sa.DateTime(timezone=True)),
        sa.Column("closed_at", sa.DateTime(timezone=True)),
        sa.Column("legacy", postgresql.JSONB()),
        *_timestamps(),
        sa.UniqueConstraint("code", name="pqrsf_cases_code_key"),
        sa.UniqueConstraint("request_id", name="pqrsf_cases_request_id_key"),
        sa.UniqueConstraint(
            "request_type_id", "year", "number", name="pqrsf_cases_type_year_number_key"
        ),
        sa.CheckConstraint(
            "origin IN ('public_form', 'internal', 'import')", name="ck_pqrsf_cases_origin"
        ),
    )
    op.create_index("ix_pqrsf_cases_status", "pqrsf_cases", ["status_id"])
    op.create_index(
        "ix_pqrsf_cases_assignee_status", "pqrsf_cases", ["assignee_user_id", "status_id"]
    )
    op.create_index(
        "ix_pqrsf_cases_type_received", "pqrsf_cases", ["request_type_id", "received_on"]
    )
    op.create_index("ix_pqrsf_cases_received_on", "pqrsf_cases", ["received_on"])
    op.create_index("ix_pqrsf_cases_due_on", "pqrsf_cases", ["due_on"])
    op.create_index("ix_pqrsf_cases_created_by", "pqrsf_cases", ["created_by_user_id"])
    op.create_index(
        "ix_pqrsf_cases_legacy_source_key",
        "pqrsf_cases",
        [sa.text("(legacy ->> 'source_key')")],
        postgresql_where=sa.text("legacy IS NOT NULL"),
    )
    op.create_table(
        "pqrsf_case_items",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "case_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("pqrsf_cases.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "document_type_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("pqrsf_document_types.id", ondelete="RESTRICT"),
        ),
        sa.Column("description", sa.String(300)),
        sa.Column("quantity", sa.Integer()),
        sa.Column("sla_days", sa.Integer()),
        sa.Column("due_on", sa.Date()),
        sa.Column("responded_on", sa.Date()),
        sa.Column("response_days", sa.Integer()),
        sa.Column("delay_cause", sa.String(300)),
        sa.Column("delay_detail", sa.Text()),
        sa.Column("sort_order", sa.Integer(), server_default="0", nullable=False),
        *_timestamps(),
        sa.CheckConstraint("quantity IS NULL OR quantity > 0", name="ck_pqrsf_case_items_quantity"),
    )
    op.create_index("ix_pqrsf_case_items_case", "pqrsf_case_items", ["case_id", "sort_order"])
    op.create_table(
        "pqrsf_case_events",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "case_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("pqrsf_cases.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("event_type", sa.String(30), nullable=False),
        sa.Column("actor_user_id", postgresql.UUID(as_uuid=True)),
        sa.Column(
            "from_status_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("pqrsf_case_statuses.id", ondelete="RESTRICT"),
        ),
        sa.Column(
            "to_status_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("pqrsf_case_statuses.id", ondelete="RESTRICT"),
        ),
        sa.Column("note", sa.Text()),
        sa.Column("data", postgresql.JSONB(), server_default="{}", nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )
    op.create_index(
        "ix_pqrsf_case_events_case_created", "pqrsf_case_events", ["case_id", "created_at"]
    )

    op.bulk_insert(
        sa.table(
            "pqrsf_case_statuses",
            sa.column("id", postgresql.UUID(as_uuid=True)),
            sa.column("code", sa.String()),
            sa.column("name", sa.String()),
            sa.column("color", sa.String()),
            sa.column("sort_order", sa.Integer()),
            sa.column("is_initial", sa.Boolean()),
            sa.column("is_terminal", sa.Boolean()),
        ),
        [
            {
                "id": _seed_id("statuses", code),
                "code": code,
                "name": name,
                "color": color,
                "sort_order": sort_order,
                "is_initial": is_initial,
                "is_terminal": is_terminal,
            }
            for code, name, color, sort_order, is_initial, is_terminal in _STATUSES
        ],
    )
    op.bulk_insert(
        sa.table(
            "pqrsf_dependencies",
            sa.column("id", postgresql.UUID(as_uuid=True)),
            sa.column("code", sa.String()),
            sa.column("name", sa.String()),
            sa.column("sort_order", sa.Integer()),
        ),
        [
            {"id": _seed_id("dependencies", code), "code": code, "name": name, "sort_order": i}
            for i, (code, name) in enumerate(_DEPENDENCIES, start=1)
        ],
    )
    op.bulk_insert(
        sa.table(
            "pqrsf_document_types",
            sa.column("id", postgresql.UUID(as_uuid=True)),
            sa.column("name", sa.String()),
            sa.column("sla_days", sa.Integer()),
        ),
        [
            {"id": _seed_id("document-types", name.lower()), "name": name, "sla_days": sla}
            for name, sla in _DOCUMENT_TYPES
        ],
    )
    _open_cases_for_existing_requests(connection)


def _open_cases_for_existing_requests(connection: sa.engine.Connection) -> None:
    initial_status = _seed_id("statuses", "recibida")
    types = {
        row.id: row
        for row in connection.execute(
            sa.text("SELECT id, prefix, requires_follow_up, sla_days FROM pqrsf_request_types")
        )
    }
    requests = connection.execute(
        sa.text(
            "SELECT id, request_type_id, site_id, reason, description, business_name, nit, "
            "full_name, email, created_at FROM pqrsf_requests ORDER BY created_at, id"
        )
    ).all()
    counters: dict[tuple[UUID, int], int] = {}
    cases, events = [], []
    for request in requests:
        request_type = types[request.request_type_id]
        received_on = request.created_at.astimezone(_TIMEZONE).date()
        key = (request.request_type_id, received_on.year)
        counters[key] = counters.get(key, 0) + 1
        number = counters[key]
        due_on = (
            _due_on(received_on, request_type.sla_days)
            if request_type.requires_follow_up and request_type.sla_days
            else None
        )
        case_id = uuid4()
        cases.append(
            {
                "id": case_id,
                "code": f"{request_type.prefix}-{received_on.year}-{number:05d}",
                "number": number,
                "year": received_on.year,
                "request_type_id": request.request_type_id,
                "status_id": initial_status,
                "origin": "public_form",
                "subject": request.reason,
                "description": request.description,
                "received_on": received_on,
                "due_on": due_on,
                "site_id": request.site_id,
                "customer_name": request.business_name,
                "customer_nit": request.nit,
                "requester_name": request.full_name,
                "requester_email": request.email,
                "request_id": request.id,
                "created_at": request.created_at,
            }
        )
        events.append(
            {
                "id": uuid4(),
                "case_id": case_id,
                "event_type": "created",
                "data": {"origin": "public_form", "backfilled": True},
                "created_at": datetime.now(_TIMEZONE),
            }
        )
    if not cases:
        return
    case_table = sa.table(
        "pqrsf_cases",
        *(sa.column(name) for name in cases[0] if name not in {"id", "request_id"}),
        sa.column("id", postgresql.UUID(as_uuid=True)),
        sa.column("request_id", postgresql.UUID(as_uuid=True)),
    )
    op.bulk_insert(case_table, cases)
    op.bulk_insert(
        sa.table(
            "pqrsf_case_events",
            sa.column("id", postgresql.UUID(as_uuid=True)),
            sa.column("case_id", postgresql.UUID(as_uuid=True)),
            sa.column("event_type", sa.String()),
            sa.column("data", postgresql.JSONB()),
            sa.column("created_at", sa.DateTime(timezone=True)),
        ),
        events,
    )
    op.bulk_insert(
        sa.table(
            "pqrsf_case_sequences",
            sa.column("request_type_id", postgresql.UUID(as_uuid=True)),
            sa.column("year", sa.Integer()),
            sa.column("last_value", sa.Integer()),
        ),
        [
            {"request_type_id": type_id, "year": year, "last_value": value}
            for (type_id, year), value in counters.items()
        ],
    )


def downgrade() -> None:
    """Drop the follow-up tables and the request type settings."""

    op.drop_index("ix_pqrsf_case_events_case_created", table_name="pqrsf_case_events")
    op.drop_table("pqrsf_case_events")
    op.drop_index("ix_pqrsf_case_items_case", table_name="pqrsf_case_items")
    op.drop_table("pqrsf_case_items")
    for index in (
        "ix_pqrsf_cases_legacy_source_key",
        "ix_pqrsf_cases_created_by",
        "ix_pqrsf_cases_due_on",
        "ix_pqrsf_cases_received_on",
        "ix_pqrsf_cases_type_received",
        "ix_pqrsf_cases_assignee_status",
        "ix_pqrsf_cases_status",
    ):
        op.drop_index(index, table_name="pqrsf_cases")
    op.drop_table("pqrsf_cases")
    op.drop_table("pqrsf_case_sequences")
    op.drop_index("uq_pqrsf_document_types_name", table_name="pqrsf_document_types")
    op.drop_table("pqrsf_document_types")
    op.drop_table("pqrsf_dependencies")
    op.drop_index("uq_pqrsf_case_statuses_single_initial", table_name="pqrsf_case_statuses")
    op.drop_table("pqrsf_case_statuses")
    op.drop_constraint("pqrsf_request_types_prefix_key", "pqrsf_request_types", type_="unique")
    op.drop_column("pqrsf_request_types", "sla_days")
    op.drop_column("pqrsf_request_types", "requires_follow_up")
    op.drop_column("pqrsf_request_types", "prefix")
