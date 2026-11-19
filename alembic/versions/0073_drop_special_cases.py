"""The BI «Casos especiales» module goes away completely (2026-11-19).

Drops ``supplier_special_cases`` (two cases in production when it was removed) and the
``special_case_id`` link of the supports. The downgrade brings the structure back, empty.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0073_drop_special_cases"
down_revision: str | None = "0072_quotation_sent_draft"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_CASE_STATUSES = ("pendiente", "en_analisis", "programado", "implementado", "descartado")
_CASE_PRIORITIES = ("baja", "media", "alta")


def _in(column: str, values: tuple[str, ...]) -> str:
    return f"{column} IN ({', '.join(repr(value) for value in values)})"


def upgrade() -> None:
    """Drop the supports' link and the cases."""

    op.drop_index("ix_supplier_supports_special_case", table_name="supplier_supports")
    op.drop_column("supplier_supports", "special_case_id")
    op.drop_table("supplier_special_cases")


def downgrade() -> None:
    """The table and the link again, empty (as ``0019`` created them)."""

    op.create_table(
        "supplier_special_cases",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("code", sa.String(20), nullable=False),
        sa.Column("title", sa.Text, nullable=False),
        sa.Column(
            "commercial_supplier_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("commercial_suppliers.id", ondelete="SET NULL"),
        ),
        sa.Column("customer_nit", sa.Text),
        sa.Column("customer_name", sa.Text),
        sa.Column("request", sa.Text, nullable=False),
        sa.Column("rule_to_program", sa.Text),
        sa.Column("status", sa.String(20), nullable=False, server_default="pendiente"),
        sa.Column("priority", sa.String(10), nullable=False, server_default="media"),
        sa.Column("assignee", sa.String(320)),
        sa.Column("resolution", sa.Text),
        sa.Column("source_key", sa.Text),
        sa.Column("created_by", sa.String(320), nullable=False),
        sa.Column("updated_by", sa.String(320), nullable=False),
        sa.Column("version", sa.Integer, nullable=False, server_default="1"),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.UniqueConstraint("code", name="uq_supplier_special_cases_code"),
        sa.CheckConstraint(_in("status", _CASE_STATUSES), name="ck_supplier_special_cases_status"),
        sa.CheckConstraint(
            _in("priority", _CASE_PRIORITIES), name="ck_supplier_special_cases_priority"
        ),
    )
    op.create_index("ix_supplier_special_cases_status", "supplier_special_cases", ["status"])
    op.create_index(
        "ix_supplier_special_cases_customer_nit", "supplier_special_cases", ["customer_nit"]
    )
    op.create_index(
        "uq_supplier_special_cases_source_key",
        "supplier_special_cases",
        ["source_key"],
        unique=True,
        postgresql_where=sa.text("source_key IS NOT NULL"),
    )
    op.add_column(
        "supplier_supports",
        sa.Column(
            "special_case_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("supplier_special_cases.id", ondelete="SET NULL"),
        ),
    )
    op.create_index("ix_supplier_supports_special_case", "supplier_supports", ["special_case_id"])
