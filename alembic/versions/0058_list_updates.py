"""List updates: a quotation that starts from an existing SIESA price list.

- ``quotations.kind``: ``cotizacion`` (a customer quotation, every existing row) or ``lista``
  (the update of a price list, module ``actualizacion-listas``).
- ``quotations.source``: where a list update started, ``cliente`` (the customer's assigned
  list) or ``lista`` (the list chosen by code or name); ``NULL`` for a quotation.
- ``quotation_lines.last_sale_price`` / ``last_sale_cost``: net price and unit cost of the
  SKU's last sale to the list's customers (a list update compares the list price, so the last
  sale goes apart).
- ``quotation_lines.support_options``: every support that covers the line, each with the
  minimum and optimal price it leaves.
- ``quotation_lines.origin`` accepts ``lista``: a SKU with a price in force on the list.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0058_list_updates"
down_revision: str | Sequence[str] | None = "0057_dwh_outbox_support_loads"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "quotations",
        sa.Column("kind", sa.String(20), nullable=False, server_default="cotizacion"),
    )
    op.alter_column("quotations", "kind", server_default=None)
    op.add_column("quotations", sa.Column("source", sa.String(20)))
    op.create_check_constraint(
        "ck_quotations_kind", "quotations", "kind IN ('cotizacion', 'lista')"
    )
    op.create_check_constraint(
        "ck_quotations_source", "quotations", "source IS NULL OR source IN ('cliente', 'lista')"
    )
    op.create_index("ix_quotations_kind_created", "quotations", ["kind", "created_at"])

    op.add_column("quotation_lines", sa.Column("last_sale_price", sa.Numeric(18, 4)))
    op.add_column("quotation_lines", sa.Column("last_sale_cost", sa.Numeric(18, 4)))
    op.add_column(
        "quotation_lines",
        sa.Column(
            "support_options",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
    )
    op.alter_column("quotation_lines", "support_options", server_default=None)
    op.drop_constraint("ck_quotation_lines_origin", "quotation_lines", type_="check")
    op.create_check_constraint(
        "ck_quotation_lines_origin",
        "quotation_lines",
        "origin IN ('historico', 'agregado', 'local', 'lista')",
    )


def downgrade() -> None:
    op.execute("DELETE FROM quotations WHERE kind = 'lista'")
    op.drop_constraint("ck_quotation_lines_origin", "quotation_lines", type_="check")
    op.create_check_constraint(
        "ck_quotation_lines_origin",
        "quotation_lines",
        "origin IN ('historico', 'agregado', 'local')",
    )
    op.drop_column("quotation_lines", "support_options")
    op.drop_column("quotation_lines", "last_sale_cost")
    op.drop_column("quotation_lines", "last_sale_price")
    op.drop_index("ix_quotations_kind_created", table_name="quotations")
    op.drop_constraint("ck_quotations_source", "quotations", type_="check")
    op.drop_constraint("ck_quotations_kind", "quotations", type_="check")
    op.drop_column("quotations", "source")
    op.drop_column("quotations", "kind")
