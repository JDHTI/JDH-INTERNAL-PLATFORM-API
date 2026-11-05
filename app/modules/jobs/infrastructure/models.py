"""SQLAlchemy models owned by the jobs module (migration ``0047_jobs``)."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import Boolean, CheckConstraint, DateTime, Index, String, Text, func, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.infrastructure.database import Base

_TZ = DateTime(timezone=True)


class JobRunModel(Base):
    """One run of a background job."""

    __tablename__ = "job_runs"
    __table_args__ = (
        CheckConstraint(
            "trigger IN ('dwh', 'scheduled', 'manual', 'startup')", name="ck_job_runs_trigger"
        ),
        CheckConstraint(
            "status IN ('queued', 'running', 'success', 'partial', 'failed')",
            name="ck_job_runs_status",
        ),
        Index("ix_job_runs_job_queued_at", "job", "queued_at"),
        # One open run per job: the worker, the fallback and a manual request never overlap.
        Index(
            "uq_job_runs_open",
            "job",
            unique=True,
            postgresql_where=text("status IN ('queued', 'running')"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    job: Mapped[str] = mapped_column(String(60), nullable=False)
    trigger: Mapped[str] = mapped_column(String(10), nullable=False)
    status: Mapped[str] = mapped_column(String(10), nullable=False)
    queued_at: Mapped[datetime] = mapped_column(_TZ, nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(_TZ)
    finished_at: Mapped[datetime | None] = mapped_column(_TZ)
    detail: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    error: Mapped[str | None] = mapped_column(Text)
    dwh_corrida_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    dwh_datos_listos: Mapped[datetime | None] = mapped_column(_TZ)
    triggered_by: Mapped[str | None] = mapped_column(String(320))


class DwhSequenceWatermarkModel(Base):
    """The last state of a warehouse sequence the platform polled, and what it dispatched."""

    __tablename__ = "dwh_sequence_watermarks"

    secuencia: Mapped[str] = mapped_column(Text, primary_key=True)
    ultima_corrida_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    ultima_estado: Mapped[str | None] = mapped_column(Text)
    ultima_datos_listos: Mapped[datetime | None] = mapped_column(_TZ)
    en_curso: Mapped[bool | None] = mapped_column(Boolean)
    proxima_ejecucion: Mapped[datetime | None] = mapped_column(_TZ)
    eta_datos_listos_proxima: Mapped[datetime | None] = mapped_column(_TZ)
    eta_datos_listos_en_curso: Mapped[datetime | None] = mapped_column(_TZ)
    dispatched_datos_listos: Mapped[datetime | None] = mapped_column(_TZ)
    polled_at: Mapped[datetime] = mapped_column(_TZ, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        _TZ, server_default=func.now(), onupdate=func.now(), nullable=False
    )
