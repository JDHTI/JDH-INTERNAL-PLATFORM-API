"""Costos: the «Regional» of each MAESTRA row, the PVP, and the quotation net of its Tasa 1
(2026-11-24).

- ``cost_increase_request_lines.regional`` (``NACIONAL``, a regional or ``RESTO DE
  REGIONALES``) and ``public_price`` (PVP without VAT). A reference may have one line per
  regional, so the unique key adds the regional. ``cost_increase_line_discounts`` goes: the
  discount of a regional is now the discount of its row.
- ``product_price_increases.row_key`` adds the regional of a row that has one other than
  ``NACIONAL`` (as ``PriceIncreaseValues.key`` does now), so two regionals of a reference on the
  same dates are two rows.
- ``cost_entry_novelties``: the Tasa 1 was stored in points (``20.0000``); it becomes a fraction
  (``0.2``). ``quotation`` becomes the quotation less its Tasa 1 (what the entry should have
  cost) and ``quotation_list_price`` keeps the one before it; the difference follows. The daily
  job re-reads the window and closes what is no longer a novelty. ``previous_price_2`` is
  renamed ``antepenultimate_price``.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0084_costs_regional_scope"
down_revision: str | Sequence[str] | None = "0083_commercial_roles"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# The regional as ``regional_key`` writes it: single spaces, upper case.
_REGIONAL = "upper(regexp_replace(btrim(regional), '\\s+', ' ', 'g'))"


def upgrade() -> None:
    # Increase requests: one line per reference and regional scope.
    op.add_column(
        "cost_increase_request_lines",
        sa.Column("regional", sa.String(30), nullable=False, server_default="NACIONAL"),
    )
    op.add_column("cost_increase_request_lines", sa.Column("public_price", sa.Numeric(18, 2)))
    op.create_check_constraint(
        "ck_cost_increase_request_lines_public_price",
        "cost_increase_request_lines",
        "public_price IS NULL OR public_price > 0",
    )
    op.drop_constraint(
        "uq_cost_increase_request_lines_sku", "cost_increase_request_lines", type_="unique"
    )
    op.create_unique_constraint(
        "uq_cost_increase_request_lines_sku_regional",
        "cost_increase_request_lines",
        ["request_id", "sku", "regional"],
    )
    op.drop_table("cost_increase_line_discounts")

    # History of increases: a regional row keys apart from the national one.
    op.execute(
        f"""
        update product_price_increases
           set row_key = row_key || '|' || {_REGIONAL}
         where regional is not null and {_REGIONAL} not in ('', 'NACIONAL')
        """
    )

    # Novelties: Tasa 1 as a fraction and the quotation net of it.
    op.alter_column(
        "cost_entry_novelties",
        "discount_pct",
        type_=sa.Numeric(9, 6),
        existing_type=sa.Numeric(9, 4),
    )
    op.add_column("cost_entry_novelties", sa.Column("quotation_list_price", sa.Numeric(18, 4)))
    op.alter_column(
        "cost_entry_novelties", "previous_price_2", new_column_name="antepenultimate_price"
    )
    op.execute(
        """
        update cost_entry_novelties
           set discount_pct = abs(discount_pct) / 100
         where discount_pct is not null
        """
    )
    op.execute(
        """
        update cost_entry_novelties
           set quotation_list_price = quotation,
               quotation = round(quotation * (1 - coalesce(discount_pct, 0)), 4)
         where quotation is not null
        """
    )
    op.execute(
        """
        update cost_entry_novelties
           set difference = unit_price - quotation,
               difference_pct = case when quotation = 0 then null
                                     else round(unit_price / quotation - 1, 6) end
         where quotation is not null and coalesce(discount_pct, 0) <> 0
        """
    )


def downgrade() -> None:
    op.execute(
        """
        update cost_entry_novelties
           set quotation = quotation_list_price,
               difference = unit_price - quotation_list_price,
               difference_pct = case when quotation_list_price = 0 then null
                                     else round(unit_price / quotation_list_price - 1, 6) end
         where quotation_list_price is not null
        """
    )
    op.execute(
        "update cost_entry_novelties set discount_pct = discount_pct * 100 "
        "where discount_pct is not null"
    )
    op.alter_column(
        "cost_entry_novelties", "antepenultimate_price", new_column_name="previous_price_2"
    )
    op.drop_column("cost_entry_novelties", "quotation_list_price")
    op.alter_column(
        "cost_entry_novelties",
        "discount_pct",
        type_=sa.Numeric(9, 4),
        existing_type=sa.Numeric(9, 6),
    )

    # The three-part key back, for the first row of each; a second regional of the same
    # reference and dates keeps its longer key (the former upload just never matches it).
    op.execute(
        """
        with ranked as (
            select id,
                   split_part(row_key, '|', 1) || '|' || split_part(row_key, '|', 2) || '|'
                   || split_part(row_key, '|', 3) as short_key,
                   row_number() over (
                       partition by split_part(row_key, '|', 1), split_part(row_key, '|', 2),
                                    split_part(row_key, '|', 3)
                       order by created_at, id
                   ) as position
              from product_price_increases
             where row_key like '%|%|%|%'
        )
        update product_price_increases p
           set row_key = ranked.short_key
          from ranked
         where p.id = ranked.id and ranked.position = 1
           and not exists (
               select 1 from product_price_increases o where o.row_key = ranked.short_key
           )
        """
    )

    op.create_table(
        "cost_increase_line_discounts",
        sa.Column(
            "line_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("cost_increase_request_lines.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("regional", sa.String(20), primary_key=True),
        sa.Column("discount_pct", sa.Numeric(9, 6), nullable=False),
        sa.CheckConstraint(
            "discount_pct >= 0 AND discount_pct <= 1",
            name="ck_cost_increase_line_discounts_pct",
        ),
    )
    # One line per reference again: the first of each keeps it.
    op.execute(
        """
        delete from cost_increase_request_lines l
         using cost_increase_request_lines o
         where l.request_id = o.request_id and l.sku = o.sku
           and (coalesce(l.row_number, 0), l.id) > (coalesce(o.row_number, 0), o.id)
        """
    )
    op.drop_constraint(
        "uq_cost_increase_request_lines_sku_regional",
        "cost_increase_request_lines",
        type_="unique",
    )
    op.create_unique_constraint(
        "uq_cost_increase_request_lines_sku",
        "cost_increase_request_lines",
        ["request_id", "sku"],
    )
    op.drop_constraint(
        "ck_cost_increase_request_lines_public_price", "cost_increase_request_lines", type_="check"
    )
    op.drop_column("cost_increase_request_lines", "public_price")
    op.drop_column("cost_increase_request_lines", "regional")
