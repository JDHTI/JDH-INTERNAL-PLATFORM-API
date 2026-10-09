"""Supports: reversible switch, unit basis provenance and special cases.

- ``supplier_supports.is_enabled``: a person can switch a support off (greyed out, out of
  force) and back on without voiding it.
- ``supplier_supports.unit_basis_source``: where ``value_unit`` (cost per box or per unit)
  comes from: the sheet itself, an inference against the warehouse cost, a person, or
  unknown.
- ``supplier_special_cases``: particular client cases that need manual configuration and
  later business logic in code; supports can point at one (``special_case_id``).
- ``products.avg_unit_cost``: current average cost of the minimum selling unit, used to
  infer whether a workbook cost is per unit or per box.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0019_support_units_and_cases"
down_revision: str | None = "0018_supplier_supports"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_UNIT_SOURCES = ("hoja", "inferido_dwh", "manual", "desconocido")
_CASE_STATUSES = ("pendiente", "en_analisis", "programado", "implementado", "descartado")
_CASE_PRIORITIES = ("baja", "media", "alta")


def _in(column: str, values: tuple[str, ...]) -> str:
    return f"{column} IN ({', '.join(repr(value) for value in values)})"


def upgrade() -> None:
    """Add the switch, the unit provenance, the special cases and the unit cost."""

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
        sa.Column("is_enabled", sa.Boolean, nullable=False, server_default="true"),
    )
    op.add_column(
        "supplier_supports",
        sa.Column("unit_basis_source", sa.String(20), nullable=False, server_default="desconocido"),
    )
    op.add_column(
        "supplier_supports",
        sa.Column(
            "special_case_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("supplier_special_cases.id", ondelete="SET NULL"),
        ),
    )
    op.create_check_constraint(
        "ck_supplier_supports_unit_basis_source",
        "supplier_supports",
        _in("unit_basis_source", _UNIT_SOURCES),
    )
    op.create_index("ix_supplier_supports_special_case", "supplier_supports", ["special_case_id"])
    # Every support loaded so far had its unit assumed or read from the sheet header.
    op.execute(
        "UPDATE supplier_supports SET unit_basis_source = 'hoja' WHERE value_unit IS NOT NULL"
    )

    op.add_column("products", sa.Column("avg_unit_cost", sa.Numeric(18, 4)))
    op.add_column("products", sa.Column("avg_unit_cost_date", sa.Date))


def downgrade() -> None:
    """Drop what this revision added."""

    op.drop_column("products", "avg_unit_cost_date")
    op.drop_column("products", "avg_unit_cost")
    op.drop_index("ix_supplier_supports_special_case", table_name="supplier_supports")
    op.drop_constraint("ck_supplier_supports_unit_basis_source", "supplier_supports", type_="check")
    op.drop_column("supplier_supports", "special_case_id")
    op.drop_column("supplier_supports", "unit_basis_source")
    op.drop_column("supplier_supports", "is_enabled")
    op.drop_table("supplier_special_cases")
