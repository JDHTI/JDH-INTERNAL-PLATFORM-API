"""The area the requester picks on the public form.

``pqrsf_request_types.asks_area`` says which types ask for it (all but compliments).
The area is kept on the request and on the suggestion; it decides who attends a case
only when no cause, subject or document category does.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0025_pqrsf_request_area"
down_revision: str | None = "0024_pqrsf_classification"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add the flag of the types and the area of requests and suggestions."""

    op.add_column(
        "pqrsf_request_types",
        sa.Column("asks_area", sa.Boolean(), nullable=False, server_default="false"),
    )
    op.execute(
        "UPDATE pqrsf_request_types SET asks_area = true "
        "WHERE code IN ('peticion', 'queja', 'reclamo', 'sugerencia')"
    )
    for table in ("pqrsf_requests", "pqrsf_feedback"):
        op.add_column(
            table,
            sa.Column(
                "dependency_id",
                postgresql.UUID(as_uuid=True),
                sa.ForeignKey("pqrsf_dependencies.id", ondelete="SET NULL"),
            ),
        )


def downgrade() -> None:
    """Drop the area and the flag."""

    op.drop_column("pqrsf_feedback", "dependency_id")
    op.drop_column("pqrsf_requests", "dependency_id")
    op.drop_column("pqrsf_request_types", "asks_area")
