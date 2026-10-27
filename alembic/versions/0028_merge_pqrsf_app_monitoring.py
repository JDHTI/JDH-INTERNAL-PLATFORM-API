"""Join the PQRSF classification chain and the application monitoring chain into one head."""

from collections.abc import Sequence

revision: str = "0028_merge_pqrsf_app_monitoring"
down_revision: str | Sequence[str] | None = ("0027_pqrsf_sales_executive", "0024_app_monitoring")
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Nothing to change: both chains touch different tables."""


def downgrade() -> None:
    """Nothing to change: both chains touch different tables."""
