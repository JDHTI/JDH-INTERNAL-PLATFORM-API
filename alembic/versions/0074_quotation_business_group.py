"""A quotation of a whole business group (BI, 2026-11-19).

``business_group`` is the group's name (``customers.business_group``) and ``group_customers``
how many NITs it had when it was built; the offer goes to the group.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0074_quotation_business_group"
down_revision: str | None = "0073_drop_special_cases"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add the group and its count of customers."""

    op.add_column("quotations", sa.Column("business_group", sa.Text, nullable=True))
    op.add_column("quotations", sa.Column("group_customers", sa.Integer, nullable=True))


def downgrade() -> None:
    """Drop them."""

    op.drop_column("quotations", "group_customers")
    op.drop_column("quotations", "business_group")
