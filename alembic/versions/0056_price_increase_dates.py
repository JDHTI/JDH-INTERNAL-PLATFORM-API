"""Price increase analysis: index the history by when each increase takes effect.

The analysis reads a period (the last year by default) by ``increased_on``.
"""

from collections.abc import Sequence

from alembic import op

# alembic_version.version_num is VARCHAR(32): keep revision ids short.
revision: str = "0056_price_increase_dates"
down_revision: str | Sequence[str] | None = "0055_price_increases"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_index(
        "ix_product_price_increases_increased_on", "product_price_increases", ["increased_on"]
    )


def downgrade() -> None:
    op.drop_index("ix_product_price_increases_increased_on", table_name="product_price_increases")
