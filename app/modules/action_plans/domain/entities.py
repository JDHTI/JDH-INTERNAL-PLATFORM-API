"""Action plan (format P.04.F.02 «Seguimiento plan de acción»).

One action has a header (who found what, where and when), immediate corrections, the causes
found with an analysis method, the planned activities with their follow-up, and how
effective they were. A not effective action opens a new attempt (``intento``) with the
same number. The code is the one of the Excel column E: ``ORIGEN-ddmmyy-NÚMERO-PROC-INTENTO``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from uuid import UUID

from app.core.errors import InvalidInputError

ORIGINS = {
    "AI": "Auditoría interna",
    "AE": "Auditoría externa",
    "AT": "Accidente de trabajo",
    "IN": "Inspección",
    "OM": "Oportunidad de mejora",
    "PI": "Partes interesadas",
    "SNC": "Salida no conforme",
}
ACTION_TYPES = {"correctiva": "Correctiva", "preventiva": "Preventiva", "mejora": "Mejora"}
RATINGS = {
    "real": "Real",
    "potencial": "Potencial",
    "oportunidad_mejora": "Oportunidad de mejora",
}
# The rating that goes with each type, as the Excel lists pair them.
DEFAULT_RATING = {"correctiva": "real", "preventiva": "potencial", "mejora": "oportunidad_mejora"}
METHODS = {
    "5m": "Método de las 5M",
    "espina_pescado": "Método de la Espina de Pescado",
    "lluvia_ideas": "Método de Lluvia de Ideas",
    "sin_fallo": "No hay fallo en nuestro proceso / Respuesta formal",
}
CAUSE_CATEGORIES = {
    "mano_obra": "Mano de obra",
    "metodos": "Métodos",
    "maquinaria": "Maquinaria",
    "materiales": "Materia prima",
    "medio_ambiente": "Medio ambiente",
    "medicion": "Medición",
    "otra": "Otra",
}
EFFECTIVENESS = {"si": "Sí", "no": "No", "pendiente": "Pendiente"}

OPEN = "abierta"
IN_PROGRESS = "en_proceso"
CLOSED = "cerrada"
STATUSES = {OPEN: "Abierta", IN_PROGRESS: "En proceso", CLOSED: "Cerrada"}

# What each type admits (the notes of row 5 of the Excel): preventive and improvement
# actions have no immediate correction; an improvement has no cause analysis.
NO_CORRECTIONS = frozenset({"preventiva", "mejora"})
NO_ANALYSIS = frozenset({"mejora"})

MAX_TEXT = 5_000
MAX_CHILDREN = 50


@dataclass
class Process:
    """A process of the SIG (the «procedencia» of an action): ALM, DIS, COM…"""

    id: UUID
    code: str
    name: str
    is_active: bool = True


@dataclass
class Correction:
    """An immediate correction (Excel U–X)."""

    id: UUID
    description: str
    responsible_text: str | None = None
    responsible_user_ids: list[UUID] = field(default_factory=list)
    due_on: date | None = None
    evidence_note: str | None = None
    done: bool = False
    done_at: datetime | None = None


@dataclass
class Cause:
    """A cause found with the analysis method (Excel Y–Z)."""

    id: UUID
    description: str
    category: str = "otra"


@dataclass
class Activity:
    """A planned activity and its follow-up (Excel AA–AJ)."""

    id: UUID
    description: str
    responsible_text: str | None = None
    responsible_user_ids: list[UUID] = field(default_factory=list)
    due_on: date | None = None
    evidence_note: str | None = None
    follow_up_text: str | None = None
    follow_up_due_on: date | None = None
    done: bool = False
    done_at: datetime | None = None
    effective: str = "pendiente"
    effectiveness_note: str | None = None

    def is_overdue(self, today: date) -> bool:
        """Not done and past its date."""

        return not self.done and self.due_on is not None and self.due_on < today


@dataclass
class PlanFile:
    """Evidence of an action (an attendance list, a photo), kept in the bucket."""

    id: UUID
    plan_id: UUID
    original_filename: str
    content_type: str
    size_bytes: int
    storage_key: str
    activity_id: UUID | None = None
    correction_id: UUID | None = None
    uploaded_by: UUID | None = None
    created_at: datetime | None = None


@dataclass
class PlanEvent:
    """A step in the history of an action."""

    id: UUID
    plan_id: UUID
    event_type: str
    actor_user_id: UUID | None
    note: str | None
    data: dict
    created_at: datetime


@dataclass
class ActionPlan:
    """One action of the matrix, with its children."""

    id: UUID
    number: int
    attempt: int
    origin: str
    process: Process
    action_type: str
    rating: str
    reported_on: date
    code: str = ""
    reporter_nit: str | None = None
    reporter_name: str | None = None
    reporter_company_role: str | None = None
    city: str | None = None
    registered_by_user_id: UUID | None = None
    registered_by_name: str | None = None
    registered_by_position: str | None = None
    registered_on: date | None = None
    unmet_requirement: str | None = None
    finding: str | None = None
    has_photos: bool = False
    has_history: bool = False
    analysis_method: str | None = None
    effectiveness_note: str | None = None
    # The cause of the PQRSF case, to find earlier actions of the same customer and cause.
    cause_label: str | None = None
    case_id: UUID | None = None
    case_code: str | None = None
    previous_attempt_id: UUID | None = None
    legacy: dict | None = None
    corrections: list[Correction] = field(default_factory=list)
    causes: list[Cause] = field(default_factory=list)
    activities: list[Activity] = field(default_factory=list)
    created_by: UUID | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None

    @property
    def status(self) -> str:
        """Open without activities, closed when every activity was done, else in progress."""

        if not self.activities:
            return OPEN
        return CLOSED if all(a.done for a in self.activities) else IN_PROGRESS

    @property
    def effectiveness(self) -> str:
        """``no`` when one activity was not effective, ``si`` when all were, else pending."""

        values = {a.effective for a in self.activities}
        if "no" in values:
            return "no"
        if values == {"si"}:
            return "si"
        return "pendiente"

    def responsible_ids(self) -> set[UUID]:
        """Every platform user responsible for a correction or an activity."""

        ids: set[UUID] = set()
        for item in [*self.corrections, *self.activities]:
            ids.update(item.responsible_user_ids)
        return ids

    def refresh_code(self) -> None:
        """Recompute the code from the header, as the Excel formula does."""

        self.code = plan_code(
            self.origin, self.reported_on, self.number, self.process.code, self.attempt
        )


def plan_code(origin: str, reported_on: date, number: int, process: str, attempt: int) -> str:
    """``SNC-290126-121-DIS-1``: origin, date (ddmmyy), number, process and attempt."""

    return f"{origin}-{reported_on:%d%m%y}-{number}-{process}-{attempt}"


def clean_choice(value: str | None, options: dict, label: str) -> str:
    """A value of a closed list (by key)."""

    if value not in options:
        raise InvalidInputError(f"{label} no válido")
    return value


def clean_text(value: str | None, label: str, limit: int = MAX_TEXT) -> str | None:
    """Optional free text: stripped, ``None`` when blank, bounded."""

    if value is None:
        return None
    text = value.strip().replace("\r\n", "\n")
    if not text:
        return None
    if len(text) > limit:
        raise InvalidInputError(f"{label} admite máximo {limit} caracteres")
    return text


def required_text(value: str | None, label: str, limit: int = MAX_TEXT) -> str:
    """Free text that must be there."""

    text = clean_text(value, label, limit)
    if text is None:
        raise InvalidInputError(f"{label} no puede estar vacío")
    return text


def check_sections(plan: ActionPlan) -> None:
    """What the type of the action admits.

    Raises:
        InvalidInputError: Corrections on a preventive or improvement action, or an
            analysis on an improvement.
    """

    label = ACTION_TYPES[plan.action_type].lower()
    if plan.action_type in NO_CORRECTIONS and plan.corrections:
        raise InvalidInputError(f"Una acción {label} no lleva corrección inmediata")
    if plan.action_type in NO_ANALYSIS and (plan.causes or plan.analysis_method):
        raise InvalidInputError(f"Una acción {label} no lleva análisis de causas")
    for limit_name, items in (
        ("correcciones", plan.corrections),
        ("causas", plan.causes),
        ("actividades", plan.activities),
    ):
        if len(items) > MAX_CHILDREN:
            raise InvalidInputError(f"Una acción admite máximo {MAX_CHILDREN} {limit_name}")
