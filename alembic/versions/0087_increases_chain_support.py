"""Increases chained on the same day, and the supplier's support of a request (2026-11-24).

- ``product_price_increases.occurrence``: the n-th workbook row with the same reference,
  dates and regional. The workbook has 99 such keys (101 rows) that an upload used to fold
  into its last row; the second one now gets ``#2`` in its key. Stored rows are the first.
- ``cost_increase_requests.support_*``: the supplier's support (its letter, an email as PDF),
  kept in the bucket under the area.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0087_increases_chain_support"
down_revision: str | Sequence[str] | None = "0086_settlement_cost_basis"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_SUPPORT = (
    ("support_file_name", sa.Text()),
    ("support_content_type", sa.String(120)),
    ("support_size_bytes", sa.BigInteger()),
    ("support_storage_key", sa.Text()),
    ("support_uploaded_by", sa.String(320)),
    ("support_uploaded_at", sa.DateTime(timezone=True)),
)


def upgrade() -> None:
    op.add_column(
        "product_price_increases",
        sa.Column("occurrence", sa.SmallInteger(), nullable=False, server_default="1"),
    )
    for name, type_ in _SUPPORT:
        op.add_column("cost_increase_requests", sa.Column(name, type_, nullable=True))


def downgrade() -> None:
    for name, _ in reversed(_SUPPORT):
        op.drop_column("cost_increase_requests", name)
    # The rows after the first of their key go: the old key cannot hold them.
    op.execute("DELETE FROM product_price_increases WHERE occurrence > 1")
    op.drop_column("product_price_increases", "occurrence")
