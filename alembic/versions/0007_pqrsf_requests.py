"""Create and seed the PQRSF request catalog and request table."""

from collections.abc import Sequence
from uuid import NAMESPACE_URL, UUID, uuid5

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0007_pqrsf_requests"
down_revision: str | None = "0006_pqrsf_sites"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TYPES = (
    ("peticion", "Petición", True, True),
    ("queja", "Queja", True, True),
    ("reclamo", "Reclamo", True, True),
    ("sugerencia", "Sugerencia", False, False),
    ("felicitacion", "Felicitación", False, False),
)


def _type_id(code: str) -> UUID:
    return uuid5(NAMESPACE_URL, f"https://jdh.com.co/pqrsf/types/{code}")


def upgrade() -> None:
    """Create request types and submitted requests."""

    op.create_table(
        "pqrsf_request_types",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("code", sa.String(30), nullable=False, unique=True),
        sa.Column("name", sa.String(80), nullable=False),
        sa.Column("allows_reason", sa.Boolean(), nullable=False),
        sa.Column("allows_attachments", sa.Boolean(), nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )
    op.create_table(
        "pqrsf_requests",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("reference", sa.String(50), nullable=False, unique=True),
        sa.Column(
            "request_type_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("pqrsf_request_types.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "site_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("pqrsf_sites.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("full_name", sa.String(200), nullable=False),
        sa.Column("business_name", sa.String(200), nullable=False),
        sa.Column("nit", sa.String(30), nullable=False),
        sa.Column("address", sa.String(300), nullable=False),
        sa.Column("department", sa.String(100), nullable=False),
        sa.Column("city", sa.String(100), nullable=False),
        sa.Column("email", sa.String(320), nullable=False),
        sa.Column("reason", sa.String(200)),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("idempotency_key", sa.String(36), nullable=False, unique=True),
        sa.Column("idempotency_hash", sa.String(64), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )
    op.create_index(
        "ix_pqrsf_requests_type_created", "pqrsf_requests", ["request_type_id", "created_at"]
    )
    op.create_index("ix_pqrsf_requests_site_created", "pqrsf_requests", ["site_id", "created_at"])
    op.create_index("ix_pqrsf_requests_created_at", "pqrsf_requests", ["created_at"])

    types = sa.table(
        "pqrsf_request_types",
        sa.column("id", postgresql.UUID(as_uuid=True)),
        sa.column("code", sa.String(30)),
        sa.column("name", sa.String(80)),
        sa.column("allows_reason", sa.Boolean()),
        sa.column("allows_attachments", sa.Boolean()),
        sa.column("is_active", sa.Boolean()),
    )
    op.bulk_insert(
        types,
        [
            {
                "id": _type_id(code),
                "code": code,
                "name": name,
                "allows_reason": allows_reason,
                "allows_attachments": allows_attachments,
                "is_active": True,
            }
            for code, name, allows_reason, allows_attachments in _TYPES
        ],
    )


def downgrade() -> None:
    """Drop PQRSF requests and their type catalog."""

    op.drop_index("ix_pqrsf_requests_created_at", table_name="pqrsf_requests")
    op.drop_index("ix_pqrsf_requests_site_created", table_name="pqrsf_requests")
    op.drop_index("ix_pqrsf_requests_type_created", table_name="pqrsf_requests")
    op.drop_table("pqrsf_requests")
    op.drop_table("pqrsf_request_types")
