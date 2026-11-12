"""Main lists, item review, staging loads and liquor tax changes.

- ``quotations.kind`` accepts ``principal``: the update of a main list (module
  ``listas-principales``, locked to BI). Stored list updates of those lists move to it.
- ``quotation_lines.review_status`` (``sin_marcar``, ``por_validar``, ``validada``),
  ``reviewed_by``, ``reviewed_at``, ``note`` and ``load_id``: a list update is reviewed item
  by item and its validated items wait in their list's staging until a load takes them.
- ``price_list_loads``: each load of a list's staging in SIESA, with the rows it wrote.
- ``liquor_tax_updates``: bulk changes of the liquor tax over every list.

Stored list updates switch to the item review: a pending one's priced items become
``por_validar``, a validated one's ``validada`` (staged), and a loaded one's ``validada``
with a load built from the update (``md5(quotation id)``, no rows). All of them go back to
``borrador``: a list update has no state of its own any more. The downgrade cannot bring
those states back.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0059_main_lists_review"
down_revision: str | Sequence[str] | None = "0058_list_updates"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# The main lists as configured on 2026-11-12 (``LIST_UPDATES_MAIN_LISTS``).
_MAIN_LISTS = ("1", "001", "002", "003", "004", "005", "009", "1M", "10T", "10U", "11N", "10N")
_MAIN_LISTS += ("1DR", "35P")
_PRICED = "l.new_price IS NOT NULL AND l.is_active"


def upgrade() -> None:
    op.drop_constraint("ck_quotations_kind", "quotations", type_="check")
    op.create_check_constraint(
        "ck_quotations_kind", "quotations", "kind IN ('cotizacion', 'lista', 'principal')"
    )
    codes = ", ".join(f"'{code}'" for code in _MAIN_LISTS)
    op.execute(
        f"UPDATE quotations SET kind = 'principal' WHERE kind = 'lista' AND price_list_id IN "
        f"({codes})"
    )

    op.create_table(
        "price_list_loads",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("kind", sa.String(20), nullable=False),
        sa.Column("price_list_id", sa.String(3), nullable=False),
        sa.Column("price_list_name", sa.Text()),
        sa.Column("activation_date", sa.Date(), nullable=False),
        sa.Column("line_count", sa.Integer(), nullable=False),
        sa.Column("rows", postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'")),
        sa.Column("created_by", sa.String(320), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("kind IN ('lista', 'principal')", name="ck_price_list_loads_kind"),
    )
    op.alter_column("price_list_loads", "rows", server_default=None)
    op.create_index("ix_price_list_loads_kind_created", "price_list_loads", ["kind", "created_at"])

    op.add_column(
        "quotation_lines",
        sa.Column("review_status", sa.String(20), nullable=False, server_default="sin_marcar"),
    )
    op.alter_column("quotation_lines", "review_status", server_default=None)
    op.add_column("quotation_lines", sa.Column("reviewed_by", sa.String(320)))
    op.add_column("quotation_lines", sa.Column("reviewed_at", sa.DateTime(timezone=True)))
    op.add_column("quotation_lines", sa.Column("note", sa.Text()))
    op.add_column(
        "quotation_lines",
        sa.Column(
            "load_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("price_list_loads.id", ondelete="SET NULL"),
        ),
    )
    op.create_check_constraint(
        "ck_quotation_lines_review_status",
        "quotation_lines",
        "review_status IN ('sin_marcar', 'por_validar', 'validada')",
    )
    op.create_index(
        "ix_quotation_lines_staged",
        "quotation_lines",
        ["quotation_id"],
        postgresql_where=sa.text("review_status = 'validada' AND load_id IS NULL"),
    )
    op.create_index("ix_quotation_lines_load", "quotation_lines", ["load_id"])

    # Stored list updates switch to the item review.
    op.execute(
        "UPDATE quotation_lines l SET review_status = 'por_validar',"
        " reviewed_by = q.confirmed_by, reviewed_at = q.confirmed_at"
        " FROM quotations q WHERE q.id = l.quotation_id AND q.kind IN ('lista', 'principal')"
        f" AND q.status = 'pendiente_validacion' AND {_PRICED}"
    )
    op.execute(
        "UPDATE quotation_lines l SET review_status = 'validada',"
        " reviewed_by = q.validated_by, reviewed_at = q.validated_at"
        " FROM quotations q WHERE q.id = l.quotation_id AND q.kind IN ('lista', 'principal')"
        f" AND q.status IN ('pendiente_carga', 'cargada') AND {_PRICED}"
    )
    op.execute(
        "INSERT INTO price_list_loads (id, kind, price_list_id, price_list_name,"
        " activation_date, line_count, rows, created_by, created_at)"
        " SELECT md5(q.id::text)::uuid, q.kind, coalesce(q.export_price_list_id, q.price_list_id),"
        " q.price_list_name, coalesce(q.activation_date, q.uploaded_at::date, current_date),"
        " (SELECT count(*) FROM quotation_lines l WHERE l.quotation_id = q.id"
        " AND l.review_status = 'validada'), '[]'::jsonb, coalesce(q.uploaded_by, q.updated_by),"
        " coalesce(q.uploaded_at, q.updated_at)"
        " FROM quotations q WHERE q.kind IN ('lista', 'principal') AND q.status = 'cargada'"
        " AND coalesce(q.export_price_list_id, q.price_list_id) IS NOT NULL"
    )
    op.execute(
        "UPDATE quotation_lines l SET load_id = md5(q.id::text)::uuid"
        " FROM quotations q WHERE q.id = l.quotation_id AND q.kind IN ('lista', 'principal')"
        " AND q.status = 'cargada' AND l.review_status = 'validada'"
        " AND coalesce(q.export_price_list_id, q.price_list_id) IS NOT NULL"
    )
    op.execute(
        "UPDATE quotations SET status = 'borrador' WHERE kind IN ('lista', 'principal')"
        " AND status <> 'borrador'"
    )

    op.create_table(
        "liquor_tax_updates",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("rows", postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'")),
        sa.Column(
            "export_rows", postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'")
        ),
        sa.Column("note", sa.Text()),
        sa.Column("review_note", sa.Text()),
        sa.Column("activation_date", sa.Date()),
        sa.Column("created_by", sa.String(320), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("validated_by", sa.String(320)),
        sa.Column("validated_at", sa.DateTime(timezone=True)),
        sa.Column("uploaded_by", sa.String(320)),
        sa.Column("uploaded_at", sa.DateTime(timezone=True)),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.CheckConstraint(
            "status IN ('pendiente_validacion', 'pendiente_carga', 'cargada', 'devuelta')",
            name="ck_liquor_tax_updates_status",
        ),
    )
    for column in ("rows", "export_rows", "version"):
        op.alter_column("liquor_tax_updates", column, server_default=None)
    op.create_index("ix_liquor_tax_updates_created", "liquor_tax_updates", ["created_at"])


def downgrade() -> None:
    op.drop_index("ix_liquor_tax_updates_created", table_name="liquor_tax_updates")
    op.drop_table("liquor_tax_updates")
    op.drop_index("ix_quotation_lines_load", table_name="quotation_lines")
    op.drop_index("ix_quotation_lines_staged", table_name="quotation_lines")
    op.drop_constraint("ck_quotation_lines_review_status", "quotation_lines", type_="check")
    op.drop_column("quotation_lines", "load_id")
    op.drop_column("quotation_lines", "note")
    op.drop_column("quotation_lines", "reviewed_at")
    op.drop_column("quotation_lines", "reviewed_by")
    op.drop_column("quotation_lines", "review_status")
    op.drop_index("ix_price_list_loads_kind_created", table_name="price_list_loads")
    op.drop_table("price_list_loads")
    op.execute("UPDATE quotations SET kind = 'lista' WHERE kind = 'principal'")
    op.drop_constraint("ck_quotations_kind", "quotations", type_="check")
    op.create_check_constraint(
        "ck_quotations_kind", "quotations", "kind IN ('cotizacion', 'lista')"
    )
