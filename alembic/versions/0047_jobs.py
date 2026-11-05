"""Background job runs (``job_runs``) and the DWH sequences they wait for.

``job_runs`` keeps one row per run; the partial unique index allows one open
(queued or running) run per job. ``dwh_sequence_watermarks`` stores each warehouse
sequence as last polled from ``monitoreo.secuencias`` and the ``ultima_datos_listos``
whose jobs were already queued, so a restart does not run them again.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0047_jobs"
down_revision: str | None = "0046_area_modules"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TZ = sa.DateTime(timezone=True)
_UUID = postgresql.UUID(as_uuid=True)


def upgrade() -> None:
    """Create the runs and the watermarks."""

    op.create_table(
        "job_runs",
        sa.Column("id", _UUID, primary_key=True),
        sa.Column("job", sa.String(60), nullable=False),
        sa.Column("trigger", sa.String(10), nullable=False),
        sa.Column("status", sa.String(10), nullable=False),
        sa.Column("queued_at", _TZ, nullable=False),
        sa.Column("started_at", _TZ),
        sa.Column("finished_at", _TZ),
        sa.Column(
            "detail", postgresql.JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")
        ),
        sa.Column("error", sa.Text),
        sa.Column("dwh_corrida_id", _UUID),
        sa.Column("dwh_datos_listos", _TZ),
        sa.Column("triggered_by", sa.String(320)),
        sa.CheckConstraint(
            "trigger IN ('dwh', 'scheduled', 'manual', 'startup')", name="ck_job_runs_trigger"
        ),
        sa.CheckConstraint(
            "status IN ('queued', 'running', 'success', 'partial', 'failed')",
            name="ck_job_runs_status",
        ),
    )
    op.create_index("ix_job_runs_job_queued_at", "job_runs", ["job", "queued_at"])
    op.create_index(
        "uq_job_runs_open",
        "job_runs",
        ["job"],
        unique=True,
        postgresql_where=sa.text("status IN ('queued', 'running')"),
    )
    op.create_table(
        "dwh_sequence_watermarks",
        sa.Column("secuencia", sa.Text, primary_key=True),
        sa.Column("ultima_corrida_id", _UUID),
        sa.Column("ultima_estado", sa.Text),
        sa.Column("ultima_datos_listos", _TZ),
        sa.Column("en_curso", sa.Boolean),
        sa.Column("proxima_ejecucion", _TZ),
        sa.Column("eta_datos_listos_proxima", _TZ),
        sa.Column("eta_datos_listos_en_curso", _TZ),
        sa.Column("dispatched_datos_listos", _TZ),
        sa.Column("polled_at", _TZ, nullable=False),
        sa.Column("updated_at", _TZ, server_default=sa.func.now(), nullable=False),
    )


def downgrade() -> None:
    """Drop them."""

    op.drop_table("dwh_sequence_watermarks")
    op.drop_index("uq_job_runs_open", table_name="job_runs")
    op.drop_index("ix_job_runs_job_queued_at", table_name="job_runs")
    op.drop_table("job_runs")
