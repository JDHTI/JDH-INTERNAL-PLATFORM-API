"""Quoter: the operation centers a quotation is costed from.

- ``quotations.cost_centers``: the centers whose warehouses give the cost; empty is
  *Nacional* (the highest cost of every warehouse, SKU by SKU). Each existing quotation keeps
  the center it was priced from, so nothing changes until it is refreshed or edited.
- ``quotation_lines.cost_center``: the center whose warehouses set the line's cost (none for
  *Nacional* or a list with its own warehouses).
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0054_quotation_cost_centers"
down_revision: str | Sequence[str] | None = "0053_app_monitoring_jdh_com_co"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "quotations",
        sa.Column(
            "cost_centers",
            postgresql.ARRAY(sa.String(10)),
            nullable=False,
            server_default=sa.text("'{}'::varchar(10)[]"),
        ),
    )
    op.execute(
        "UPDATE quotations SET cost_centers = ARRAY[billing_center_code]::varchar(10)[] "
        "WHERE billing_center_code IS NOT NULL"
    )
    op.alter_column("quotations", "cost_centers", server_default=None)
    op.add_column("quotation_lines", sa.Column("cost_center", sa.String(10)))
    op.execute(
        "UPDATE quotation_lines l SET cost_center = q.billing_center_code FROM quotations q "
        "WHERE q.id = l.quotation_id AND l.cost IS NOT NULL AND q.billing_center_code IS NOT NULL "
        # Lists with their own warehouses (list 1 and the skill's) are not costed by center.
        "AND coalesce(q.price_list_id, '') NOT IN ('1', "
        "'001', '1M', '10X', '002', '10T', '10R', '003', '10U', '10S', '004', '11N', '005', "
        "'10N', '009', '1DR', '35P')"
    )


def downgrade() -> None:
    op.drop_column("quotation_lines", "cost_center")
    op.drop_column("quotations", "cost_centers")
