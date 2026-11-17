"""Action plans (P.04.F.02): corrective, preventive and improvement actions of the SIG.

- ``action_plan_processes``: the processes («procedencia») with the codes of the Excel,
  seeded with provisional names that Calidad confirms.
- ``action_plans``: one row per action and attempt, with the code of the Excel column E.
- ``action_plan_corrections``, ``action_plan_causes``, ``action_plan_activities``: the rows
  of each action (immediate corrections, causes, plan with its follow-up and effectiveness).
- ``action_plan_files`` and ``action_plan_events``: evidence in the bucket and history.
- ``pqrsf_dependencies.process_code``: the process of a PQRSF dependency, so an action
  opened from a case takes it.
"""

import uuid
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0065_action_plans"
down_revision: str | None = "0064_pqrsf_resolution_and_om"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TZ = sa.DateTime(timezone=True)
_UUID = postgresql.UUID(as_uuid=True)
_NAMESPACE = uuid.UUID("5d1a2f0e-3c4b-4a6d-9e8f-0a1b2c3d4e5f")

# Codes of the P.04 list «PROCEDENCIA»; names are provisional until Calidad confirms them.
_PROCESSES = [
    ("ADM", "Administrativo"),
    ("ABA", "Abastecimiento"),
    ("ALM", "Almacenamiento"),
    ("CAL", "Calidad"),
    ("COM", "Comercial"),
    ("DIR", "Dirección"),
    ("DIS", "Distribución"),
    ("HUM", "Gestión humana"),
    ("LOG", "Logística"),
    ("MAN", "Mantenimiento"),
    ("OPE", "Operaciones"),
    ("SIG", "Sistema integrado de gestión"),
    ("SEG", "Seguridad y salud en el trabajo"),
]
_DEPENDENCY_PROCESS = {
    "comercial": "COM",
    "distribucion": "DIS",
    "almacenamiento": "ALM",
    "abastecimiento": "ABA",
    "calidad": "CAL",
}


def _people() -> list[sa.Column]:
    """Who does it and by when: the same columns on corrections and activities."""

    return [
        sa.Column("responsible_text", sa.Text()),
        sa.Column(
            "responsible_user_ids", postgresql.ARRAY(_UUID), nullable=False, server_default="{}"
        ),
        sa.Column("due_on", sa.Date()),
        sa.Column("evidence_note", sa.Text()),
    ]


def _children(name: str, extra: list[sa.Column]) -> None:
    op.create_table(
        name,
        sa.Column("id", _UUID, primary_key=True),
        sa.Column(
            "plan_id", _UUID, sa.ForeignKey("action_plans.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
        *extra,
    )
    op.create_index(f"ix_{name}_plan", name, ["plan_id", "sort_order"])


def upgrade() -> None:
    processes = op.create_table(
        "action_plan_processes",
        sa.Column("id", _UUID, primary_key=True),
        sa.Column("code", sa.String(8), nullable=False, unique=True),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
    )
    op.bulk_insert(
        processes,
        [
            {"id": uuid.uuid5(_NAMESPACE, code), "code": code, "name": name, "is_active": True}
            for code, name in _PROCESSES
        ],
    )

    op.create_table(
        "action_plans",
        sa.Column("id", _UUID, primary_key=True),
        sa.Column("number", sa.Integer(), nullable=False),
        sa.Column("attempt", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("code", sa.String(60), nullable=False),
        sa.Column("origin", sa.String(4), nullable=False),
        sa.Column(
            "process_id",
            _UUID,
            sa.ForeignKey("action_plan_processes.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("action_type", sa.String(12), nullable=False),
        sa.Column("rating", sa.String(20), nullable=False),
        sa.Column("reported_on", sa.Date(), nullable=False),
        sa.Column("reporter_nit", sa.String(30)),
        sa.Column("reporter_name", sa.String(200)),
        sa.Column("reporter_company_role", sa.String(300)),
        sa.Column("city", sa.String(80)),
        sa.Column("registered_by_user_id", _UUID),
        sa.Column("registered_by_name", sa.String(200)),
        sa.Column("registered_by_position", sa.String(200)),
        sa.Column("registered_on", sa.Date()),
        sa.Column("unmet_requirement", sa.Text()),
        sa.Column("finding", sa.Text()),
        sa.Column("has_photos", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("has_history", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("analysis_method", sa.String(20)),
        sa.Column("effectiveness_note", sa.Text()),
        sa.Column("cause_label", sa.String(200)),
        sa.Column("case_id", _UUID, sa.ForeignKey("pqrsf_cases.id", ondelete="SET NULL")),
        sa.Column("case_code", sa.String(20)),
        sa.Column(
            "previous_attempt_id", _UUID, sa.ForeignKey("action_plans.id", ondelete="SET NULL")
        ),
        sa.Column("legacy", postgresql.JSONB()),
        sa.Column("created_by", _UUID),
        sa.Column("created_at", _TZ, nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", _TZ, nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("number", "attempt", name="action_plans_number_attempt_key"),
        sa.UniqueConstraint("code", name="action_plans_code_key"),
        sa.CheckConstraint(
            "origin IN ('AI', 'AE', 'AT', 'IN', 'OM', 'PI', 'SNC')", name="ck_action_plans_origin"
        ),
        sa.CheckConstraint(
            "action_type IN ('correctiva', 'preventiva', 'mejora')", name="ck_action_plans_type"
        ),
        sa.CheckConstraint(
            "rating IN ('real', 'potencial', 'oportunidad_mejora')", name="ck_action_plans_rating"
        ),
        sa.CheckConstraint(
            "analysis_method IS NULL OR analysis_method IN "
            "('5m', 'espina_pescado', 'lluvia_ideas', 'sin_fallo')",
            name="ck_action_plans_method",
        ),
        sa.CheckConstraint("attempt > 0", name="ck_action_plans_attempt"),
    )
    op.create_index("ix_action_plans_reported_on", "action_plans", ["reported_on"])
    op.create_index("ix_action_plans_process", "action_plans", ["process_id"])
    op.create_index("ix_action_plans_case", "action_plans", ["case_id"])
    op.create_index("ix_action_plans_reporter_nit", "action_plans", ["reporter_nit"])

    _children(
        "action_plan_corrections",
        [
            sa.Column("description", sa.Text(), nullable=False),
            *_people(),
            sa.Column("done", sa.Boolean(), nullable=False, server_default="false"),
            sa.Column("done_at", _TZ),
        ],
    )
    _children(
        "action_plan_causes",
        [
            sa.Column("category", sa.String(20), nullable=False, server_default="otra"),
            sa.Column("description", sa.Text(), nullable=False),
            sa.CheckConstraint(
                "category IN ('mano_obra', 'metodos', 'maquinaria', 'materiales', "
                "'medio_ambiente', 'medicion', 'otra')",
                name="ck_action_plan_causes_category",
            ),
        ],
    )
    _children(
        "action_plan_activities",
        [
            sa.Column("description", sa.Text(), nullable=False),
            *_people(),
            sa.Column("follow_up_text", sa.Text()),
            sa.Column("follow_up_due_on", sa.Date()),
            sa.Column("done", sa.Boolean(), nullable=False, server_default="false"),
            sa.Column("done_at", _TZ),
            sa.Column("effective", sa.String(10), nullable=False, server_default="pendiente"),
            sa.Column("effectiveness_note", sa.Text()),
            sa.CheckConstraint(
                "effective IN ('si', 'no', 'pendiente')",
                name="ck_action_plan_activities_effective",
            ),
        ],
    )

    op.create_table(
        "action_plan_files",
        sa.Column("id", _UUID, primary_key=True),
        sa.Column(
            "plan_id", _UUID, sa.ForeignKey("action_plans.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("activity_id", _UUID),
        sa.Column("correction_id", _UUID),
        sa.Column("original_filename", sa.String(255), nullable=False),
        sa.Column("content_type", sa.String(120), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("storage_key", sa.String(500), nullable=False),
        sa.Column("uploaded_by", _UUID),
        sa.Column("created_at", _TZ, nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_action_plan_files_plan", "action_plan_files", ["plan_id", "created_at"])

    op.create_table(
        "action_plan_events",
        sa.Column("id", _UUID, primary_key=True),
        sa.Column(
            "plan_id", _UUID, sa.ForeignKey("action_plans.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("event_type", sa.String(40), nullable=False),
        sa.Column("actor_user_id", _UUID),
        sa.Column("note", sa.Text()),
        sa.Column("data", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("created_at", _TZ, nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_action_plan_events_plan", "action_plan_events", ["plan_id", "created_at"])

    op.add_column("pqrsf_dependencies", sa.Column("process_code", sa.String(8)))
    for code, process in _DEPENDENCY_PROCESS.items():
        op.execute(
            sa.text("UPDATE pqrsf_dependencies SET process_code = :p WHERE code = :c").bindparams(
                p=process, c=code
            )
        )


def downgrade() -> None:
    op.drop_column("pqrsf_dependencies", "process_code")
    op.drop_table("action_plan_events")
    op.drop_table("action_plan_files")
    op.drop_table("action_plan_activities")
    op.drop_table("action_plan_causes")
    op.drop_table("action_plan_corrections")
    op.drop_table("action_plans")
    op.drop_table("action_plan_processes")
