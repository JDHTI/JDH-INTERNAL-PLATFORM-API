"""Add the per-area project manager: folders, projects, epics and tasks.

Same depth as the document manager (area / folder / project). Inside a project,
epics only group tasks (no status or column of their own; their progress comes
from their tasks) and tasks live on a Scrumban board: ``backlog`` (its own
board), ``in_progress``, ``pending_validation``, ``blocked`` and ``done``. A task
may have no epic. Deleting a project cascades to epics and tasks; an epic with
tasks cannot be deleted.

Statuses, priorities and colors are text with CHECK constraints; colors are
palette keys the UI maps to its own shades.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0015_projects"
down_revision: str | None = "0014_masters"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_COLORS = (
    "('default', 'gray', 'brown', 'orange', 'yellow', 'green', 'blue', 'purple', 'pink', 'red')"
)
_TASK_STATUSES = "('backlog', 'in_progress', 'pending_validation', 'blocked', 'done')"
_DATES = "start_date IS NULL OR end_date IS NULL OR end_date >= start_date"


def _timestamps() -> list[sa.Column]:
    return [
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    ]


def _common() -> list[sa.Column]:
    return [
        sa.Column("position", sa.Double(), nullable=False),
        sa.Column("version", sa.Integer(), server_default="1", nullable=False),
        sa.Column("created_by", sa.String(320), nullable=False),
        sa.Column("updated_by", sa.String(320), nullable=False),
        *_timestamps(),
    ]


def upgrade() -> None:
    """Create the project, epic and task tables."""

    op.create_table(
        "project_folders",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "area_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("areas.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("name", sa.String(150), nullable=False),
        sa.Column("description", sa.Text()),
        sa.Column("color", sa.String(20), server_default="default", nullable=False),
        sa.Column("created_by", sa.String(320), nullable=False),
        *_timestamps(),
        sa.UniqueConstraint("area_id", "id", name="uq_project_folders_area_id_id"),
        sa.CheckConstraint(f"color IN {_COLORS}", name="ck_project_folders_color"),
    )
    op.create_index(
        "uq_project_folders_area_name_lower",
        "project_folders",
        ["area_id", sa.text("lower(name)")],
        unique=True,
    )

    op.create_table(
        "projects",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("area_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("folder_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("description", sa.Text()),
        sa.Column("color", sa.String(20), server_default="default", nullable=False),
        sa.Column("status", sa.String(20), server_default="created", nullable=False),
        sa.Column("priority", sa.String(10), server_default="medium", nullable=False),
        sa.Column("start_date", sa.Date(), nullable=False),
        sa.Column("end_date", sa.Date()),
        sa.Column("owner_user_id", postgresql.UUID(as_uuid=True)),
        sa.Column("position", sa.Double(), nullable=False),
        sa.Column("is_archived", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("archived_at", sa.DateTime(timezone=True)),
        sa.Column("version", sa.Integer(), server_default="1", nullable=False),
        sa.Column("created_by", sa.String(320), nullable=False),
        sa.Column("updated_by", sa.String(320), nullable=False),
        *_timestamps(),
        sa.ForeignKeyConstraint(
            ["area_id", "folder_id"],
            ["project_folders.area_id", "project_folders.id"],
            name="fk_projects_folder_same_area",
        ),
        sa.CheckConstraint(
            "status IN ('created', 'ideation', 'in_progress', 'on_hold', 'completed', 'cancelled')",
            name="ck_projects_status",
        ),
        sa.CheckConstraint("priority IN ('low', 'medium', 'high')", name="ck_projects_priority"),
        sa.CheckConstraint(f"color IN {_COLORS}", name="ck_projects_color"),
        sa.CheckConstraint(
            "end_date IS NULL OR end_date >= start_date", name="ck_projects_end_after_start"
        ),
        sa.CheckConstraint(
            "is_archived = (archived_at IS NOT NULL)", name="ck_projects_archived_at"
        ),
    )
    op.create_index(
        "ix_projects_area_folder_position", "projects", ["area_id", "folder_id", "position"]
    )

    op.create_table(
        "project_epics",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("area_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "project_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("description", sa.Text()),
        sa.Column("color", sa.String(20), server_default="default", nullable=False),
        sa.Column("start_date", sa.Date()),
        sa.Column("end_date", sa.Date()),
        *_common(),
        sa.UniqueConstraint("project_id", "id", name="uq_project_epics_project_id_id"),
        sa.CheckConstraint(f"color IN {_COLORS}", name="ck_project_epics_color"),
        sa.CheckConstraint(_DATES, name="ck_project_epics_end_after_start"),
    )
    op.create_index(
        "ix_project_epics_project_position", "project_epics", ["project_id", "position"]
    )

    op.create_table(
        "project_tasks",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("area_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "project_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("epic_id", postgresql.UUID(as_uuid=True)),
        sa.Column("name", sa.String(300), nullable=False),
        sa.Column("description", sa.Text()),
        sa.Column("status", sa.String(20), server_default="backlog", nullable=False),
        sa.Column("priority", sa.String(10), server_default="medium", nullable=False),
        sa.Column("owner_user_id", postgresql.UUID(as_uuid=True)),
        sa.Column("start_date", sa.Date()),
        sa.Column("end_date", sa.Date()),
        sa.Column("blocked_reason", sa.String(500)),
        *_common(),
        # MATCH SIMPLE: a NULL epic_id skips the check, so a task may have no epic.
        sa.ForeignKeyConstraint(
            ["project_id", "epic_id"],
            ["project_epics.project_id", "project_epics.id"],
            name="fk_project_tasks_epic_same_project",
        ),
        sa.CheckConstraint(f"status IN {_TASK_STATUSES}", name="ck_project_tasks_status"),
        sa.CheckConstraint(
            "priority IN ('low', 'medium', 'high')", name="ck_project_tasks_priority"
        ),
        sa.CheckConstraint(_DATES, name="ck_project_tasks_end_after_start"),
    )
    op.create_index(
        "ix_project_tasks_project_status_position",
        "project_tasks",
        ["project_id", "status", "position"],
    )
    op.create_index("ix_project_tasks_epic_id", "project_tasks", ["epic_id"])


def downgrade() -> None:
    """Drop the project tables."""

    op.drop_index("ix_project_tasks_epic_id", table_name="project_tasks")
    op.drop_index("ix_project_tasks_project_status_position", table_name="project_tasks")
    op.drop_table("project_tasks")
    op.drop_index("ix_project_epics_project_position", table_name="project_epics")
    op.drop_table("project_epics")
    op.drop_index("ix_projects_area_folder_position", table_name="projects")
    op.drop_table("projects")
    op.drop_index("uq_project_folders_area_name_lower", table_name="project_folders")
    op.drop_table("project_folders")
