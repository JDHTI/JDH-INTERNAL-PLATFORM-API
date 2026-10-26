"""Supplier contacts the cases write to.

Filled each time someone sends a message to a supplier from a case, so the next case of
that supplier suggests the same addresses; Supplies' catalog can be loaded here later.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0026_pqrsf_supplier_contacts"
down_revision: str | None = "0025_pqrsf_request_area"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create the contacts table."""

    op.create_table(
        "pqrsf_supplier_contacts",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        # ERP supplier code ("Cod. Proveedor"); empty for an address used without one.
        sa.Column("supplier_code", sa.String(40), nullable=False, server_default=""),
        sa.Column("supplier_name", sa.String(200)),
        sa.Column("email", sa.String(320), nullable=False),
        sa.Column("contact_name", sa.String(200)),
        sa.Column("last_used_at", sa.DateTime(timezone=True)),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.UniqueConstraint(
            "supplier_code", "email", name="pqrsf_supplier_contacts_code_email_key"
        ),
    )
    op.create_index("ix_pqrsf_supplier_contacts_email", "pqrsf_supplier_contacts", ["email"])


def downgrade() -> None:
    """Drop the contacts table."""

    op.drop_index("ix_pqrsf_supplier_contacts_email", table_name="pqrsf_supplier_contacts")
    op.drop_table("pqrsf_supplier_contacts")
