"""The sales executive of a case: who sold the invoice the complaint or claim is about.

``sales_executive_name`` already existed as typed text. The case now also keeps the
seller's code and email, the platform user they match (so the executive can follow the
case) and where it came from: the invoice in the sales data, the comercial who filed it,
or a person who typed it.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0027_pqrsf_sales_executive"
down_revision: str | None = "0026_pqrsf_supplier_contacts"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add the seller code, email, user and source to the cases."""

    op.add_column("pqrsf_cases", sa.Column("sales_executive_code", sa.String(40)))
    op.add_column("pqrsf_cases", sa.Column("sales_executive_email", sa.String(320)))
    op.add_column(
        "pqrsf_cases", sa.Column("sales_executive_user_id", postgresql.UUID(as_uuid=True))
    )
    op.add_column("pqrsf_cases", sa.Column("sales_executive_source", sa.String(10)))
    op.create_check_constraint(
        "ck_pqrsf_cases_sales_executive_source",
        "pqrsf_cases",
        "sales_executive_source IS NULL "
        "OR sales_executive_source IN ('invoice', 'internal', 'manual')",
    )
    op.execute(
        "UPDATE pqrsf_cases SET sales_executive_source = 'manual' "
        "WHERE sales_executive_name IS NOT NULL"
    )
    op.create_index("ix_pqrsf_cases_sales_executive", "pqrsf_cases", ["sales_executive_user_id"])


def downgrade() -> None:
    """Drop the seller fields."""

    op.drop_index("ix_pqrsf_cases_sales_executive", table_name="pqrsf_cases")
    op.drop_constraint("ck_pqrsf_cases_sales_executive_source", "pqrsf_cases", type_="check")
    op.drop_column("pqrsf_cases", "sales_executive_source")
    op.drop_column("pqrsf_cases", "sales_executive_user_id")
    op.drop_column("pqrsf_cases", "sales_executive_email")
    op.drop_column("pqrsf_cases", "sales_executive_code")
