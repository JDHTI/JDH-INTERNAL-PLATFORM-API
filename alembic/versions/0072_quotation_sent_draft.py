"""Sending the offer to the customer is not a status: an ``enviada`` quotation is a draft.

BI, 2026-11-19: the offer goes to the customer independently of the price list; the quotation
stays ``borrador`` (``sent_at`` and ``sent_by`` say it was sent) and then may go to validation
to update or create a list.
"""

from collections.abc import Sequence

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
    """A sent quotation goes back to draft (it keeps its stamps) and the status goes away."""

    op.execute("UPDATE quotations SET status = 'borrador' WHERE status = 'enviada'")
    op.drop_constraint("ck_quotations_status", "quotations", type_="check")
    op.create_check_constraint("ck_quotations_status", "quotations", _WITHOUT_SENT)


def downgrade() -> None:
    """Allow the status again; the drafts stay drafts."""

    op.drop_constraint("ck_quotations_status", "quotations", type_="check")
    op.create_check_constraint("ck_quotations_status", "quotations", _WITH_SENT)
