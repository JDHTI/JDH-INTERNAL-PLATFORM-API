"""The offer is not sent from the platform: no ``enviada`` status and no sending stamps.

BI, 2026-11-19: the advisor downloads the offer's PDF or Excel and sends it to the customer by
hand. An ``enviada`` quotation goes back to draft, and ``sent_at`` / ``sent_by`` (migration
``0071``) go away. Only updating or creating a price list goes to validation.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0072_quotation_sent_draft"
down_revision: str | None = "0071_quotation_sent"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_WITH_SENT = (
    "status IN ('borrador', 'enviada', 'pendiente_validacion', 'pendiente_carga', 'cargada')"
)
_WITHOUT_SENT = "status IN ('borrador', 'pendiente_validacion', 'pendiente_carga', 'cargada')"


def upgrade() -> None:
    """A sent quotation goes back to draft; the status and the stamps go away."""

    op.execute("UPDATE quotations SET status = 'borrador' WHERE status = 'enviada'")
    op.drop_constraint("ck_quotations_status", "quotations", type_="check")
    op.create_check_constraint("ck_quotations_status", "quotations", _WITHOUT_SENT)
    op.drop_column("quotations", "sent_by")
    op.drop_column("quotations", "sent_at")


def downgrade() -> None:
    """Allow the status and the stamps again (empty); the drafts stay drafts."""

    op.add_column("quotations", sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("quotations", sa.Column("sent_by", sa.String(320), nullable=True))
    op.drop_constraint("ck_quotations_status", "quotations", type_="check")
    op.create_check_constraint("ck_quotations_status", "quotations", _WITH_SENT)
