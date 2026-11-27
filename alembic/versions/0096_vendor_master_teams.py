"""Maestro de vendedores: the platform's team master rows (2026-11-27).

- ``vendor_master_teams``: a team created in the platform (not loaded in the warehouse yet),
  or a warehouse team's class or visibility in the template. The template offers only the
  teams of the master (``consumo.equipos`` plus these), and an upload with another team is
  refused.

It starts empty: the warehouse's teams are offered as they are.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0096_vendor_master_teams"
down_revision: str | Sequence[str] | None = "0095_service_level"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TS = sa.DateTime(timezone=True)


def upgrade() -> None:
    op.create_table(
        "vendor_master_teams",
        sa.Column("name", sa.Text(), primary_key=True),
        sa.Column("team_class", sa.Text()),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("note", sa.Text()),
        sa.Column("created_by", sa.String(320), nullable=False),
        sa.Column("created_at", _TS, nullable=False),
        sa.Column("updated_by", sa.String(320), nullable=False),
        sa.Column("updated_at", _TS, nullable=False),
    )


def downgrade() -> None:
    op.drop_table("vendor_master_teams")
