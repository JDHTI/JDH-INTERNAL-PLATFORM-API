"""Employee directory: sites, employees synced from Entra, and the history of the syncs.

- ``directory_sites``: physical locations ("sedes"). Named apart from ``pqrsf_sites``,
  the PQRSF sites catalog, on purpose.
- ``directory_employees``: one row per person. A synced employee has its Entra object id
  and its platform user (``users.id``), both or neither (manual employees).
- ``directory_sync_runs``: one row per sync. A partial unique index on the running
  status lets only one run at a time.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0040_directory"
down_revision: str | None = "0039_merge_heads"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TZ = sa.DateTime(timezone=True)
_UUID = postgresql.UUID(as_uuid=True)


def upgrade() -> None:
    """Create the sites, the employees and the sync runs."""

    op.create_table(
        "directory_sites",
        sa.Column("id", _UUID, primary_key=True),
        sa.Column("name", sa.String(150), nullable=False, unique=True),
        sa.Column("address", sa.String(300)),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default="true"),
        sa.Column("created_at", _TZ, nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", _TZ, nullable=False, server_default=sa.func.now()),
    )
    op.create_table(
        "directory_employees",
        sa.Column("id", _UUID, primary_key=True),
        sa.Column("user_id", _UUID, sa.ForeignKey("users.id", ondelete="RESTRICT"), unique=True),
        sa.Column("entra_object_id", sa.String(100), unique=True),
        sa.Column("full_name", sa.String(200), nullable=False),
        sa.Column("email", sa.String(320)),
        sa.Column("job_title", sa.String(200)),
        sa.Column("position", sa.String(200), nullable=False, server_default=""),
        sa.Column("department", sa.String(200)),
        sa.Column("office_location", sa.String(200)),
        sa.Column("site_id", _UUID, sa.ForeignKey("directory_sites.id", ondelete="RESTRICT")),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default="true"),
        sa.Column("account_enabled", sa.Boolean),
        sa.Column("user_type", sa.String(20)),
        sa.Column("auto_visible", sa.Boolean, nullable=False, server_default="true"),
        sa.Column("visibility", sa.String(10), nullable=False, server_default="auto"),
        sa.Column("birth_date", sa.Date),
        sa.Column("hire_date", sa.Date),
        sa.Column(
            "manager_id",
            _UUID,
            sa.ForeignKey("directory_employees.id", ondelete="SET NULL"),
        ),
        sa.Column("is_manager", sa.Boolean, nullable=False, server_default="false"),
        sa.Column("dwh_synced_at", _TZ),
        sa.Column("photo_key", sa.String(500)),
        sa.Column("photo_thumb_key", sa.String(500)),
        sa.Column("photo_etag", sa.String(200)),
        sa.Column("photo_content_type", sa.String(100)),
        sa.Column("photo_updated_at", _TZ),
        sa.Column("search_text", sa.Text, nullable=False, server_default=""),
        sa.Column("synced_at", _TZ),
        sa.Column("created_at", _TZ, nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", _TZ, nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint(
            "(user_id IS NULL) = (entra_object_id IS NULL)",
            name="ck_directory_employees_identity_pair",
        ),
        sa.CheckConstraint(
            "visibility IN ('auto', 'shown', 'hidden')",
            name="ck_directory_employees_visibility",
        ),
    )
    for name, columns in (
        ("site_id", ["site_id"]),
        ("department", ["department"]),
        ("manager_id", ["manager_id"]),
        ("hire_date", ["hire_date"]),
        ("birth_date", ["birth_date"]),
        ("full_name", ["full_name"]),
        ("public", ["is_active", "visibility"]),
    ):
        op.create_index(f"ix_directory_employees_{name}", "directory_employees", columns)
    op.create_table(
        "directory_sync_runs",
        sa.Column("id", _UUID, primary_key=True),
        sa.Column("trigger", sa.String(10), nullable=False),
        sa.Column("status", sa.String(10), nullable=False),
        sa.Column("started_at", _TZ, nullable=False),
        sa.Column("finished_at", _TZ),
        sa.Column(
            "counts", postgresql.JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")
        ),
        sa.Column("error_code", sa.String(50)),
        sa.Column("error_message", sa.Text),
        sa.Column("triggered_by", sa.String(320)),
        sa.CheckConstraint(
            "trigger IN ('manual', 'scheduled')", name="ck_directory_sync_runs_trigger"
        ),
        sa.CheckConstraint(
            "status IN ('running', 'success', 'failed')", name="ck_directory_sync_runs_status"
        ),
    )
    op.create_index("ix_directory_sync_runs_started_at", "directory_sync_runs", ["started_at"])
    op.create_index(
        "uq_directory_sync_runs_running",
        "directory_sync_runs",
        ["status"],
        unique=True,
        postgresql_where=sa.text("status = 'running'"),
    )


def downgrade() -> None:
    """Drop the sync runs, the employees and the sites."""

    op.drop_table("directory_sync_runs")
    op.drop_table("directory_employees")
    op.drop_table("directory_sites")
