"""Load cycles of the warehouse monitoring copy."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from uuid import UUID


class CycleStatus(StrEnum):
    """Outcome of one load cycle."""

    SUCCESS = "success"
    # Some views loaded and others failed; the failed ones keep their last copy.
    PARTIAL = "partial"
    FAILED = "failed"


@dataclass(frozen=True)
class RunFilters:
    """Filters shared by the run, extraction and transformation listings."""

    start: datetime | None = None
    end: datetime | None = None
    secuencia: str | None = None
    estados: Sequence[str] = ()
    dominio: str | None = None
    disparo: str | None = None
    query: str | None = None


@dataclass
class ViewReport:
    """What one view did in a cycle."""

    read: int = 0
    written: int = 0
    error: str | None = None

    def as_dict(self) -> dict[str, object]:
        """Plain form stored with the cycle."""

        return {"leidas": self.read, "escritas": self.written, "error": self.error}


@dataclass
class LoadCycle:
    """One pass over every view of ``monitoreo``."""

    id: UUID
    started_at: datetime
    finished_at: datetime | None = None
    status: CycleStatus | None = None
    error: str | None = None
    views: dict[str, ViewReport] = field(default_factory=dict)

    def close(self, finished_at: datetime) -> None:
        """Stamp the end and derive the status from the views."""

        self.finished_at = finished_at
        if self.error is not None:
            self.status = CycleStatus.FAILED
            return
        failed = [name for name, report in self.views.items() if report.error]
        if not failed:
            self.status = CycleStatus.SUCCESS
        elif len(failed) == len(self.views):
            self.status = CycleStatus.FAILED
            self.error = "Ninguna vista se pudo cargar"
        else:
            self.status = CycleStatus.PARTIAL
            self.error = f"Vistas sin cargar: {', '.join(sorted(failed))}"
