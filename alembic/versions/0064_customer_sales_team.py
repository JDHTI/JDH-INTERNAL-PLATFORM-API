"""Sales team of each customer branch (``sales_team``, ``sales_team_class``).

Copied from ``consumo.vendedores`` (``equipo``, ``clase_equipo``) through the branch's seller.
The team class ``MASIVO FOOD`` puts the customer on the Masivo food margin list.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0064_customer_sales_team"
down_revision: str | None = "0063_territory"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add the two columns."""

    op.add_column("customers", sa.Column("sales_team", sa.Text()))
    op.add_column("customers", sa.Column("sales_team_class", sa.Text()))


def downgrade() -> None:
    """Drop them."""

    op.drop_column("customers", "sales_team_class")
    op.drop_column("customers", "sales_team")
