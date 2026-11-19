"""Index the customers by salesperson (2026-11-19).

Each commercial user sees only the supports of their customers, and quotes only their
business groups: both read ``customers`` by ``salesperson_code``. Without an index every
search walked the 73 thousand branches (31 of the 50 ms of a supports search, measured on
the local copy).
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0075_customers_salesperson_index"
down_revision: str | None = "0074_quotation_business_group"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add the index."""

    op.create_index("ix_customers_salesperson_code", "customers", ["salesperson_code"])


def downgrade() -> None:
    """Drop the index."""

    op.drop_index("ix_customers_salesperson_code", table_name="customers")
