"""PQRSF: a case flags the news nobody on the team has seen yet.

``pqrsf_cases.news_at`` is when an email from the client or a supplier joined the case.
It clears when its responsible (or a manager) opens the case. It does not touch the
status nor the due date.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0049_pqrsf_case_news"
down_revision: str | None = "0048_masters_live"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("pqrsf_cases", sa.Column("news_at", sa.DateTime(timezone=True)))
    op.create_index(
        "ix_pqrsf_cases_news_at",
        "pqrsf_cases",
        ["news_at"],
        postgresql_where=sa.text("news_at IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("ix_pqrsf_cases_news_at", table_name="pqrsf_cases")
    op.drop_column("pqrsf_cases", "news_at")
