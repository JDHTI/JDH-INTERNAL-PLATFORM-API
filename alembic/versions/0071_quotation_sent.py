"""A quotation sent to the customer without validation (``enviada``).

BI, 2026-11-18: the quotation flows with no block up to sending the offer to the customer, for
Comercial and BI; only updating or creating a price list goes through validation.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0071_quotation_sent"
down_revision: str | None = "0070_request_target_margin"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_BEFORE = "status IN ('borrador', 'pendiente_validacion', 'pendiente_carga', 'cargada')"
_AFTER = "status IN ('borrador', 'enviada', 'pendiente_validacion', 'pendiente_carga', 'cargada')"


def upgrade() -> None:
    """Allow the status and stamp who sent it and when."""

    op.add_column("quotations", sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("quotations", sa.Column("sent_by", sa.String(320), nullable=True))
    op.drop_constraint("ck_quotations_status", "quotations", type_="check")
    op.create_check_constraint("ck_quotations_status", "quotations", _AFTER)


def downgrade() -> None:
    """A sent quotation goes back to draft; the stamps go away."""

    op.execute("UPDATE quotations SET status = 'borrador' WHERE status = 'enviada'")
    op.drop_constraint("ck_quotations_status", "quotations", type_="check")
    op.create_check_constraint("ck_quotations_status", "quotations", _BEFORE)
    op.drop_column("quotations", "sent_by")
    op.drop_column("quotations", "sent_at")
