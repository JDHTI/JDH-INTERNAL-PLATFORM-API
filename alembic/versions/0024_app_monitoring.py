"""Application monitoring: the platforms TI checks and the history of the checks.

- ``app_mon_aplicativos``: one row per platform, edited from Monitoreo → Aplicativos.
  The API check reads ``url_base + ruta_health``; the UI check reads
  ``url_base + ruta_ui``. Seeded with the supplier app, the catalog and the intranet.
- ``app_mon_chequeos``: one row per check (``objetivo`` ``api`` or ``ui``), purged
  after the retention days.
"""

from collections.abc import Sequence
from uuid import uuid4

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0024_app_monitoring"
down_revision: str | None = "0023_dwh_monitoring"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TZ = sa.DateTime(timezone=True)
_UUID = postgresql.UUID(as_uuid=True)

_SEED = (
    ("app-proveedores", "App Proveedores", "https://app-proveedores.jdh.local"),
    ("catalogo", "Catálogo (Tienda)", "https://tienda.jdh.local"),
    ("intranet", "Intranet", "https://intranet.jdh.local"),
)


def upgrade() -> None:
    """Create the applications, their checks, and register the three platforms."""

    aplicativos = op.create_table(
        "app_mon_aplicativos",
        sa.Column("id", _UUID, primary_key=True),
        sa.Column("slug", sa.String(60), nullable=False, unique=True),
        sa.Column("nombre", sa.String(120), nullable=False),
        sa.Column("descripcion", sa.Text),
        sa.Column("url_base", sa.String(500), nullable=False),
        sa.Column("ruta_health", sa.String(300), nullable=False),
        sa.Column("ruta_ui", sa.String(300), nullable=False),
        sa.Column("verificar_ui", sa.Boolean, nullable=False),
        sa.Column("habilitado", sa.Boolean, nullable=False),
        sa.Column("verificar_tls", sa.Boolean, nullable=False),
        sa.Column("timeout_s", sa.Float, nullable=False),
        sa.Column("umbral_lento_ms", sa.Integer, nullable=False),
        sa.Column("orden", sa.Integer, nullable=False, server_default="0"),
        sa.Column("creado_en", _TZ, nullable=False, server_default=sa.func.now()),
        sa.Column("actualizado_en", _TZ, nullable=False, server_default=sa.func.now()),
    )
    op.create_table(
        "app_mon_chequeos",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column(
            "aplicativo_id",
            _UUID,
            sa.ForeignKey("app_mon_aplicativos.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("objetivo", sa.String(10), nullable=False),
        sa.Column("chequeado_en", _TZ, nullable=False),
        sa.Column("estado", sa.String(10), nullable=False),
        sa.Column("codigo_http", sa.SmallInteger),
        sa.Column("latencia_ms", sa.Integer),
        sa.Column("error", sa.Text),
        sa.Column("detalle", postgresql.JSONB),
    )
    op.create_index(
        "ix_app_mon_chequeos_aplicativo",
        "app_mon_chequeos",
        ["aplicativo_id", "objetivo", "chequeado_en"],
    )
    op.create_index("ix_app_mon_chequeos_chequeado_en", "app_mon_chequeos", ["chequeado_en"])
    op.bulk_insert(
        aplicativos,
        [
            {
                "id": uuid4(),
                "slug": slug,
                "nombre": nombre,
                "descripcion": None,
                "url_base": url,
                "ruta_health": "/api/health",
                "ruta_ui": "/",
                "verificar_ui": True,
                "habilitado": True,
                "verificar_tls": True,
                "timeout_s": 10.0,
                "umbral_lento_ms": 2000,
                "orden": position,
            }
            for position, (slug, nombre, url) in enumerate(_SEED)
        ],
    )


def downgrade() -> None:
    """Drop the checks and the applications."""

    op.drop_table("app_mon_chequeos")
    op.drop_table("app_mon_aplicativos")
