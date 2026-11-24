"""Quotation by branch (2026-11-24).

A customer quotation can be made for one branch of the customer: that branch decides the list,
channel, center and seller, its sales are the window, and its NIT-sucursal supports apply.

- ``quotations.customer_branch_id``: the branch quoted; ``NULL`` is the NIT as a whole.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0087_quotation_branch"
down_revision: str | Sequence[str] | None = "0086_settlement_cost_basis"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("quotations", sa.Column("customer_branch_id", sa.String(20), nullable=True))


def downgrade() -> None:
    op.drop_column("quotations", "customer_branch_id")
