"""Masters read live from the DWH: each master names the sequence that refreshes it.

``master_definitions.dwh_sequence`` is the warehouse sequence (``monitoreo.secuencias``)
whose run publishes the master's ``consumo`` view. The platform caches what it reads by
that sequence's last run, and serves the cached copy while the sequence reloads.

``terceros`` is retired: no module needs it (customers and suppliers cover the business)
and it is the master with the most personal data.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0048_masters_live"
down_revision: str | None = "0047_jobs"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_IDENTITY = (
    "clientes",
    "proveedores",
    "vendedores",
    "supervisores",
    "equipos",
    "centros-operacion",
    "centros-costo",
    "unidades-negocio",
    "regionales",
)


def upgrade() -> None:
    """Add the sequence of each master and retire ``terceros``."""

    op.add_column("master_definitions", sa.Column("dwh_sequence", sa.String(60)))
    definitions = sa.table(
        "master_definitions",
        sa.column("slug", sa.String),
        sa.column("dwh_sequence", sa.String),
        sa.column("is_active", sa.Boolean),
    )
    op.execute(
        definitions.update()
        .where(definitions.c.slug.in_(_IDENTITY))
        .values(dwh_sequence="identidad")
    )
    op.execute(
        definitions.update()
        .where(definitions.c.slug == "productos")
        .values(dwh_sequence="reporte_ventas")
    )
    op.execute(definitions.update().where(definitions.c.slug == "terceros").values(is_active=False))


def downgrade() -> None:
    """Bring ``terceros`` back and drop the sequence column."""

    definitions = sa.table(
        "master_definitions", sa.column("slug", sa.String), sa.column("is_active", sa.Boolean)
    )
    op.execute(definitions.update().where(definitions.c.slug == "terceros").values(is_active=True))
    op.drop_column("master_definitions", "dwh_sequence")
