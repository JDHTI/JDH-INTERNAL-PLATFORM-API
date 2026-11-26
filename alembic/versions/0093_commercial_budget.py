"""Gestión comercial: the commercial budget by sales rep and the rep master uploads (2026-11-26).

- ``sales_month_customers`` and ``sales_month_loads``: months of sales by customer branch,
  copied from ``consumo.ventas`` by the daily job.
- ``sales_reps``: the rep master (``consumo.vendedores``), copied daily.
- ``sales_budget_periods``, ``sales_budgets`` and ``sales_budget_changes``: the months, each
  rep's budget (every field optional) and its history.
- ``sales_budget_snapshot_reps`` and ``sales_budget_snapshot_customers``: the frozen picture of
  a closed month.
- ``vendor_master_uploads``: every upload of the rep master to dwh-platform (never the file).

Every table starts empty: the job fills the sales and the reps.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0093_commercial_budget"
down_revision: str | Sequence[str] | None = "0092_cost_settings"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_MONEY = sa.Numeric(20, 4)
_RATIO = sa.Numeric(12, 6)
_COUNT = sa.Numeric(14, 4)
_PERIOD = "period ~ '^[0-9]{4}-(0[1-9]|1[0-2])$'"
_TS = sa.DateTime(timezone=True)


def upgrade() -> None:
    op.create_table(
        "sales_month_customers",
        sa.Column("period", sa.String(7), primary_key=True),
        sa.Column("customer_key", sa.Text(), primary_key=True),
        sa.Column("net", _MONEY, nullable=False),
        sa.Column("cost", _MONEY, nullable=False),
        sa.Column("lines", sa.Integer(), nullable=False),
    )
    op.create_table(
        "sales_month_loads",
        sa.Column("period", sa.String(7), primary_key=True),
        sa.Column("loaded_at", _TS, nullable=False),
        sa.Column("customers", sa.Integer(), nullable=False),
        sa.Column("lines", sa.Integer(), nullable=False),
        sa.Column("net", _MONEY, nullable=False),
    )
    op.create_table(
        "sales_reps",
        sa.Column("code", sa.Text(), primary_key=True),
        sa.Column("name", sa.Text()),
        sa.Column("team", sa.Text()),
        sa.Column("team_class", sa.Text()),
        sa.Column("regional", sa.Text()),
        sa.Column("business_unit", sa.Text()),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("synced_at", _TS, nullable=False),
    )
    op.create_table(
        "sales_budget_periods",
        sa.Column("period", sa.String(7), primary_key=True),
        sa.Column("status", sa.String(12), nullable=False, server_default="abierto"),
        sa.Column("closed_at", _TS),
        sa.Column("closed_by", sa.String(320)),
        sa.Column("reopened_at", _TS),
        sa.Column("reopened_by", sa.String(320)),
        sa.Column("version", sa.Integer(), nullable=False, server_default="0"),
        sa.CheckConstraint(
            "status IN ('abierto','cerrado')", name="ck_sales_budget_periods_status"
        ),
        sa.CheckConstraint(_PERIOD, name="ck_sales_budget_periods_period"),
    )
    op.create_table(
        "sales_budgets",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("period", sa.String(7), nullable=False),
        sa.Column("salesperson_code", sa.Text(), nullable=False),
        sa.Column("budget_sales", _MONEY),
        sa.Column("budget_margin", _RATIO),
        sa.Column("budget_customers", sa.Integer()),
        sa.Column("budget_collection", _MONEY),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("updated_at", _TS, nullable=False),
        sa.Column("updated_by", sa.String(320), nullable=False),
        sa.UniqueConstraint("period", "salesperson_code", name="uq_sales_budgets_period_rep"),
    )
    op.create_table(
        "sales_budget_changes",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("period", sa.String(7), nullable=False),
        sa.Column("salesperson_code", sa.Text(), nullable=False),
        sa.Column("before", postgresql.JSONB(), nullable=False),
        sa.Column("after", postgresql.JSONB(), nullable=False),
        sa.Column("source", sa.String(12), nullable=False),
        sa.Column("note", sa.Text()),
        sa.Column("actor", sa.String(320), nullable=False),
        sa.Column("at", _TS, nullable=False),
    )
    op.create_index("ix_sales_budget_changes_period", "sales_budget_changes", ["period", "at"])
    op.create_index(
        "ix_sales_budget_changes_rep", "sales_budget_changes", ["period", "salesperson_code"]
    )
    op.create_table(
        "sales_budget_snapshot_reps",
        sa.Column("period", sa.String(7), primary_key=True),
        sa.Column("code", sa.Text(), primary_key=True),
        sa.Column("name", sa.Text()),
        sa.Column("regional", sa.Text()),
        sa.Column("business_unit", sa.Text()),
        sa.Column("team", sa.Text()),
        sa.Column("team_class", sa.Text()),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("current_net", _MONEY, nullable=False),
        sa.Column("current_margin", _RATIO),
        sa.Column("current_customers", _COUNT, nullable=False),
        sa.Column("average_net", _MONEY, nullable=False),
        sa.Column("average_margin", _RATIO),
        sa.Column("average_customers", _COUNT, nullable=False),
        sa.Column("average_months", sa.Integer()),
        sa.Column("last_year_net", _MONEY, nullable=False),
        sa.Column("last_year_margin", _RATIO),
        sa.Column("last_year_customers", _COUNT, nullable=False),
        sa.Column("budget_sales", _MONEY),
        sa.Column("budget_margin", _RATIO),
        sa.Column("budget_customers", sa.Integer()),
        sa.Column("budget_collection", _MONEY),
    )
    op.create_table(
        "sales_budget_snapshot_customers",
        sa.Column("period", sa.String(7), primary_key=True),
        sa.Column("customer_key", sa.Text(), primary_key=True),
        sa.Column("salesperson_code", sa.Text()),
        sa.Column("salesperson_name", sa.Text()),
        sa.Column("net", _MONEY, nullable=False),
        sa.Column("cost", _MONEY, nullable=False),
        sa.Column("lines", sa.Integer(), nullable=False),
    )
    op.create_index(
        "ix_sales_budget_snapshot_customers_rep",
        "sales_budget_snapshot_customers",
        ["period", "salesperson_code"],
    )
    op.create_table(
        "vendor_master_uploads",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("filename", sa.Text(), nullable=False),
        sa.Column("size", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(12), nullable=False),
        sa.Column("http_status", sa.Integer()),
        sa.Column("rejected_rows", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("warnings", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("summary", postgresql.JSONB(), nullable=False),
        sa.Column("actor", sa.String(320), nullable=False),
        sa.Column("at", _TS, nullable=False),
    )
    op.create_index("ix_vendor_master_uploads_at", "vendor_master_uploads", ["at"])


def downgrade() -> None:
    op.drop_index("ix_vendor_master_uploads_at", table_name="vendor_master_uploads")
    op.drop_table("vendor_master_uploads")
    op.drop_index(
        "ix_sales_budget_snapshot_customers_rep", table_name="sales_budget_snapshot_customers"
    )
    op.drop_table("sales_budget_snapshot_customers")
    op.drop_table("sales_budget_snapshot_reps")
    op.drop_index("ix_sales_budget_changes_rep", table_name="sales_budget_changes")
    op.drop_index("ix_sales_budget_changes_period", table_name="sales_budget_changes")
    op.drop_table("sales_budget_changes")
    op.drop_table("sales_budgets")
    op.drop_table("sales_budget_periods")
    op.drop_table("sales_reps")
    op.drop_table("sales_month_loads")
    op.drop_table("sales_month_customers")
