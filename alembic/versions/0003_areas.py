"""Areas module: area space columns, role catalog and members, seeded with the UI's areas.

The ``areas`` table comes from ``0002_identity`` (tenant_id, name). This
revision adds what the areas module needs on the same row (slug, remit, icon,
sort_order, responsible), gives existing areas a slug, seeds the areas the UI
used to mock, and creates the per-area role catalog and memberships.
"""

import re
import unicodedata
import uuid
from collections.abc import Sequence
from datetime import UTC, datetime

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

from app.core.config import settings

revision: str = "0003_areas"
down_revision: str | None = "0002_identity"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Same fallback the identity service uses for users, so seeded areas and
# signed-in users share a tenant.
TENANT_ID = settings.ENTRA_TENANT_ID or "development"

# Captured from internal-platform-ui src/shared/config/areas.config.ts.
# ``icon`` is the lucide-react component name.
SEED_AREAS = [
    ("ti", "TI", "Server", "Infraestructura, soporte y accesos de la compañía."),
    (
        "calidad",
        "Calidad",
        "ClipboardCheck",
        "PQRSF, auditorías y documentación del sistema de gestión.",
    ),
    ("tesoreria", "Tesorería", "Landmark", "Pagos, bancos y disponibilidad de efectivo."),
    ("cartera", "Cartera", "HandCoins", "Recaudo, acuerdos de pago y edades de cartera."),
    ("mercadeo", "Mercadeo", "Megaphone", "Campañas, material POP y marca."),
    ("contabilidad", "Contabilidad", "Calculator", "Causación, cierres y activos fijos."),
    (
        "bi",
        "BI",
        "TrendingUp",
        "Inteligencia de negocios, indicadores y análisis para la toma de decisiones.",
    ),
    (
        "comercial",
        "Comercial",
        "Building2",
        "Ventas, clientes y seguimiento de la gestión comercial.",
    ),
]


def _slugify(name: str) -> str:
    ascii_name = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    slug = re.sub(r"[^a-z0-9]+", "-", ascii_name.lower()).strip("-")
    return slug[:50].strip("-") or "area"


def _timestamps() -> list[sa.Column]:
    return [
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    ]


def _backfill_and_seed() -> None:
    """Give every existing area a unique slug, then merge in the seed areas."""

    connection = op.get_bind()
    rows = connection.execute(sa.text("SELECT id, tenant_id, name FROM areas ORDER BY created_at"))
    rows = rows.all()
    seed_by_name = {name.lower(): (slug, icon, remit) for slug, name, icon, remit in SEED_AREAS}
    # Seed slugs are reserved for the seed areas, so an unrelated existing area
    # never takes /ti or /calidad.
    seed_slugs = {slug for slug, *_ in SEED_AREAS}
    taken: set[str] = set()

    # Existing areas that match a seed name (in the seed tenant) take the
    # seed's slug and metadata instead of creating a duplicate.
    matched: set[str] = set()
    for area_id, tenant_id, name in rows:
        seed = seed_by_name.get(name.lower()) if tenant_id == TENANT_ID else None
        if seed and seed[0] not in taken:
            slug, icon, remit = seed
            position = next(i for i, s in enumerate(SEED_AREAS, start=1) if s[0] == slug)
            connection.execute(
                sa.text(
                    "UPDATE areas SET slug = :slug, icon = :icon, remit = :remit, "
                    "sort_order = :sort_order WHERE id = :id"
                ),
                {
                    "slug": slug,
                    "icon": icon,
                    "remit": remit,
                    "sort_order": position * 10,
                    "id": area_id,
                },
            )
            taken.add(slug)
            matched.add(slug)
            continue
        base = _slugify(name)
        slug, suffix = base, 2
        while slug in taken or slug in seed_slugs:
            slug = f"{base[:46]}-{suffix}"
            suffix += 1
        connection.execute(
            sa.text("UPDATE areas SET slug = :slug WHERE id = :id"), {"slug": slug, "id": area_id}
        )
        taken.add(slug)

    now = datetime.now(UTC)
    areas = sa.table(
        "areas",
        sa.column("id", postgresql.UUID(as_uuid=True)),
        sa.column("tenant_id", sa.String()),
        sa.column("name", sa.String()),
        sa.column("slug", sa.String()),
        sa.column("remit", sa.String()),
        sa.column("icon", sa.String()),
        sa.column("sort_order", sa.Integer()),
        sa.column("created_at", sa.DateTime(timezone=True)),
        sa.column("updated_at", sa.DateTime(timezone=True)),
    )
    missing = [
        {
            "id": uuid.uuid4(),
            "tenant_id": TENANT_ID,
            "name": name,
            "slug": slug,
            "remit": remit,
            "icon": icon,
            "sort_order": position * 10,
            "created_at": now,
            "updated_at": now,
        }
        for position, (slug, name, icon, remit) in enumerate(SEED_AREAS, start=1)
        if slug not in matched
    ]
    if missing:
        op.bulk_insert(areas, missing)


def upgrade() -> None:
    """Extend areas, seed them, and create area_roles and area_members."""

    op.add_column("areas", sa.Column("slug", sa.String(50), nullable=True))
    op.add_column("areas", sa.Column("remit", sa.String(300), nullable=True))
    op.add_column("areas", sa.Column("icon", sa.String(64), nullable=True))
    op.add_column(
        "areas", sa.Column("sort_order", sa.Integer(), server_default="0", nullable=False)
    )
    op.add_column(
        "areas",
        sa.Column("responsible_user_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        "areas_responsible_user_id_fkey",
        "areas",
        "users",
        ["responsible_user_id"],
        ["id"],
        ondelete="SET NULL",
    )

    _backfill_and_seed()
    op.alter_column("areas", "slug", nullable=False)
    op.create_unique_constraint("areas_slug_key", "areas", ["slug"])

    op.create_table(
        "area_roles",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "area_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("areas.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.String(100), nullable=False),
        *_timestamps(),
        sa.UniqueConstraint("area_id", "id", name="uq_area_roles_area_id_id"),
    )
    op.create_index(
        "uq_area_roles_area_name_lower",
        "area_roles",
        ["area_id", sa.text("lower(name)")],
        unique=True,
    )

    op.create_table(
        "area_members",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "area_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("areas.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("role_id", postgresql.UUID(as_uuid=True), nullable=False),
        *_timestamps(),
        sa.UniqueConstraint("area_id", "user_id", name="uq_area_members_area_user"),
        # NO ACTION: validated at statement end, so the area CASCADE passes.
        sa.ForeignKeyConstraint(
            ["area_id", "role_id"],
            ["area_roles.area_id", "area_roles.id"],
            name="fk_area_members_role_same_area",
        ),
    )
    op.create_index("ix_area_members_user_id", "area_members", ["user_id"])


def downgrade() -> None:
    """Drop memberships and roles, then the area space columns.

    Areas inserted by the seed stay: from 0002's point of view they are
    ordinary organization areas, and users may already be assigned to them.
    """

    op.drop_index("ix_area_members_user_id", table_name="area_members")
    op.drop_table("area_members")
    op.drop_index("uq_area_roles_area_name_lower", table_name="area_roles")
    op.drop_table("area_roles")
    op.drop_constraint("areas_slug_key", "areas", type_="unique")
    op.drop_constraint("areas_responsible_user_id_fkey", "areas", type_="foreignkey")
    for column in ("responsible_user_id", "sort_order", "icon", "remit", "slug"):
        op.drop_column("areas", column)
