"""Join the PQRSF case classification chain and the quotation review chain into one head."""

from collections.abc import Sequence

revision: str = "0039_merge_heads"
down_revision: str | Sequence[str] | None = (
    "0038_pqrsf_case_classification",
    "0038_quotation_review",
)
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Nothing to change: both chains touch different tables."""


def downgrade() -> None:
    """Nothing to change: both chains touch different tables."""
