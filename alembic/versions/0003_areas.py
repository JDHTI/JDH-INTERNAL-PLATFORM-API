"""Areas module: areas, role catalog and members, seeded with the UI's areas."""

import uuid
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0003_areas"
down_revision: str | None = "0002_identity_users"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Captured from internal-platform-ui src/shared/config/areas.config.ts
# (branch feat/add-bi-and-commercial-areas). ``icon`` is the lucide-react name.
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
    """Create areas, area_roles and area_members, then seed the areas."""

    areas = op.create_table(
        "areas",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("slug", sa.String(50), nullable=False),
        sa.Column("name", sa.String(150), nullable=False),
        sa.Column("remit", sa.String(300)),
        sa.Column("icon", sa.String(64)),
        sa.Column("sort_order", sa.Integer, server_default="0", nullable=False),
        sa.Column(
            "responsible_user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
        ),
        *_timestamps(),
        sa.UniqueConstraint("slug", name="areas_slug_key"),
    )
    op.create_index("uq_areas_name_lower", "areas", [sa.text("lower(name)")], unique=True)

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

    op.bulk_insert(
        areas,
        [
            {
                "id": uuid.uuid4(),
                "slug": slug,
                "name": name,
                "icon": icon,
                "remit": remit,
                "sort_order": position * 10,
            }
            for position, (slug, name, icon, remit) in enumerate(SEED_AREAS, start=1)
        ],
    )


def downgrade() -> None:
    """Drop area_members, area_roles and areas."""

    op.drop_index("ix_area_members_user_id", table_name="area_members")
    op.drop_table("area_members")
    op.drop_index("uq_area_roles_area_name_lower", table_name="area_roles")
    op.drop_table("area_roles")
    op.drop_index("uq_areas_name_lower", table_name="areas")
    op.drop_table("areas")
