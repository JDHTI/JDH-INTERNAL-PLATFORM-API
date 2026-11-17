"""Supports in the promotional warehouses (SIESA warehouse group 23, ``promo_warehouses``).

Rules v2 (2026-11-17): a support applies in the promotional warehouses only when it says so;
otherwise an order from one of them goes at that warehouse's cost, without the support.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0067_support_promo_warehouses"
down_revision: str | None = "0066_support_request_margin_mode"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add the flag (false for every support already stored)."""

    op.add_column(
        "supplier_supports",
        sa.Column("promo_warehouses", sa.Boolean(), nullable=False, server_default="false"),
    )


def downgrade() -> None:
    """Drop it."""

    op.drop_column("supplier_supports", "promo_warehouses")
