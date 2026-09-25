"""Create and seed the PQRSF site catalog."""

from collections.abc import Sequence
from uuid import NAMESPACE_URL, UUID, uuid5

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0006_pqrsf_sites"
down_revision: str | None = "0005_user_activity"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_SITES = (
    ("aco", "Aco"),
    ("apartado", "Apartadó"),
    ("barranquilla", "Barranquilla"),
    ("bogota", "Bogotá"),
    ("bucaramanga", "Bucaramanga"),
    ("cartagena", "Cartagena"),
    ("jdh-market-apartado", "JDH Market Apartadó"),
    ("jdh-market-valledupar", "JDH Market Valledupar"),
    ("medellin", "Medellín"),
    ("pereira", "Pereira"),
    ("valledupar", "Valledupar"),
    ("yumbo", "Yumbo"),
)


def _site_id(code: str) -> UUID:
    return uuid5(NAMESPACE_URL, f"https://jdh.com.co/pqrsf/sites/{code}")


def upgrade() -> None:
    """Create the site catalog and insert its fixed initial values."""

    op.create_table(
        "pqrsf_sites",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("code", sa.String(80), nullable=False, unique=True),
        sa.Column("name", sa.String(150), nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )
    sites = sa.table(
        "pqrsf_sites",
        sa.column("id", postgresql.UUID(as_uuid=True)),
        sa.column("code", sa.String(80)),
        sa.column("name", sa.String(150)),
        sa.column("is_active", sa.Boolean()),
    )
    op.bulk_insert(
        sites,
        [
            {"id": _site_id(code), "code": code, "name": name, "is_active": True}
            for code, name in _SITES
        ],
    )


def downgrade() -> None:
    """Drop the site catalog."""

    op.drop_table("pqrsf_sites")
