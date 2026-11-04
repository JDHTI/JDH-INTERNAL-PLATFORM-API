"""Classification and products on the case, and the delivery date of the order.

Until now the area, subject, cause and pickup order lived only on the public request, and
the case read them from there. The case now keeps them (with the route of the cause) so
they can be filtered and, later, reclassified. The products of the request are copied to
``pqrsf_case_products``, which will also hold the lines of internal and email cases. The
requester now gives the delivery date of the order when there are products.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0038_pqrsf_case_classification"
down_revision: str | None = "0037_quotations"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add the classification columns, the product lines and copy them from the requests."""

    op.add_column("pqrsf_requests", sa.Column("delivery_date", sa.Date()))

    op.add_column(
        "pqrsf_cases",
        sa.Column(
            "requested_area_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("pqrsf_dependencies.id", ondelete="SET NULL"),
        ),
    )
    op.add_column(
        "pqrsf_cases",
        sa.Column(
            "subject_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("pqrsf_subjects.id", ondelete="SET NULL"),
        ),
    )
    op.add_column(
        "pqrsf_cases",
        sa.Column(
            "cause_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("pqrsf_causes.id", ondelete="SET NULL"),
        ),
    )
    op.add_column("pqrsf_cases", sa.Column("route", sa.String(10)))
    op.add_column("pqrsf_cases", sa.Column("pickup_order_requested", sa.String(8)))
    op.add_column("pqrsf_cases", sa.Column("pickup_order_number", sa.String(40)))
    op.add_column("pqrsf_cases", sa.Column("delivery_date", sa.Date()))
    op.create_check_constraint(
        "ck_pqrsf_cases_route",
        "pqrsf_cases",
        "route IS NULL OR route IN ('supplier', 'internal', 'none')",
    )
    op.create_check_constraint(
        "ck_pqrsf_cases_pickup_order",
        "pqrsf_cases",
        "pickup_order_requested IS NULL OR pickup_order_requested IN ('yes', 'no', 'unknown')",
    )
    op.create_index("ix_pqrsf_cases_requested_area", "pqrsf_cases", ["requested_area_id"])
    op.create_index("ix_pqrsf_cases_subject", "pqrsf_cases", ["subject_id"])
    op.create_index("ix_pqrsf_cases_cause", "pqrsf_cases", ["cause_id"])

    op.create_table(
        "pqrsf_case_products",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "case_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("pqrsf_cases.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("source", sa.String(10), nullable=False),
        sa.Column("invoice_number", sa.String(40), nullable=False),
        sa.Column("sku", sa.String(40), nullable=False),
        # Copies of the product master at submission, so they read the same later.
        sa.Column("description", sa.Text()),
        sa.Column("supplier_code", sa.String(60)),
        sa.Column("supplier_name", sa.String(300)),
        sa.Column("lot", sa.String(60), nullable=False),
        sa.Column("expiry_date", sa.Date(), nullable=False),
        sa.Column("affected_quantity", sa.Integer(), nullable=False),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint(
            "source IN ('request', 'internal', 'email')", name="ck_pqrsf_case_products_source"
        ),
        sa.CheckConstraint(
            "affected_quantity > 0", name="ck_pqrsf_case_products_affected_quantity"
        ),
    )
    op.create_index("ix_pqrsf_case_products_case", "pqrsf_case_products", ["case_id", "sort_order"])
    op.create_index("ix_pqrsf_case_products_supplier", "pqrsf_case_products", ["supplier_code"])
    op.create_index("ix_pqrsf_case_products_sku", "pqrsf_case_products", ["sku"])

    # The cases opened from the public form take what their request already says.
    op.execute(
        """
        UPDATE pqrsf_cases AS c
        SET requested_area_id = r.dependency_id,
            subject_id = r.subject_id,
            cause_id = r.cause_id,
            route = k.route,
            pickup_order_requested = r.pickup_order_requested,
            pickup_order_number = r.pickup_order_number
        FROM pqrsf_requests AS r
        LEFT JOIN pqrsf_causes AS k ON k.id = r.cause_id
        WHERE c.request_id = r.id
        """
    )
    op.execute(
        """
        INSERT INTO pqrsf_case_products (
            id, case_id, source, invoice_number, sku, description, supplier_code,
            supplier_name, lot, expiry_date, affected_quantity, sort_order
        )
        SELECT gen_random_uuid(), c.id, 'request',
               line.value ->> 'invoice_number',
               line.value ->> 'sku',
               line.value ->> 'description',
               line.value ->> 'supplier_code',
               line.value ->> 'supplier_name',
               line.value ->> 'lot',
               (line.value ->> 'expiry_date')::date,
               (line.value ->> 'affected_quantity')::integer,
               line.position - 1
        FROM pqrsf_cases AS c
        JOIN pqrsf_requests AS r ON r.id = c.request_id
        CROSS JOIN LATERAL jsonb_array_elements(
            CASE WHEN jsonb_typeof(r.products) = 'array' THEN r.products ELSE '[]'::jsonb END
        ) WITH ORDINALITY AS line(value, position)
        """
    )


def downgrade() -> None:
    """Drop the product lines and the classification columns."""

    op.drop_index("ix_pqrsf_case_products_sku", table_name="pqrsf_case_products")
    op.drop_index("ix_pqrsf_case_products_supplier", table_name="pqrsf_case_products")
    op.drop_index("ix_pqrsf_case_products_case", table_name="pqrsf_case_products")
    op.drop_table("pqrsf_case_products")
    op.drop_index("ix_pqrsf_cases_cause", table_name="pqrsf_cases")
    op.drop_index("ix_pqrsf_cases_subject", table_name="pqrsf_cases")
    op.drop_index("ix_pqrsf_cases_requested_area", table_name="pqrsf_cases")
    op.drop_constraint("ck_pqrsf_cases_pickup_order", "pqrsf_cases", type_="check")
    op.drop_constraint("ck_pqrsf_cases_route", "pqrsf_cases", type_="check")
    for column in (
        "delivery_date",
        "pickup_order_number",
        "pickup_order_requested",
        "route",
        "cause_id",
        "subject_id",
        "requested_area_id",
    ):
        op.drop_column("pqrsf_cases", column)
    op.drop_column("pqrsf_requests", "delivery_date")
