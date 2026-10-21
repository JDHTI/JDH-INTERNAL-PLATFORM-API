"""Join the PQRSF and the commercial/order validation chains into one head.

Both chains grew from ``0016_pqrsf_email`` on separate branches. Keeping their
revision identifiers lets a database that already ran either side upgrade in place.
"""

from collections.abc import Sequence

revision: str = "0022_merge_pqrsf_orders"
down_revision: str | Sequence[str] | None = (
    "0021_order_validation_approvals",
    "0021_pqrsf_item_deliveries",
)
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Nothing to change: the merge only joins the two chains."""


def downgrade() -> None:
    """Nothing to change: the merge only joins the two chains."""
