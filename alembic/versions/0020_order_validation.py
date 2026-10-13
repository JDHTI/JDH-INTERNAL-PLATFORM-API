"""Retained orders validator: uploaded files and the result of each line.

- ``order_validation_batches``: one uploaded SIESA retained orders report, with the counts
  of its latest validation (lines and orders ready for release).
- ``order_validation_lines``: each row of the report as read, plus the latest decision
  (status, reason, margins, the deciding support and every support checked).
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0020_order_validation"
down_revision: str | None = "0019_support_units_and_cases"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_STATUSES = ("listo_para_liberar", "pendiente_validacion")
_REASONS = (
    "cumple_con_apoyo",
    "cumple_margen_lista",
    "acuerdo_en_revision",
    "no_cumple_acuerdo",
    "acuerdo_no_evaluable",
    "acuerdo_vencido",
    "sin_acuerdo",
    "datos_incompletos",
)


def _in(column: str, values: tuple[str, ...]) -> str:
    return f"{column} IN ({', '.join(repr(value) for value in values)})"


def upgrade() -> None:
    """Create the files and lines of the validator."""

    op.create_table(
        "order_validation_batches",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("file_name", sa.String(255), nullable=False),
        sa.Column("uploaded_by", sa.String(320), nullable=False),
        sa.Column("uploaded_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("validated_by", sa.String(320), nullable=False),
        sa.Column("validated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("line_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("order_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("ready_line_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("ready_order_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )
    op.create_table(
        "order_validation_lines",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "batch_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("order_validation_batches.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("row_number", sa.Integer, nullable=False),
        sa.Column("document", sa.Text, nullable=False),
        sa.Column("order_date", sa.Date),
        sa.Column("operation_center", sa.Text),
        sa.Column("status_text", sa.Text),
        sa.Column("customer_nit", sa.Text),
        sa.Column("customer_name", sa.Text),
        sa.Column("salesperson", sa.Text),
        sa.Column("channel", sa.Text),
        sa.Column("warehouse", sa.Text),
        sa.Column("sku", sa.Text),
        sa.Column("description", sa.Text),
        sa.Column("supplier_code", sa.Text),
        sa.Column("supplier_name", sa.Text),
        sa.Column("unit_price", sa.Numeric(18, 4)),
        sa.Column("quantity", sa.Numeric(18, 4)),
        sa.Column("margin_pct", sa.Numeric(14, 6)),
        sa.Column("price_list", sa.Text),
        sa.Column("retained", sa.Text),
        sa.Column("problems", postgresql.ARRAY(sa.Text), nullable=False, server_default="{}"),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("reason", sa.String(30), nullable=False),
        sa.Column("detail", sa.Text, nullable=False),
        sa.Column(
            "support_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("supplier_supports.id", ondelete="SET NULL"),
        ),
        sa.Column("effective_margin_pct", sa.Numeric(14, 6)),
        sa.Column("required_margin_pct", sa.Numeric(14, 6)),
        sa.Column("checks", postgresql.JSONB, nullable=False, server_default="[]"),
        sa.CheckConstraint(_in("status", _STATUSES), name="ck_order_validation_lines_status"),
        sa.CheckConstraint(_in("reason", _REASONS), name="ck_order_validation_lines_reason"),
    )
    op.create_index(
        "ix_order_validation_lines_batch", "order_validation_lines", ["batch_id", "row_number"]
    )
    op.create_index(
        "ix_order_validation_lines_document", "order_validation_lines", ["batch_id", "document"]
    )


def downgrade() -> None:
    """Drop the validator tables."""

    op.drop_index("ix_order_validation_lines_document", table_name="order_validation_lines")
    op.drop_index("ix_order_validation_lines_batch", table_name="order_validation_lines")
    op.drop_table("order_validation_lines")
    op.drop_table("order_validation_batches")
