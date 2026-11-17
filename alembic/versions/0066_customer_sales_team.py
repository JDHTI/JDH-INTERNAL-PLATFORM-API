"""Sales team of each customer branch (``sales_team``, ``sales_team_class``).

Copied from ``consumo.vendedores`` (``equipo``, ``clase_equipo``) through the branch's seller.
The team class ``MASIVO FOOD`` puts the customer on the Masivo food margin list.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0066_customer_sales_team"
# Also the merge point of the two 0065 that landed in parallel on master (action plans and
# employee margin), so the history has one head again.
down_revision: str | Sequence[str] | None = ("0065_action_plans", "0065_employee_margin")
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
