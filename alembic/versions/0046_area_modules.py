"""Modules switched on per area (``area_modules``) and the index of the master sync.

``area_modules`` keeps only an administrator's choices: an area without a row for a
module follows the catalog default (``app/modules/areas/domain/catalog.py``), so the
deploy changes nothing until someone switches a module on or off. ``config`` holds the
module's settings in the area (``maestros``: which masters).

``ix_master_records_master_synced_at`` serves the sync that deactivates the records a
run did not see.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0046_area_modules"
down_revision: str | None = "0045_salesperson_links"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TZ = sa.DateTime(timezone=True)
_UUID = postgresql.UUID(as_uuid=True)


def upgrade() -> None:
    """Create the module choices and the sync index."""

    op.create_table(
        "area_modules",
        sa.Column("id", _UUID, primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("area_id", _UUID, sa.ForeignKey("areas.id", ondelete="CASCADE"), nullable=False),
        sa.Column("module", sa.String(60), nullable=False),
        sa.Column("is_enabled", sa.Boolean, nullable=False),
        sa.Column(
            "config",
            postgresql.JSONB,
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("created_at", _TZ, server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", _TZ, server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("area_id", "module", name="uq_area_modules_area_module"),
    )
    op.create_index(
        "ix_master_records_master_synced_at", "master_records", ["master_id", "synced_at"]
    )


def downgrade() -> None:
    """Drop them."""

    op.drop_index("ix_master_records_master_synced_at", table_name="master_records")
    op.drop_table("area_modules")
