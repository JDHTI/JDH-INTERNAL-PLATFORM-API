"""Mark the supports of a business group (2026-11-20).

A business group's support is loaded as one copy per active customer of the group, and
until now every copy only covered its own NIT: a customer that joined the group later (or
was inactive when it was loaded) never got it. ``group_wide`` marks the copies, so the quoter,
the order validator and each salesperson's view take them for the whole group.

The copies already loaded are marked by what they share: rows that are not voided, with a
NIT and a group, written in the same transaction (same ``created_at``) with the same
supplier, SKU, class, method, value and dates for two or more customers of the same group.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0076_support_group_wide"
down_revision: str | None = "0075_customers_salesperson_index"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_COPIES = """
UPDATE supplier_supports s SET group_wide = true
FROM (
    SELECT commercial_supplier_id, product_sku, kind, calc_method, supplier_value,
           valid_from, valid_to, upper(trim(customer_group)) AS grp, created_at
    FROM supplier_supports
    WHERE customer_nit IS NOT NULL AND customer_group IS NOT NULL AND status <> 'anulado'
    GROUP BY 1, 2, 3, 4, 5, 6, 7, 8, 9
    HAVING count(DISTINCT customer_nit) >= 2
) c
WHERE s.customer_nit IS NOT NULL AND s.status <> 'anulado'
  AND s.commercial_supplier_id IS NOT DISTINCT FROM c.commercial_supplier_id
  AND s.product_sku IS NOT DISTINCT FROM c.product_sku
  AND s.kind = c.kind AND s.calc_method = c.calc_method
  AND s.supplier_value IS NOT DISTINCT FROM c.supplier_value
  AND s.valid_from IS NOT DISTINCT FROM c.valid_from
  AND s.valid_to IS NOT DISTINCT FROM c.valid_to
  AND upper(trim(s.customer_group)) = c.grp
  AND s.created_at = c.created_at
"""


def upgrade() -> None:
    """Add the flag and mark the copies already loaded."""

    op.add_column(
        "supplier_supports",
        sa.Column("group_wide", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.execute(_COPIES)


def downgrade() -> None:
    """Drop the flag."""

    op.drop_column("supplier_supports", "group_wide")
