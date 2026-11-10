"""Quoter: validated quotations wait to be loaded, centers can be chosen, temporary customers.

- ``status``: ``creada`` and ``actualizada`` become ``pendiente_carga``; a validator then marks
  them ``cargada`` (``uploaded_at``, ``uploaded_by``). ``list_action`` still says whether the
  list is created or updated.
- ``billing_center_source`` (``auto`` from the branches, ``manual`` chosen) and
  ``billing_center_name``.
- ``temporary_customer`` (JSON): a customer not in SIESA yet. Such a quotation has no list,
  so ``price_list_id`` takes ``NULL``.
- ``include_variation``: the customer offer shows the variation of each price.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0052_quotation_upload_flow"
down_revision: str | Sequence[str] | None = "0051_quotation_offer"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_NEW_STATUSES = "status IN ('borrador', 'pendiente_validacion', 'pendiente_carga', 'cargada')"
_OLD_STATUSES = "status IN ('borrador', 'pendiente_validacion', 'creada', 'actualizada')"


def upgrade() -> None:
    op.drop_constraint("ck_quotations_status", "quotations", type_="check")
    op.execute(
        "UPDATE quotations SET status = 'pendiente_carga' WHERE status IN ('creada', 'actualizada')"
    )
    op.create_check_constraint("ck_quotations_status", "quotations", _NEW_STATUSES)
    op.add_column("quotations", sa.Column("uploaded_at", sa.DateTime(timezone=True)))
    op.add_column("quotations", sa.Column("uploaded_by", sa.String(320)))
    op.add_column(
        "quotations",
        sa.Column("billing_center_source", sa.String(10), nullable=False, server_default="auto"),
    )
    op.alter_column("quotations", "billing_center_source", server_default=None)
    op.create_check_constraint(
        "ck_quotations_billing_center_source",
        "quotations",
        "billing_center_source IN ('auto', 'manual')",
    )
    op.add_column("quotations", sa.Column("billing_center_name", sa.Text()))
    op.add_column("quotations", sa.Column("temporary_customer", postgresql.JSONB()))
    op.alter_column("quotations", "price_list_id", existing_type=sa.String(3), nullable=True)
    op.add_column(
        "quotations",
        sa.Column("include_variation", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.alter_column("quotations", "include_variation", server_default=None)


def downgrade() -> None:
    op.drop_column("quotations", "include_variation")
    # Temporary customers have no list: they cannot exist before this revision.
    op.execute("DELETE FROM quotations WHERE temporary_customer IS NOT NULL")
    op.alter_column("quotations", "price_list_id", existing_type=sa.String(3), nullable=False)
    op.drop_column("quotations", "temporary_customer")
    op.drop_column("quotations", "billing_center_name")
    op.drop_constraint("ck_quotations_billing_center_source", "quotations", type_="check")
    op.drop_column("quotations", "billing_center_source")
    op.drop_column("quotations", "uploaded_by")
    op.drop_column("quotations", "uploaded_at")
    op.drop_constraint("ck_quotations_status", "quotations", type_="check")
    op.execute(
        "UPDATE quotations SET status = CASE WHEN list_action = 'creacion' THEN 'creada' "
        "ELSE 'actualizada' END WHERE status IN ('pendiente_carga', 'cargada')"
    )
    op.create_check_constraint("ck_quotations_status", "quotations", _OLD_STATUSES)
