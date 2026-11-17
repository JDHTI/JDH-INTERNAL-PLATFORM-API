"""Tables of the action plans (P.04.F.02)."""

from __future__ import annotations

import uuid
from datetime import date, datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.infrastructure.database import Base

_TZ = DateTime(timezone=True)
_UUID = UUID(as_uuid=True)


class ProcessModel(Base):
    """A process of the SIG: the «procedencia» of an action."""

    __tablename__ = "action_plan_processes"

    id: Mapped[uuid.UUID] = mapped_column(_UUID, primary_key=True)
    code: Mapped[str] = mapped_column(String(8), nullable=False, unique=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")


class ActionPlanModel(Base):
    """One action (and one attempt) of the matrix."""

    __tablename__ = "action_plans"
    __table_args__ = (
        UniqueConstraint("number", "attempt", name="action_plans_number_attempt_key"),
        UniqueConstraint("code", name="action_plans_code_key"),
        CheckConstraint(
            "origin IN ('AI', 'AE', 'AT', 'IN', 'OM', 'PI', 'SNC')", name="ck_action_plans_origin"
        ),
        CheckConstraint(
            "action_type IN ('correctiva', 'preventiva', 'mejora')", name="ck_action_plans_type"
        ),
        CheckConstraint(
            "rating IN ('real', 'potencial', 'oportunidad_mejora')", name="ck_action_plans_rating"
        ),
        CheckConstraint(
            "analysis_method IS NULL OR analysis_method IN "
            "('5m', 'espina_pescado', 'lluvia_ideas', 'sin_fallo')",
            name="ck_action_plans_method",
        ),
        CheckConstraint("attempt > 0", name="ck_action_plans_attempt"),
        Index("ix_action_plans_reported_on", "reported_on"),
        Index("ix_action_plans_process", "process_id"),
        Index("ix_action_plans_case", "case_id"),
        Index("ix_action_plans_reporter_nit", "reporter_nit"),
    )

    id: Mapped[uuid.UUID] = mapped_column(_UUID, primary_key=True)
    number: Mapped[int] = mapped_column(Integer, nullable=False)
    attempt: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")
    code: Mapped[str] = mapped_column(String(60), nullable=False)
    origin: Mapped[str] = mapped_column(String(4), nullable=False)
    process_id: Mapped[uuid.UUID] = mapped_column(
        _UUID, ForeignKey("action_plan_processes.id", ondelete="RESTRICT"), nullable=False
    )
    action_type: Mapped[str] = mapped_column(String(12), nullable=False)
    rating: Mapped[str] = mapped_column(String(20), nullable=False)
    reported_on: Mapped[date] = mapped_column(Date, nullable=False)
    reporter_nit: Mapped[str | None] = mapped_column(String(30))
    reporter_name: Mapped[str | None] = mapped_column(String(200))
    reporter_company_role: Mapped[str | None] = mapped_column(String(300))
    city: Mapped[str | None] = mapped_column(String(80))
    registered_by_user_id: Mapped[uuid.UUID | None] = mapped_column(_UUID)
    registered_by_name: Mapped[str | None] = mapped_column(String(200))
    registered_by_position: Mapped[str | None] = mapped_column(String(200))
    registered_on: Mapped[date | None] = mapped_column(Date)
    unmet_requirement: Mapped[str | None] = mapped_column(Text)
    finding: Mapped[str | None] = mapped_column(Text)
    has_photos: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    has_history: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    analysis_method: Mapped[str | None] = mapped_column(String(20))
    effectiveness_note: Mapped[str | None] = mapped_column(Text)
    # The cause of the PQRSF case, to find earlier actions of the same customer and cause.
    cause_label: Mapped[str | None] = mapped_column(String(200))
    case_id: Mapped[uuid.UUID | None] = mapped_column(
        _UUID, ForeignKey("pqrsf_cases.id", ondelete="SET NULL")
    )
    case_code: Mapped[str | None] = mapped_column(String(20))
    previous_attempt_id: Mapped[uuid.UUID | None] = mapped_column(
        _UUID, ForeignKey("action_plans.id", ondelete="SET NULL")
    )
    legacy: Mapped[dict | None] = mapped_column(JSONB)
    created_by: Mapped[uuid.UUID | None] = mapped_column(_UUID)
    created_at: Mapped[datetime] = mapped_column(_TZ, server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        _TZ, server_default=func.now(), onupdate=func.now(), nullable=False
    )


class CorrectionModel(Base):
    """An immediate correction (Excel U–X)."""

    __tablename__ = "action_plan_corrections"
    __table_args__ = (Index("ix_action_plan_corrections_plan", "plan_id", "sort_order"),)

    id: Mapped[uuid.UUID] = mapped_column(_UUID, primary_key=True)
    plan_id: Mapped[uuid.UUID] = mapped_column(
        _UUID, ForeignKey("action_plans.id", ondelete="CASCADE"), nullable=False
    )
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    description: Mapped[str] = mapped_column(Text, nullable=False)
    responsible_text: Mapped[str | None] = mapped_column(Text)
    responsible_user_ids: Mapped[list[uuid.UUID]] = mapped_column(
        ARRAY(_UUID), nullable=False, server_default="{}"
    )
    due_on: Mapped[date | None] = mapped_column(Date)
    evidence_note: Mapped[str | None] = mapped_column(Text)
    done: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    done_at: Mapped[datetime | None] = mapped_column(_TZ)


class CauseModel(Base):
    """A cause found with the analysis method (Excel Y–Z)."""

    __tablename__ = "action_plan_causes"
    __table_args__ = (
        Index("ix_action_plan_causes_plan", "plan_id", "sort_order"),
        CheckConstraint(
            "category IN ('mano_obra', 'metodos', 'maquinaria', 'materiales', "
            "'medio_ambiente', 'medicion', 'otra')",
            name="ck_action_plan_causes_category",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(_UUID, primary_key=True)
    plan_id: Mapped[uuid.UUID] = mapped_column(
        _UUID, ForeignKey("action_plans.id", ondelete="CASCADE"), nullable=False
    )
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    category: Mapped[str] = mapped_column(String(20), nullable=False, server_default="otra")
    description: Mapped[str] = mapped_column(Text, nullable=False)


class ActivityModel(Base):
    """A planned activity and its follow-up (Excel AA–AJ)."""

    __tablename__ = "action_plan_activities"
    __table_args__ = (
        Index("ix_action_plan_activities_plan", "plan_id", "sort_order"),
        CheckConstraint(
            "effective IN ('si', 'no', 'pendiente')", name="ck_action_plan_activities_effective"
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(_UUID, primary_key=True)
    plan_id: Mapped[uuid.UUID] = mapped_column(
        _UUID, ForeignKey("action_plans.id", ondelete="CASCADE"), nullable=False
    )
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    description: Mapped[str] = mapped_column(Text, nullable=False)
    responsible_text: Mapped[str | None] = mapped_column(Text)
    responsible_user_ids: Mapped[list[uuid.UUID]] = mapped_column(
        ARRAY(_UUID), nullable=False, server_default="{}"
    )
    due_on: Mapped[date | None] = mapped_column(Date)
    evidence_note: Mapped[str | None] = mapped_column(Text)
    follow_up_text: Mapped[str | None] = mapped_column(Text)
    follow_up_due_on: Mapped[date | None] = mapped_column(Date)
    done: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    done_at: Mapped[datetime | None] = mapped_column(_TZ)
    effective: Mapped[str] = mapped_column(String(10), nullable=False, server_default="pendiente")
    effectiveness_note: Mapped[str | None] = mapped_column(Text)


class PlanFileModel(Base):
    """Evidence of an action, kept in the bucket."""

    __tablename__ = "action_plan_files"
    __table_args__ = (Index("ix_action_plan_files_plan", "plan_id", "created_at"),)

    id: Mapped[uuid.UUID] = mapped_column(_UUID, primary_key=True)
    plan_id: Mapped[uuid.UUID] = mapped_column(
        _UUID, ForeignKey("action_plans.id", ondelete="CASCADE"), nullable=False
    )
    activity_id: Mapped[uuid.UUID | None] = mapped_column(_UUID)
    correction_id: Mapped[uuid.UUID | None] = mapped_column(_UUID)
    original_filename: Mapped[str] = mapped_column(String(255), nullable=False)
    content_type: Mapped[str] = mapped_column(String(120), nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    storage_key: Mapped[str] = mapped_column(String(500), nullable=False)
    uploaded_by: Mapped[uuid.UUID | None] = mapped_column(_UUID)
    created_at: Mapped[datetime] = mapped_column(_TZ, server_default=func.now(), nullable=False)


class PlanEventModel(Base):
    """The history of an action."""

    __tablename__ = "action_plan_events"
    __table_args__ = (Index("ix_action_plan_events_plan", "plan_id", "created_at"),)

    id: Mapped[uuid.UUID] = mapped_column(_UUID, primary_key=True)
    plan_id: Mapped[uuid.UUID] = mapped_column(
        _UUID, ForeignKey("action_plans.id", ondelete="CASCADE"), nullable=False
    )
    event_type: Mapped[str] = mapped_column(String(40), nullable=False)
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(_UUID)
    note: Mapped[str | None] = mapped_column(Text)
    data: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default="{}")
    created_at: Mapped[datetime] = mapped_column(_TZ, server_default=func.now(), nullable=False)
