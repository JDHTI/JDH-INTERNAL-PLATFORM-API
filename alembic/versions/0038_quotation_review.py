"""Quoter: confirmation and validation, list creation and the cost of the area's skill.

- ``quotations``: the states ``pendiente_validacion``, ``creada`` and ``actualizada``
  replace ``generado`` (a generated quotation becomes ``actualizada``); ``list_action``
  (update the customer's list or create a new one), ``new_list_name``, ``regional`` (liquor
  tax Nacional or Costa), ``price_family``, ``stock_source``, ``below_target_count``, who
  confirmed and who validated, and the reviewer's note. ``generated_*`` become
  ``validated_*``.
- ``quotation_lines``: the two costs the rule compares (quotation, discarded quotation and
  inventory) and which one won, the optimal price, the below-target alert and whether the
  item is active. ``cost_is_global`` goes: the cost no longer averages every warehouse.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0038_quotation_review"
down_revision: str | None = "0037_quotations"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_NEW_STATUSES = "status IN ('borrador', 'pendiente_validacion', 'creada', 'actualizada')"
_OLD_STATUSES = "status IN ('borrador', 'generado')"


def upgrade() -> None:
    op.drop_constraint("ck_quotations_status", "quotations", type_="check")
    op.alter_column("quotations", "generated_at", new_column_name="validated_at")
    op.alter_column("quotations", "generated_by", new_column_name="validated_by")
    op.execute("UPDATE quotations SET status = 'actualizada' WHERE status = 'generado'")
    op.create_check_constraint("ck_quotations_status", "quotations", _NEW_STATUSES)
    op.add_column(
        "quotations",
        sa.Column("list_action", sa.String(20), nullable=False, server_default="actualizacion"),
    )
    op.add_column("quotations", sa.Column("new_list_name", sa.String(40)))
    op.add_column(
        "quotations",
        sa.Column("regional", sa.String(10), nullable=False, server_default="nacional"),
    )
    op.add_column(
        "quotations",
        sa.Column("price_family", sa.String(20), nullable=False, server_default="generales"),
    )
    op.add_column("quotations", sa.Column("stock_source", sa.Text()))
    op.add_column(
        "quotations",
        sa.Column("below_target_count", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column("quotations", sa.Column("confirmed_at", sa.DateTime(timezone=True)))
    op.add_column("quotations", sa.Column("confirmed_by", sa.String(320)))
    op.add_column("quotations", sa.Column("review_note", sa.Text()))
    for name in ("list_action", "regional", "price_family", "below_target_count"):
        op.alter_column("quotations", name, server_default=None)
    # List 1 is never updated from a quotation: its customers get a new list.
    op.execute("UPDATE quotations SET list_action = 'creacion' WHERE price_list_id = '1'")
    op.create_check_constraint(
        "ck_quotations_list_action", "quotations", "list_action IN ('actualizacion', 'creacion')"
    )
    op.create_check_constraint(
        "ck_quotations_regional", "quotations", "regional IN ('nacional', 'costa')"
    )

    op.drop_column("quotation_lines", "cost_is_global")
    for name in ("quote_cost", "discarded_quote_cost", "inventory_cost", "optimal_price"):
        op.add_column("quotation_lines", sa.Column(name, sa.Numeric(18, 4)))
    op.add_column("quotation_lines", sa.Column("cost_source", sa.String(20)))
    op.add_column(
        "quotation_lines",
        sa.Column("below_target", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column(
        "quotation_lines",
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
    )
    op.alter_column("quotation_lines", "below_target", server_default=None)
    op.alter_column("quotation_lines", "is_active", server_default=None)


def downgrade() -> None:
    for name in (
        "is_active",
        "below_target",
        "cost_source",
        "optimal_price",
        "inventory_cost",
        "discarded_quote_cost",
        "quote_cost",
    ):
        op.drop_column("quotation_lines", name)
    op.add_column(
        "quotation_lines",
        sa.Column("cost_is_global", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.alter_column("quotation_lines", "cost_is_global", server_default=None)

    op.drop_constraint("ck_quotations_regional", "quotations", type_="check")
    op.drop_constraint("ck_quotations_list_action", "quotations", type_="check")
    for name in (
        "review_note",
        "confirmed_by",
        "confirmed_at",
        "below_target_count",
        "stock_source",
        "price_family",
        "regional",
        "new_list_name",
        "list_action",
    ):
        op.drop_column("quotations", name)
    op.drop_constraint("ck_quotations_status", "quotations", type_="check")
    # Pending and closed quotations go back to the two old states.
    op.execute(
        "UPDATE quotations SET status = CASE WHEN status IN ('creada', 'actualizada') "
        "THEN 'generado' ELSE 'borrador' END"
    )
    op.alter_column("quotations", "validated_at", new_column_name="generated_at")
    op.alter_column("quotations", "validated_by", new_column_name="generated_by")
    op.create_check_constraint("ck_quotations_status", "quotations", _OLD_STATUSES)
