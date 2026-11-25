"""List updates: references taken out with the template (2026-11-25).

- ``quotations.excluded_skus``: the references an advisor deleted from the list update
  template and uploaded back. They leave the update, and a refresh does not bring them back
  from the list; adding one again lifts it.

It starts empty, so every existing quotation stays as it is.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0091_list_update_excluded_skus"
down_revision: str | Sequence[str] | None = "0090_price_increase_siesa_loads"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "quotations",
        sa.Column(
            "excluded_skus",
            postgresql.ARRAY(sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::text[]"),
        ),
    )
    op.alter_column("quotations", "excluded_skus", server_default=None)


def downgrade() -> None:
    op.drop_column("quotations", "excluded_skus")
