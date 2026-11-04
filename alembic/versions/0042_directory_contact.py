"""Contact data and weekly contact schedule of each employee (completar perfil, perfil).

- ``directory_employees`` gains ``mobile_phone``, ``landline_phone`` and
  ``show_mobile_in_directory`` (the mobile shows in the public profile only when true).
- ``directory_contact_schedules``: the windows when a person can be reached, one row per
  weekday and range, replaced whole from the profile.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0042_directory_contact"
down_revision: str | None = "0041_permissions"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_UUID = postgresql.UUID(as_uuid=True)


def upgrade() -> None:
    """Add the contact columns and the schedule table."""

    op.add_column("directory_employees", sa.Column("mobile_phone", sa.String(30)))
    op.add_column("directory_employees", sa.Column("landline_phone", sa.String(30)))
    op.add_column(
        "directory_employees",
        sa.Column("show_mobile_in_directory", sa.Boolean, nullable=False, server_default="false"),
    )
    op.create_table(
        "directory_contact_schedules",
        sa.Column("id", _UUID, primary_key=True),
        sa.Column(
            "employee_id",
            _UUID,
            sa.ForeignKey("directory_employees.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("day", sa.String(10), nullable=False),
        sa.Column("start_time", sa.Time, nullable=False),
        sa.Column("end_time", sa.Time, nullable=False),
        sa.UniqueConstraint(
            "employee_id", "day", "start_time", name="uq_directory_contact_schedules_slot"
        ),
        sa.CheckConstraint(
            "day IN ('LUNES', 'MARTES', 'MIERCOLES', 'JUEVES', 'VIERNES', 'SABADO', 'DOMINGO')",
            name="ck_directory_contact_schedules_day",
        ),
        sa.CheckConstraint("end_time > start_time", name="ck_directory_contact_schedules_range"),
    )
    op.create_index(
        "ix_directory_contact_schedules_employee_id",
        "directory_contact_schedules",
        ["employee_id"],
    )


def downgrade() -> None:
    """Drop the schedule table and the contact columns."""

    op.drop_table("directory_contact_schedules")
    op.drop_column("directory_employees", "show_mobile_in_directory")
    op.drop_column("directory_employees", "landline_phone")
    op.drop_column("directory_employees", "mobile_phone")
