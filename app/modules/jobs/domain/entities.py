"""Runs of the background jobs and the warehouse sequences they wait for."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
from uuid import UUID

from app.modules.jobs.domain.registry import RunStatus, Trigger


@dataclass(frozen=True)
class JobRun:
    """One run of a job."""

    id: UUID
    job: str
    trigger: Trigger
    status: RunStatus
    queued_at: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None
    detail: dict[str, Any] = field(default_factory=dict)
    error: str | None = None
    # The DWH sequence run whose fresh data started it.
    dwh_corrida_id: UUID | None = None
    dwh_datos_listos: datetime | None = None
    triggered_by: str | None = None


@dataclass(frozen=True)
class SequenceState:
    """One row of ``monitoreo.secuencias``, plus what the platform already ran for it."""

    secuencia: str
    ultima_corrida_id: UUID | None = None
    ultima_estado: str | None = None
    ultima_datos_listos: datetime | None = None
    en_curso: bool | None = None
    proxima_ejecucion: datetime | None = None
    eta_datos_listos_proxima: datetime | None = None
    eta_datos_listos_en_curso: datetime | None = None
    # ``ultima_datos_listos`` of the last run whose jobs were queued.
    dispatched_datos_listos: datetime | None = None
    polled_at: datetime | None = None

    @property
    def fresh(self) -> bool:
        """Whether its ``consumo`` data is safe to read: finished well and not reloading."""

        return (
            self.ultima_estado == "success"
            and not self.en_curso
            and self.ultima_datos_listos is not None
        )

    @property
    def advanced(self) -> bool:
        """Whether it finished a run the platform has not dispatched yet."""

        return self.ultima_datos_listos is not None and (
            self.dispatched_datos_listos is None
            or self.ultima_datos_listos > self.dispatched_datos_listos
        )

    @property
    def next_ready_at(self) -> datetime | None:
        """When its data should be ready next (the run in progress, else the next one)."""

        return self.eta_datos_listos_en_curso or self.eta_datos_listos_proxima


@dataclass(frozen=True)
class JobOverview:
    """A job with its latest run and the state of its sequence."""

    key: str
    label: str
    description: str
    sequence: str | None
    fallback_days: list[int]
    last_run: JobRun | None
    last_success_at: datetime | None
    sequence_state: SequenceState | None

    @property
    def next_expected_at(self) -> datetime | None:
        """When the DWH should trigger it next."""

        return self.sequence_state.next_ready_at if self.sequence_state else None
