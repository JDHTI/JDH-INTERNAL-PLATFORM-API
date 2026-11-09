"""Quoter: the category of each line and products only for the quotation.

- ``quotation_lines.category``: the product category in SIESA (``consumo.productos.categoria``),
  stored with the line like its VAT. Lines stored before stay empty until a refresh.
- ``quotation_lines.origin`` accepts ``local``: a product the advisor quotes that SIESA does
  not have (SKU ``LOC-<n>``). It has no cost and never reaches the files.

It also joins the two heads master had (``0049_order_validation_released`` and
``0049_pqrsf_case_news``), which touch other tables.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0050_quotation_local_lines"
down_revision: str | Sequence[str] | None = (
    "0049_order_validation_released",
    "0049_pqrsf_case_news",
)
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_NEW_ORIGINS = "origin IN ('historico', 'agregado', 'local')"
_OLD_ORIGINS = "origin IN ('historico', 'agregado')"


def upgrade() -> None:
    op.add_column("quotation_lines", sa.Column("category", sa.Text()))
    op.drop_constraint("ck_quotation_lines_origin", "quotation_lines", type_="check")
    op.create_check_constraint("ck_quotation_lines_origin", "quotation_lines", _NEW_ORIGINS)


def downgrade() -> None:
    # Local products do not exist before this revision.
    op.execute("DELETE FROM quotation_lines WHERE origin = 'local'")
    op.drop_constraint("ck_quotation_lines_origin", "quotation_lines", type_="check")
    op.create_check_constraint("ck_quotation_lines_origin", "quotation_lines", _OLD_ORIGINS)
    op.drop_column("quotation_lines", "category")
