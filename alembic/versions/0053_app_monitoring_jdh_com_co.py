"""Monitored applications move from ``*.jdh.local`` to ``*.jdh.com.co``.

The three platforms seeded by ``0024`` are now published under the corporate
domain (certificate ``*.jdh.com.co`` at the infrastructure proxy). Only rows that
still hold the seeded URL change: one edited from Monitoreo → Aplicativos keeps
what TI wrote.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0053_app_monitoring_jdh_com_co"
down_revision: str | None = "0052_quotation_upload_flow"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_MOVES = (
    ("app-proveedores", "https://app-proveedores.jdh.local", "https://app-proveedores.jdh.com.co"),
    ("catalogo", "https://tienda.jdh.local", "https://tienda.jdh.com.co"),
    ("intranet", "https://intranet.jdh.local", "https://intranet.jdh.com.co"),
)

_UPDATE = sa.text(
    "UPDATE app_mon_aplicativos SET url_base = :new, actualizado_en = now() "
    "WHERE slug = :slug AND url_base = :old"
)


def upgrade() -> None:
    """Point the seeded applications at their ``jdh.com.co`` host."""

    for slug, old, new in _MOVES:
        op.execute(_UPDATE.bindparams(slug=slug, old=old, new=new))


def downgrade() -> None:
    """Back to the ``jdh.local`` hosts, only where the URL is still the moved one."""

    for slug, old, new in _MOVES:
        op.execute(_UPDATE.bindparams(slug=slug, old=new, new=old))
