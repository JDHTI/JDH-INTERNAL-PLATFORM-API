"""PQRSF: how a case ended, and the improvement opportunity (OM, P.06) sent to a supplier.

- ``pqrsf_cases``:
  - ``resolution`` (procede / no procede), ``internal_failure``, ``resolution_note`` and
    ``resolved_at``, set when a complaint or a claim is finished;
  - ``om_sent_at``, the «OM enviada» flag (not a status).
- ``pqrsf_sites``: ``city``, ``address`` and ``om_code`` (three letters, 1181BOG), which the OM
  prints.
- ``pqrsf_document_templates``: the Word templates kept in the bucket, one active per kind.
- ``pqrsf_om_sequence``: one row with the next OM number; Calidad sets where it starts.
- ``pqrsf_improvement_opportunities``: one OM per case and supplier, with its Word and PDF.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0064_pqrsf_resolution_and_om"
down_revision: str | None = "0063_territory"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TZ = sa.DateTime(timezone=True)
_UUID = postgresql.UUID(as_uuid=True)


def upgrade() -> None:
    op.add_column("pqrsf_cases", sa.Column("resolution", sa.String(12)))
    op.add_column(
        "pqrsf_cases",
        sa.Column("internal_failure", sa.Boolean(), nullable=False, server_default="false"),
    )
    op.add_column("pqrsf_cases", sa.Column("resolution_note", sa.Text()))
    op.add_column("pqrsf_cases", sa.Column("resolved_at", _TZ))
    op.add_column("pqrsf_cases", sa.Column("om_sent_at", _TZ))
    op.create_check_constraint(
        "ck_pqrsf_cases_resolution",
        "pqrsf_cases",
        "resolution IS NULL OR resolution IN ('procede', 'no_procede')",
    )

    op.add_column("pqrsf_sites", sa.Column("city", sa.String(80)))
    op.add_column("pqrsf_sites", sa.Column("address", sa.String(200)))
    op.add_column("pqrsf_sites", sa.Column("om_code", sa.String(3)))
    op.create_unique_constraint("pqrsf_sites_om_code_key", "pqrsf_sites", ["om_code"])
    op.create_check_constraint(
        "ck_pqrsf_sites_om_code", "pqrsf_sites", "om_code IS NULL OR om_code ~ '^[A-Z]{3}$'"
    )

    op.create_table(
        "pqrsf_document_templates",
        sa.Column("id", _UUID, primary_key=True),
        sa.Column("kind", sa.String(20), nullable=False),
        sa.Column("version_label", sa.String(60), nullable=False),
        sa.Column("filename", sa.String(255), nullable=False),
        sa.Column("storage_key", sa.String(500), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("checksum_sha256", sa.String(64), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("uploaded_by", _UUID),
        sa.Column("created_at", _TZ, nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("kind IN ('om')", name="ck_pqrsf_document_templates_kind"),
    )
    op.create_index(
        "ix_pqrsf_document_templates_active",
        "pqrsf_document_templates",
        ["kind"],
        unique=True,
        postgresql_where=sa.text("is_active"),
    )

    op.create_table(
        "pqrsf_om_sequence",
        sa.Column("id", sa.SmallInteger(), primary_key=True),
        sa.Column("next_number", sa.Integer(), nullable=False),
        sa.Column("updated_by", _UUID),
        sa.Column("updated_at", _TZ, nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("id = 1", name="ck_pqrsf_om_sequence_single"),
        sa.CheckConstraint("next_number > 0", name="ck_pqrsf_om_sequence_positive"),
    )
    op.execute("INSERT INTO pqrsf_om_sequence (id, next_number) VALUES (1, 1)")

    op.create_table(
        "pqrsf_improvement_opportunities",
        sa.Column("id", _UUID, primary_key=True),
        sa.Column(
            "case_id",
            _UUID,
            sa.ForeignKey("pqrsf_cases.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("number", sa.Integer(), nullable=False),
        sa.Column("code", sa.String(20), nullable=False),
        sa.Column("supplier_code", sa.String(40)),
        sa.Column("supplier_name", sa.String(200)),
        sa.Column("immediate_actions", sa.Text()),
        sa.Column("request_note", sa.Text()),
        sa.Column("document_number", sa.String(200)),
        sa.Column("document_date", sa.Date()),
        sa.Column("evidence_ids", postgresql.ARRAY(_UUID), nullable=False, server_default="{}"),
        sa.Column("signer_user_id", _UUID),
        sa.Column("signer_name", sa.String(200)),
        sa.Column("signer_position", sa.String(200)),
        sa.Column("signer_email", sa.String(320)),
        sa.Column("signer_phone", sa.String(60)),
        sa.Column(
            "template_id",
            _UUID,
            sa.ForeignKey("pqrsf_document_templates.id", ondelete="SET NULL"),
        ),
        sa.Column("docx_key", sa.String(500)),
        sa.Column("pdf_key", sa.String(500)),
        sa.Column("edited", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("status", sa.String(10), nullable=False, server_default="draft"),
        sa.Column("generated_at", _TZ),
        sa.Column("sent_at", _TZ),
        sa.Column("created_by", _UUID),
        sa.Column("created_at", _TZ, nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", _TZ, nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint(
            "status IN ('draft', 'sent')", name="ck_pqrsf_improvement_opportunities_status"
        ),
        sa.UniqueConstraint("number", name="pqrsf_improvement_opportunities_number_key"),
    )
    op.create_index(
        "ix_pqrsf_improvement_opportunities_case",
        "pqrsf_improvement_opportunities",
        ["case_id", "created_at"],
    )
    # One OM per supplier of a case (cases without supplier code share one).
    op.create_index(
        "ux_pqrsf_improvement_opportunities_supplier",
        "pqrsf_improvement_opportunities",
        ["case_id", sa.text("coalesce(supplier_code, '')")],
        unique=True,
    )


def downgrade() -> None:
    op.drop_table("pqrsf_improvement_opportunities")
    op.drop_table("pqrsf_om_sequence")
    op.drop_index("ix_pqrsf_document_templates_active", table_name="pqrsf_document_templates")
    op.drop_table("pqrsf_document_templates")
    op.drop_constraint("ck_pqrsf_sites_om_code", "pqrsf_sites", type_="check")
    op.drop_constraint("pqrsf_sites_om_code_key", "pqrsf_sites", type_="unique")
    op.drop_column("pqrsf_sites", "om_code")
    op.drop_column("pqrsf_sites", "address")
    op.drop_column("pqrsf_sites", "city")
    op.drop_constraint("ck_pqrsf_cases_resolution", "pqrsf_cases", type_="check")
    op.drop_column("pqrsf_cases", "om_sent_at")
    op.drop_column("pqrsf_cases", "resolved_at")
    op.drop_column("pqrsf_cases", "resolution_note")
    op.drop_column("pqrsf_cases", "internal_failure")
    op.drop_column("pqrsf_cases", "resolution")
