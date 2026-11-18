"""Use cases of the action plans (P.04.F.02).

Who does what:
- **Calidad** (``manage`` on ``planes-accion``) creates actions (by hand or from a PQRSF
  case), writes the corrections, causes and plan, rates how effective each activity was and
  opens a new attempt when it was not. ``read`` sees every action.
- **A responsible** (a platform user named on a correction or an activity) sees the actions
  where they are, marks their own rows as done and uploads evidence. Being named rings
  their bell.
"""

from __future__ import annotations

import logging
from collections import Counter
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, date, datetime
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

from app.core.errors import ConflictError, ForbiddenError, InvalidInputError, NotFoundError
from app.modules.action_plans.domain.entities import (
    ACTION_TYPES,
    CAUSE_CATEGORIES,
    DEFAULT_RATING,
    EFFECTIVENESS,
    METHODS,
    ORIGINS,
    RATINGS,
    ActionPlan,
    Activity,
    Cause,
    Correction,
    PlanEvent,
    PlanFile,
    Process,
    check_sections,
    clean_choice,
    clean_text,
    required_text,
)
from app.modules.action_plans.domain.ports import (
    ActionPlanRepository,
    Bell,
    CaseSource,
    FileGuard,
    ModulePolicy,
    PlanFilters,
    Profiles,
    Storage,
)
from app.modules.identity.domain.principal import Principal

logger = logging.getLogger(__name__)

MAX_PAGE = 100
MAX_FILES_PER_UPLOAD = 10
_LOCAL = ZoneInfo("America/Bogota")
_HEADER_FIELDS = frozenset(
    {
        "origin",
        "process_id",
        "action_type",
        "rating",
        "reported_on",
        "reporter_nit",
        "reporter_name",
        "reporter_company_role",
        "city",
        "registered_on",
        "unmet_requirement",
        "finding",
        "has_photos",
        "has_history",
        "analysis_method",
        "effectiveness_note",
    }
)
_LIMITS = {
    "reporter_nit": 30,
    "reporter_name": 200,
    "reporter_company_role": 300,
    "city": 80,
}


@dataclass(frozen=True)
class PlanChanges:
    """Header fields to change; ``fields`` says which were sent (``None`` clears)."""

    fields: frozenset[str]
    values: dict


@dataclass(frozen=True)
class CorrectionInput:
    """An immediate correction as the form sends it; ``id`` keeps an existing row."""

    description: str
    id: UUID | None = None
    responsible_text: str | None = None
    responsible_user_ids: tuple[UUID, ...] = ()
    due_on: date | None = None
    evidence_note: str | None = None


@dataclass(frozen=True)
class CauseInput:
    """A cause; ``id`` keeps an existing row."""

    description: str
    category: str = "otra"
    id: UUID | None = None


@dataclass(frozen=True)
class ActivityInput:
    """A planned activity; ``id`` keeps an existing row (and its progress)."""

    description: str
    id: UUID | None = None
    responsible_text: str | None = None
    responsible_user_ids: tuple[UUID, ...] = ()
    due_on: date | None = None
    evidence_note: str | None = None
    follow_up_text: str | None = None
    follow_up_due_on: date | None = None


@dataclass(frozen=True)
class UploadedFile:
    """A file as received."""

    filename: str
    content: bytes


@dataclass(frozen=True)
class PlanAccess:
    """What the caller may do with one action."""

    can_view: bool
    can_manage: bool
    is_responsible: bool


@dataclass(frozen=True)
class PlanDetail:
    """An action with its evidence, history and earlier actions of the same customer."""

    plan: ActionPlan
    access: PlanAccess
    files: list[PlanFile]
    events: list[PlanEvent]
    similar: int
    # Name of each responsible user of the corrections and activities.
    people: dict[UUID, str] = field(default_factory=dict)


@dataclass(frozen=True)
class PlanPage:
    """A page of the matrix."""

    items: list[ActionPlan]
    total: int
    page: int
    size: int


@dataclass(frozen=True)
class Indicators:
    """The «Indicador» sheet: actions and activities by type, status and effectiveness."""

    actions: int
    activities: int
    by_type: dict[str, int]
    by_status: dict[str, int]
    by_effectiveness: dict[str, int]
    by_origin: dict[str, int]
    by_process: dict[str, int]
    by_month: dict[str, int]
    by_city: dict[str, int]
    overdue_activities: int
    activities_done: int
    activities_effective: dict[str, int] = field(default_factory=dict)


class ActionPlanService:
    """The matrix of action plans."""

    def __init__(
        self,
        repository: ActionPlanRepository,
        policy: ModulePolicy,
        cases: CaseSource,
        profiles: Profiles,
        storage: Storage,
        guard: FileGuard,
        bell: Bell | None = None,
        area_slug: str = "calidad",
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        """Create the service.

        Args:
            repository (ActionPlanRepository): Actions, processes, files and history.
            policy (ModulePolicy): Who reads and manages ``planes-accion``.
            cases (CaseSource): The PQRSF case an action comes from.
            profiles (Profiles): Name and position of who registers an action.
            storage (Storage): The bucket the evidence goes to.
            guard (FileGuard): Upload rules (extensions, sizes, antivirus).
            bell (Bell | None): Tells the responsibles they were named.
            area_slug (str): Area of the module, for links in the bell.
            clock (Callable[[], datetime]): Current instant (replaced in tests).
        """

        self.repository = repository
        self.policy = policy
        self.cases = cases
        self.profiles = profiles
        self.storage = storage
        self.guard = guard
        self.bell = bell
        self.area_slug = area_slug
        self.clock = clock

    # --- Access ---

    async def permissions(self, principal: Principal) -> tuple[bool, bool]:
        """``(can_read, can_manage)`` on the module, for the UI."""

        manage = await self.policy.is_manager(principal)
        return manage or await self.policy.can_read(principal), manage

    async def _access(self, plan: ActionPlan, principal: Principal) -> PlanAccess:
        read, manage = await self.permissions(principal)
        responsible = principal.user_id in plan.responsible_ids()
        return PlanAccess(read or responsible, manage, responsible)

    async def _require_manager(self, principal: Principal) -> None:
        if not await self.policy.is_manager(principal):
            raise ForbiddenError("Solo Calidad gestiona los planes de acción")

    async def _load(self, plan_id: UUID, principal: Principal) -> tuple[ActionPlan, PlanAccess]:
        plan = await self.repository.get(plan_id)
        if plan is None:
            raise NotFoundError("Acción no encontrada")
        access = await self._access(plan, principal)
        if not access.can_view:
            raise ForbiddenError("No tienes acceso a esta acción")
        return plan, access

    def _today(self) -> date:
        return self.clock().astimezone(_LOCAL).date()

    def today(self) -> date:
        """Today in Colombia, for overdue marks."""

        return self._today()

    def _event(
        self, plan_id: UUID, event_type: str, actor: UUID | None, note=None, data=None
    ) -> PlanEvent:
        return PlanEvent(uuid4(), plan_id, event_type, actor, note, data or {}, self.clock())

    # --- Catalog ---

    async def processes(self, include_inactive: bool = False) -> list[Process]:
        """The processes of the SIG (the «procedencia»)."""

        return await self.repository.list_processes(include_inactive)

    async def save_process(
        self,
        process_id: UUID | None,
        code: str | None,
        name: str | None,
        is_active: bool | None,
        principal: Principal,
    ) -> Process:
        """Create or rename a process (managers); the code is fixed once created."""

        await self._require_manager(principal)
        if process_id is None:
            clean_code = (code or "").strip().upper()
            if not (2 <= len(clean_code) <= 8 and clean_code.isalpha()):
                raise InvalidInputError("El código del proceso son de 2 a 8 letras")
            process = Process(uuid4(), clean_code, required_text(name, "El nombre", 120))
        else:
            found = await self.repository.get_process(process_id)
            if found is None:
                raise NotFoundError("Proceso no encontrado")
            process = found
            if name is not None:
                process.name = required_text(name, "El nombre", 120)
        if is_active is not None:
            process.is_active = is_active
        return await self.repository.save_process(process)

    # --- Reading ---

    async def list_plans(
        self, filters: PlanFilters, page: int, size: int, principal: Principal
    ) -> PlanPage:
        """A page of the matrix; a responsible without ``read`` sees only their actions."""

        if page < 1 or not 1 <= size <= MAX_PAGE:
            raise InvalidInputError(f"Página desde 1 y tamaño entre 1 y {MAX_PAGE}")
        scoped = await self._scoped(filters, principal)
        items, total = await self.repository.list_plans(scoped, (page - 1) * size, size)
        return PlanPage(items, total, page, size)

    async def _scoped(self, filters: PlanFilters, principal: Principal) -> PlanFilters:
        read, _ = await self.permissions(principal)
        return filters if read else replace(filters, visible_to=principal.user_id)

    async def get_plan(self, plan_id: UUID, principal: Principal) -> PlanDetail:
        """An action with its evidence and history."""

        plan, access = await self._load(plan_id, principal)
        similar = (
            await self.repository.similar(plan.reporter_nit, plan.cause_label, plan.id)
            if plan.reporter_nit
            else 0
        )
        return PlanDetail(
            plan,
            access,
            await self.repository.list_files(plan.id),
            await self.repository.list_events(plan.id),
            similar,
            await self.profiles.names(sorted(plan.responsible_ids(), key=str)),
        )

    async def export_plans(self, filters: PlanFilters, principal: Principal) -> list[ActionPlan]:
        """Every action of the filters, for the Excel (who reads the module)."""

        read, _ = await self.permissions(principal)
        if not read:
            raise ForbiddenError("Solo quien consulta los planes de acción puede exportarlos")
        return await self.repository.all_plans(filters)

    async def indicators(self, filters: PlanFilters, principal: Principal) -> Indicators:
        """The indicators of the «Indicador» sheet, for the filters."""

        plans = await self.repository.all_plans(await self._scoped(filters, principal))
        today = self._today()
        activities = [a for p in plans for a in p.activities]
        by_effectiveness = Counter(a.effective for a in activities)
        return Indicators(
            actions=len(plans),
            activities=len(activities),
            by_type=dict(Counter(p.action_type for p in plans)),
            by_status=dict(Counter(p.status for p in plans)),
            by_effectiveness=dict(Counter(p.effectiveness for p in plans)),
            by_origin=dict(Counter(p.origin for p in plans)),
            by_process=dict(Counter(p.process.code for p in plans)),
            by_month=dict(Counter(p.reported_on.strftime("%Y-%m") for p in plans)),
            by_city=dict(Counter((p.city or "Sin ciudad") for p in plans)),
            overdue_activities=sum(1 for a in activities if a.is_overdue(today)),
            activities_done=sum(1 for a in activities if a.done),
            activities_effective=dict(by_effectiveness),
        )

    # --- Creating ---

    async def create_plan(self, changes: PlanChanges, principal: Principal) -> ActionPlan:
        """A new action from the form (managers)."""

        await self._require_manager(principal)
        values = changes.values
        process = await self._process(values.get("process_id"))
        action_type = clean_choice(values.get("action_type"), ACTION_TYPES, "Tipo de acción")
        signer = await self.profiles.signer(principal.user_id)
        reported_on = values.get("reported_on") or self._today()
        plan = ActionPlan(
            id=uuid4(),
            number=await self.repository.next_number(),
            attempt=1,
            origin=clean_choice(values.get("origin"), ORIGINS, "Origen"),
            process=process,
            action_type=action_type,
            rating=clean_choice(
                values.get("rating") or DEFAULT_RATING[action_type], RATINGS, "Calificación"
            ),
            reported_on=reported_on,
            registered_by_user_id=principal.user_id,
            registered_by_name=getattr(signer, "name", None) or principal.email,
            registered_by_position=getattr(signer, "position", None),
            registered_on=self._today(),
            created_by=principal.user_id,
        )
        self._apply(plan, changes.fields - {"process_id", "origin", "action_type"}, values)
        return await self._insert(plan, principal, "created")

    async def create_from_case(self, case_id: UUID, principal: Principal) -> ActionPlan:
        """A corrective action already filled from a PQRSF case (managers).

        Origin SNC, the process of the case dependency, the customer, the city of the site,
        the finding (subject and description), whether there is photographic evidence,
        earlier actions of the same customer and cause, and the immediate corrections
        already done (pickup order approved, improvement opportunity sent).
        """

        await self._require_manager(principal)
        facts = await self.cases.facts(case_id, principal)
        process = None
        if facts.process_code:
            process = await self.repository.get_process_by_code(facts.process_code)
        if process is None:
            process = await self.repository.get_process_by_code("CAL")
        if process is None:
            raise ConflictError("Falta el proceso CAL en el catálogo de procedencias")
        signer = await self.profiles.signer(principal.user_id)
        finding = "\n\n".join(text for text in (facts.subject, facts.description) if text)
        corrections = []
        if facts.pickup_order:
            corrections.append(
                Correction(uuid4(), f"Se aprobó la orden de recogida {facts.pickup_order}")
            )
        for code in facts.om_codes:
            corrections.append(
                Correction(uuid4(), f"Se envió la oportunidad de mejora {code} al proveedor")
            )
        plan = ActionPlan(
            id=uuid4(),
            number=await self.repository.next_number(),
            attempt=1,
            origin="SNC",
            process=process,
            action_type="correctiva",
            rating="real",
            reported_on=facts.received_on,
            reporter_nit=facts.customer_nit,
            reporter_name=facts.requester_name,
            reporter_company_role=facts.customer_name,
            city=facts.city,
            registered_by_user_id=principal.user_id,
            registered_by_name=getattr(signer, "name", None) or principal.email,
            registered_by_position=getattr(signer, "position", None),
            registered_on=self._today(),
            finding=finding or None,
            has_photos=facts.has_photos,
            cause_label=facts.cause_label,
            case_id=facts.id,
            case_code=facts.code,
            corrections=corrections,
            created_by=principal.user_id,
        )
        if facts.customer_nit:
            plan.has_history = (
                await self.repository.similar(facts.customer_nit, facts.cause_label, plan.id) > 0
            )
        stored = await self._insert(plan, principal, "created", {"case_code": facts.code})
        try:
            await self.cases.link(facts.id, principal, stored.id, stored.code)
        except Exception:
            logger.exception("Could not link action %s to case %s", stored.code, facts.code)
        return stored

    async def next_attempt(self, plan_id: UUID, principal: Principal) -> ActionPlan:
        """A new attempt of an action that was not effective (managers).

        Same number, the attempt plus one, the same header, a link to the previous attempt
        and «antecedentes» set; the corrections, causes and plan start empty.
        """

        await self._require_manager(principal)
        plan, _ = await self._load(plan_id, principal)
        if plan.effectiveness != "no":
            raise ConflictError("Solo una acción calificada como no eficaz abre un nuevo intento")
        attempt = replace(
            plan,
            id=uuid4(),
            attempt=plan.attempt + 1,
            reported_on=self._today(),
            registered_on=self._today(),
            has_history=True,
            previous_attempt_id=plan.id,
            effectiveness_note=None,
            corrections=[],
            causes=[],
            activities=[],
            legacy=None,
            created_by=principal.user_id,
        )
        return await self._insert(attempt, principal, "attempt_opened", {"previous": plan.code})

    async def _insert(
        self, plan: ActionPlan, principal: Principal, event_type: str, data: dict | None = None
    ) -> ActionPlan:
        check_sections(plan)
        for _ in range(3):
            plan.refresh_code()
            try:
                stored = await self.repository.create(
                    plan,
                    self._event(plan.id, event_type, principal.user_id, data=data),
                )
            except ConflictError:
                # Two actions took the same number at once: take the next one.
                if plan.attempt != 1:
                    raise
                plan.number = await self.repository.next_number()
                continue
            await self._ring(stored, set(), principal)
            return stored
        raise ConflictError("No se pudo asignar el número de la acción; intenta de nuevo")

    # --- Editing (managers) ---

    async def update_plan(
        self, plan_id: UUID, changes: PlanChanges, principal: Principal
    ) -> ActionPlan:
        """Change the header, the analysis method or the effectiveness note."""

        await self._require_manager(principal)
        plan, _ = await self._load(plan_id, principal)
        unknown = changes.fields - _HEADER_FIELDS
        if unknown:
            raise InvalidInputError(f"Campos no editables: {', '.join(sorted(unknown))}")
        if "process_id" in changes.fields:
            plan.process = await self._process(changes.values.get("process_id"))
        if "origin" in changes.fields:
            plan.origin = clean_choice(changes.values.get("origin"), ORIGINS, "Origen")
        if "action_type" in changes.fields:
            plan.action_type = clean_choice(
                changes.values.get("action_type"), ACTION_TYPES, "Tipo de acción"
            )
        self._apply(plan, changes.fields - {"process_id", "origin", "action_type"}, changes.values)
        check_sections(plan)
        plan.refresh_code()
        return await self.repository.save(
            plan,
            [
                self._event(
                    plan.id, "updated", principal.user_id, data={"fields": sorted(changes.fields)}
                )
            ],
        )

    async def replace_corrections(
        self, plan_id: UUID, items: Sequence[CorrectionInput], principal: Principal
    ) -> ActionPlan:
        """The immediate corrections, as the form leaves them (progress is kept by id)."""

        await self._require_manager(principal)
        plan, _ = await self._load(plan_id, principal)
        before = plan.responsible_ids()
        current = {c.id: c for c in plan.corrections}
        plan.corrections = [
            Correction(
                id=item.id if item.id in current else uuid4(),
                description=required_text(item.description, "La corrección", 2_000),
                responsible_text=clean_text(item.responsible_text, "Los responsables", 1_000),
                responsible_user_ids=list(dict.fromkeys(item.responsible_user_ids)),
                due_on=item.due_on,
                evidence_note=clean_text(item.evidence_note, "La evidencia", 1_000),
                done=current[item.id].done if item.id in current else False,
                done_at=current[item.id].done_at if item.id in current else None,
            )
            for item in items
        ]
        return await self._save_rows(plan, before, "corrections", principal)

    async def replace_causes(
        self, plan_id: UUID, items: Sequence[CauseInput], principal: Principal
    ) -> ActionPlan:
        """The causes found with the analysis method."""

        await self._require_manager(principal)
        plan, _ = await self._load(plan_id, principal)
        current = {c.id for c in plan.causes}
        plan.causes = [
            Cause(
                id=item.id if item.id in current else uuid4(),
                description=required_text(item.description, "La causa", 2_000),
                category=clean_choice(item.category, CAUSE_CATEGORIES, "Categoría de la causa"),
            )
            for item in items
        ]
        return await self._save_rows(plan, plan.responsible_ids(), "causes", principal)

    async def replace_activities(
        self, plan_id: UUID, items: Sequence[ActivityInput], principal: Principal
    ) -> ActionPlan:
        """The plan of activities (progress and rating are kept by id)."""

        await self._require_manager(principal)
        plan, _ = await self._load(plan_id, principal)
        before = plan.responsible_ids()
        current = {a.id: a for a in plan.activities}
        activities = []
        for item in items:
            kept = current.get(item.id) if item.id else None
            activities.append(
                Activity(
                    id=kept.id if kept else uuid4(),
                    description=required_text(item.description, "La actividad", 2_000),
                    responsible_text=clean_text(item.responsible_text, "Los responsables", 1_000),
                    responsible_user_ids=list(dict.fromkeys(item.responsible_user_ids)),
                    due_on=item.due_on,
                    evidence_note=clean_text(item.evidence_note, "La evidencia", 1_000),
                    follow_up_text=clean_text(item.follow_up_text, "El seguimiento", 1_000),
                    follow_up_due_on=item.follow_up_due_on,
                    done=kept.done if kept else False,
                    done_at=kept.done_at if kept else None,
                    effective=kept.effective if kept else "pendiente",
                    effectiveness_note=kept.effectiveness_note if kept else None,
                )
            )
        plan.activities = activities
        return await self._save_rows(plan, before, "activities", principal)

    async def _save_rows(
        self, plan: ActionPlan, before: set[UUID], section: str, principal: Principal
    ) -> ActionPlan:
        check_sections(plan)
        stored = await self.repository.save(
            plan,
            [self._event(plan.id, "rows_updated", principal.user_id, data={"section": section})],
        )
        await self._ring(stored, before, principal)
        return stored

    # --- Progress ---

    async def mark_done(
        self,
        plan_id: UUID,
        row_id: UUID,
        done: bool,
        evidence_note: str | None,
        principal: Principal,
    ) -> ActionPlan:
        """Mark a correction or an activity as done (or not): its responsible or Calidad."""

        plan, access = await self._load(plan_id, principal)
        row = next((r for r in [*plan.corrections, *plan.activities] if r.id == row_id), None)
        if row is None:
            raise NotFoundError("Fila no encontrada en la acción")
        if not access.can_manage and principal.user_id not in row.responsible_user_ids:
            raise ForbiddenError("Solo su responsable o Calidad marca esta actividad")
        row.done = done
        row.done_at = self.clock() if done else None
        if evidence_note is not None:
            row.evidence_note = clean_text(evidence_note, "La evidencia", 1_000)
        return await self.repository.save(
            plan,
            [
                self._event(
                    plan.id,
                    "row_done" if done else "row_reopened",
                    principal.user_id,
                    data={"row_id": str(row.id), "description": row.description[:200]},
                )
            ],
        )

    async def rate_activity(
        self,
        plan_id: UUID,
        activity_id: UUID,
        effective: str,
        note: str | None,
        principal: Principal,
    ) -> ActionPlan:
        """Rate how effective a done activity was (Calidad)."""

        await self._require_manager(principal)
        plan, _ = await self._load(plan_id, principal)
        activity = next((a for a in plan.activities if a.id == activity_id), None)
        if activity is None:
            raise NotFoundError("Actividad no encontrada en la acción")
        value = clean_choice(effective, EFFECTIVENESS, "Eficacia")
        if value != "pendiente" and not activity.done:
            raise ConflictError("La eficacia se califica cuando la actividad está realizada")
        activity.effective = value
        activity.effectiveness_note = clean_text(note, "La descripción de la eficacia", 2_000)
        return await self.repository.save(
            plan,
            [
                self._event(
                    plan.id,
                    "rated",
                    principal.user_id,
                    note=activity.effectiveness_note,
                    data={"activity_id": str(activity.id), "effective": value},
                )
            ],
        )

    async def delete_plan(self, plan_id: UUID, principal: Principal) -> None:
        """Delete an action and its evidence (managers)."""

        await self._require_manager(principal)
        await self._load(plan_id, principal)
        for key in await self.repository.delete(plan_id):
            try:
                await self.storage.delete_object(key)
            except Exception:
                logger.warning("Could not remove evidence %s", key)

    # --- Evidence ---

    async def add_files(
        self,
        plan_id: UUID,
        files: Sequence[UploadedFile],
        principal: Principal,
        row_id: UUID | None = None,
    ) -> tuple[list[PlanFile], list[dict]]:
        """Upload evidence to an action (or one of its rows): Calidad or a responsible."""

        plan, access = await self._load(plan_id, principal)
        if not access.can_manage and not access.is_responsible:
            raise ForbiddenError("Solo Calidad o un responsable sube evidencias")
        if not files:
            raise InvalidInputError("Adjunta al menos un archivo")
        if len(files) > MAX_FILES_PER_UPLOAD:
            raise InvalidInputError(f"Máximo {MAX_FILES_PER_UPLOAD} archivos por carga")
        activity_id = correction_id = None
        if row_id is not None:
            if any(a.id == row_id for a in plan.activities):
                activity_id = row_id
            elif any(c.id == row_id for c in plan.corrections):
                correction_id = row_id
            else:
                raise NotFoundError("Fila no encontrada en la acción")
        stored: list[PlanFile] = []
        rejected: list[dict] = []
        total = 0
        for upload in files:
            reason = self.guard.refuse(upload.filename, len(upload.content), len(stored), total)
            if reason:
                rejected.append({"name": upload.filename, "reason": reason})
                continue
            try:
                name, extension, content_type, data = await self.guard.clean(
                    upload.filename, upload.content
                )
            except InvalidInputError as exc:
                rejected.append({"name": upload.filename, "reason": str(exc)})
                continue
            file_id = uuid4()
            key = f"action-plans/{plan.id}/{file_id}.{extension}"
            await self.storage.put_object(key, data, content_type)
            record = PlanFile(
                id=file_id,
                plan_id=plan.id,
                original_filename=name,
                content_type=content_type,
                size_bytes=len(data),
                storage_key=key,
                activity_id=activity_id,
                correction_id=correction_id,
                uploaded_by=principal.user_id,
                created_at=self.clock(),
            )
            await self.repository.add_file(record)
            stored.append(record)
            total += len(data)
        if stored:
            await self.repository.add_event(
                self._event(
                    plan.id,
                    "files_added",
                    principal.user_id,
                    data={"files": [f.original_filename for f in stored]},
                )
            )
        return stored, rejected

    async def file_content(
        self, plan_id: UUID, file_id: UUID, principal: Principal
    ) -> tuple[PlanFile, bytes]:
        """An evidence file, for whoever sees the action."""

        await self._load(plan_id, principal)
        found = await self.repository.get_file(plan_id, file_id)
        if found is None:
            raise NotFoundError("Evidencia no encontrada")
        return found, await self.storage.get_object(found.storage_key)

    async def delete_file(self, plan_id: UUID, file_id: UUID, principal: Principal) -> None:
        """Remove an evidence file (Calidad, or who uploaded it)."""

        _, access = await self._load(plan_id, principal)
        found = await self.repository.get_file(plan_id, file_id)
        if found is None:
            raise NotFoundError("Evidencia no encontrada")
        if not access.can_manage and found.uploaded_by != principal.user_id:
            raise ForbiddenError("Solo Calidad o quien la subió quita una evidencia")
        await self.repository.delete_file(file_id)
        try:
            await self.storage.delete_object(found.storage_key)
        except Exception:
            logger.warning("Could not remove evidence %s", found.storage_key)

    # --- Helpers ---

    async def _process(self, process_id) -> Process:
        if not process_id:
            raise InvalidInputError("Indica la procedencia (proceso) de la acción")
        process = await self.repository.get_process(process_id)
        if process is None or not process.is_active:
            raise NotFoundError("Proceso no encontrado")
        return process

    def _apply(self, plan: ActionPlan, fields: frozenset[str], values: dict) -> None:
        """Copy the free header fields from a form into the action."""

        for name in fields:
            value = values.get(name)
            if name == "rating":
                plan.rating = clean_choice(value, RATINGS, "Calificación")
            elif name == "analysis_method":
                plan.analysis_method = (
                    clean_choice(value, METHODS, "Método de análisis") if value else None
                )
            elif name in ("has_photos", "has_history"):
                setattr(plan, name, bool(value))
            elif name in ("reported_on", "registered_on"):
                if name == "reported_on" and value is None:
                    raise InvalidInputError("Indica la fecha de la acción")
                if value is not None and value > self._today():
                    raise InvalidInputError("La fecha no puede ser futura")
                setattr(plan, name, value)
            elif name in _LIMITS:
                setattr(plan, name, clean_text(value, name, _LIMITS[name]))
            elif name in ("unmet_requirement", "finding", "effectiveness_note"):
                setattr(plan, name, clean_text(value, name))

    async def _ring(self, plan: ActionPlan, before: set[UUID], principal: Principal) -> None:
        """Tell the newly named responsibles (never the caller) through the bell."""

        if self.bell is None:
            return
        named = plan.responsible_ids() - before - {principal.user_id}
        if not named:
            return
        try:
            await self.bell.ring(
                sorted(named, key=str),
                f"action-plan:{plan.id}:{self.clock().isoformat()}",
                f"Plan de acción {plan.code}",
                "Te asignaron actividades en un plan de acción",
                f"/{self.area_slug}/planes-accion/{plan.id}",
            )
        except Exception:
            logger.warning("Could not ring the bell for action %s", plan.code, exc_info=True)
