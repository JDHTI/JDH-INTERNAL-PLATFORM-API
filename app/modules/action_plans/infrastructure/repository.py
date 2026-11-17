"""PostgreSQL storage of the action plans: actions with their rows, processes, files, history."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence
from uuid import UUID

from sqlalchemy import and_, delete, exists, extract, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ConflictError
from app.infrastructure.database import Database
from app.modules.action_plans.domain.entities import (
    CLOSED,
    IN_PROGRESS,
    OPEN,
    ActionPlan,
    Activity,
    Cause,
    Correction,
    PlanEvent,
    PlanFile,
    Process,
)
from app.modules.action_plans.domain.ports import PlanFilters
from app.modules.action_plans.infrastructure.models import (
    ActionPlanModel,
    ActivityModel,
    CauseModel,
    CorrectionModel,
    PlanEventModel,
    PlanFileModel,
    ProcessModel,
)

_HEADER = (
    "number",
    "attempt",
    "code",
    "origin",
    "action_type",
    "rating",
    "reported_on",
    "reporter_nit",
    "reporter_name",
    "reporter_company_role",
    "city",
    "registered_by_user_id",
    "registered_by_name",
    "registered_by_position",
    "registered_on",
    "unmet_requirement",
    "finding",
    "has_photos",
    "has_history",
    "analysis_method",
    "effectiveness_note",
    "cause_label",
    "case_id",
    "case_code",
    "previous_attempt_id",
    "legacy",
    "created_by",
)


def _process(model: ProcessModel) -> Process:
    return Process(model.id, model.code, model.name, model.is_active)


def _correction(model: CorrectionModel) -> Correction:
    return Correction(
        id=model.id,
        description=model.description,
        responsible_text=model.responsible_text,
        responsible_user_ids=list(model.responsible_user_ids or []),
        due_on=model.due_on,
        evidence_note=model.evidence_note,
        done=model.done,
        done_at=model.done_at,
    )


def _cause(model: CauseModel) -> Cause:
    return Cause(model.id, model.description, model.category)


def _activity(model: ActivityModel) -> Activity:
    return Activity(
        id=model.id,
        description=model.description,
        responsible_text=model.responsible_text,
        responsible_user_ids=list(model.responsible_user_ids or []),
        due_on=model.due_on,
        evidence_note=model.evidence_note,
        follow_up_text=model.follow_up_text,
        follow_up_due_on=model.follow_up_due_on,
        done=model.done,
        done_at=model.done_at,
        effective=model.effective,
        effectiveness_note=model.effectiveness_note,
    )


def _event(model: PlanEventModel) -> PlanEvent:
    return PlanEvent(
        model.id,
        model.plan_id,
        model.event_type,
        model.actor_user_id,
        model.note,
        dict(model.data or {}),
        model.created_at,
    )


def _file(model: PlanFileModel) -> PlanFile:
    return PlanFile(
        id=model.id,
        plan_id=model.plan_id,
        original_filename=model.original_filename,
        content_type=model.content_type,
        size_bytes=model.size_bytes,
        storage_key=model.storage_key,
        activity_id=model.activity_id,
        correction_id=model.correction_id,
        uploaded_by=model.uploaded_by,
        created_at=model.created_at,
    )


def _event_model(event: PlanEvent) -> PlanEventModel:
    return PlanEventModel(
        id=event.id,
        plan_id=event.plan_id,
        event_type=event.event_type,
        actor_user_id=event.actor_user_id,
        note=event.note,
        data=event.data,
        created_at=event.created_at,
    )


def _children_models(plan: ActionPlan) -> list:
    rows: list = []
    for index, item in enumerate(plan.corrections):
        rows.append(
            CorrectionModel(
                id=item.id,
                plan_id=plan.id,
                sort_order=index,
                description=item.description,
                responsible_text=item.responsible_text,
                responsible_user_ids=list(item.responsible_user_ids),
                due_on=item.due_on,
                evidence_note=item.evidence_note,
                done=item.done,
                done_at=item.done_at,
            )
        )
    for index, item in enumerate(plan.causes):
        rows.append(
            CauseModel(
                id=item.id,
                plan_id=plan.id,
                sort_order=index,
                category=item.category,
                description=item.description,
            )
        )
    for index, item in enumerate(plan.activities):
        rows.append(
            ActivityModel(
                id=item.id,
                plan_id=plan.id,
                sort_order=index,
                description=item.description,
                responsible_text=item.responsible_text,
                responsible_user_ids=list(item.responsible_user_ids),
                due_on=item.due_on,
                evidence_note=item.evidence_note,
                follow_up_text=item.follow_up_text,
                follow_up_due_on=item.follow_up_due_on,
                done=item.done,
                done_at=item.done_at,
                effective=item.effective,
                effectiveness_note=item.effectiveness_note,
            )
        )
    return rows


def _conditions(filters: PlanFilters) -> list:
    """Filters that can be pushed to SQL; status and effectiveness are derived and filtered
    after loading (``_derived``)."""

    plan = ActionPlanModel
    conditions: list = []
    if filters.origin:
        conditions.append(plan.origin == filters.origin)
    if filters.process_id:
        conditions.append(plan.process_id == filters.process_id)
    if filters.action_type:
        conditions.append(plan.action_type == filters.action_type)
    if filters.city:
        conditions.append(func.lower(plan.city) == filters.city.strip().lower())
    if filters.year:
        conditions.append(extract("year", plan.reported_on) == filters.year)
    if filters.month:
        conditions.append(extract("month", plan.reported_on) == filters.month)
    if filters.case_id:
        conditions.append(plan.case_id == filters.case_id)
    if filters.query:
        pattern = f"%{filters.query.strip()}%"
        conditions.append(
            or_(
                plan.code.ilike(pattern),
                plan.reporter_name.ilike(pattern),
                plan.reporter_company_role.ilike(pattern),
                plan.reporter_nit.ilike(pattern),
                plan.finding.ilike(pattern),
                plan.case_code.ilike(pattern),
            )
        )
    if filters.visible_to:
        user = filters.visible_to
        conditions.append(
            or_(
                exists().where(
                    CorrectionModel.plan_id == plan.id,
                    CorrectionModel.responsible_user_ids.any(user),
                ),
                exists().where(
                    ActivityModel.plan_id == plan.id,
                    ActivityModel.responsible_user_ids.any(user),
                ),
            )
        )
    if filters.overdue_on:
        conditions.append(
            exists().where(
                ActivityModel.plan_id == plan.id,
                ActivityModel.done.is_(False),
                ActivityModel.due_on < filters.overdue_on,
            )
        )
    return conditions


def _status_condition(status: str):
    """SQL for the derived status (open / in progress / closed)."""

    has_activity = exists().where(ActivityModel.plan_id == ActionPlanModel.id)
    has_pending = exists().where(
        ActivityModel.plan_id == ActionPlanModel.id, ActivityModel.done.is_(False)
    )
    if status == OPEN:
        return ~has_activity
    if status == IN_PROGRESS:
        return has_pending
    if status == CLOSED:
        return and_(has_activity, ~has_pending)
    return None


def _effectiveness_condition(value: str):
    """SQL for the derived effectiveness (si / no / pendiente)."""

    any_no = exists().where(
        ActivityModel.plan_id == ActionPlanModel.id, ActivityModel.effective == "no"
    )
    any_pending = exists().where(
        ActivityModel.plan_id == ActionPlanModel.id, ActivityModel.effective == "pendiente"
    )
    has_activity = exists().where(ActivityModel.plan_id == ActionPlanModel.id)
    if value == "no":
        return any_no
    if value == "si":
        return and_(has_activity, ~any_no, ~any_pending)
    if value == "pendiente":
        return and_(~any_no, or_(any_pending, ~has_activity))
    return None


class SqlAlchemyActionPlanRepository:
    """Actions and everything around them, one short transaction per call."""

    def __init__(self, database: Database) -> None:
        """Create the repository.

        Args:
            database (Database): Shared database resource.
        """

        self.database = database

    # --- Processes ---

    async def list_processes(self, include_inactive: bool = False) -> list[Process]:
        """The processes of the SIG, by code."""

        query = select(ProcessModel).order_by(ProcessModel.code)
        if not include_inactive:
            query = query.where(ProcessModel.is_active.is_(True))
        async with self.database.session_factory() as session:
            return [_process(m) for m in (await session.scalars(query)).all()]

    async def get_process(self, process_id: UUID) -> Process | None:
        """One process."""

        async with self.database.session_factory() as session:
            model = await session.get(ProcessModel, process_id)
            return _process(model) if model else None

    async def get_process_by_code(self, code: str) -> Process | None:
        """A process by its code."""

        async with self.database.session_factory() as session:
            model = await session.scalar(select(ProcessModel).where(ProcessModel.code == code))
            return _process(model) if model else None

    async def save_process(self, process: Process) -> Process:
        """Insert or update a process."""

        async with self.database.session_factory() as session:
            model = await session.get(ProcessModel, process.id)
            if model is None:
                model = ProcessModel(id=process.id)
                session.add(model)
            model.code, model.name, model.is_active = process.code, process.name, process.is_active
            try:
                await session.commit()
            except IntegrityError as exc:
                await session.rollback()
                raise ConflictError("Ya existe un proceso con ese código") from exc
            return _process(model)

    # --- Actions ---

    async def next_number(self) -> int:
        """The highest number plus one."""

        async with self.database.session_factory() as session:
            highest = await session.scalar(select(func.max(ActionPlanModel.number)))
        return int(highest or 0) + 1

    async def code_exists(self, code: str) -> bool:
        """Whether an action has this code."""

        async with self.database.session_factory() as session:
            found = await session.scalar(
                select(ActionPlanModel.id).where(ActionPlanModel.code == code).limit(1)
            )
        return found is not None

    async def create(self, plan: ActionPlan, event: PlanEvent) -> ActionPlan:
        """Insert an action with its children and its first event."""

        async with self.database.session_factory() as session:
            session.add(self._header(ActionPlanModel(id=plan.id), plan))
            await session.flush()
            session.add_all(_children_models(plan))
            session.add(_event_model(event))
            try:
                await session.commit()
            except IntegrityError as exc:
                await session.rollback()
                raise ConflictError(f"Ya existe la acción {plan.code}") from exc
        return await self.get(plan.id)  # type: ignore[return-value]

    async def save(self, plan: ActionPlan, events: Sequence[PlanEvent]) -> ActionPlan:
        """Rewrite the header and the children, and add events, in one transaction."""

        async with self.database.session_factory() as session:
            model = await session.get(ActionPlanModel, plan.id)
            if model is None:
                raise ConflictError("La acción ya no existe")
            self._header(model, plan)
            for table in (CorrectionModel, CauseModel, ActivityModel):
                await session.execute(delete(table).where(table.plan_id == plan.id))
            await session.flush()
            session.add_all(_children_models(plan))
            session.add_all([_event_model(e) for e in events])
            try:
                await session.commit()
            except IntegrityError as exc:
                await session.rollback()
                raise ConflictError(f"Ya existe la acción {plan.code}") from exc
        return await self.get(plan.id)  # type: ignore[return-value]

    async def get(self, plan_id: UUID) -> ActionPlan | None:
        """One action with its children."""

        async with self.database.session_factory() as session:
            plans = await self._load(session, [plan_id])
        return plans[0] if plans else None

    async def list_plans(
        self, filters: PlanFilters, offset: int, limit: int
    ) -> tuple[list[ActionPlan], int]:
        """A page of actions, newest first, and the total."""

        conditions = self._all_conditions(filters)
        async with self.database.session_factory() as session:
            total = await session.scalar(
                select(func.count()).select_from(ActionPlanModel).where(*conditions)
            )
            ids = (
                await session.scalars(
                    select(ActionPlanModel.id)
                    .where(*conditions)
                    .order_by(
                        ActionPlanModel.reported_on.desc(),
                        ActionPlanModel.number.desc(),
                        ActionPlanModel.attempt.desc(),
                    )
                    .offset(offset)
                    .limit(limit)
                )
            ).all()
            plans = await self._load(session, list(ids))
        return plans, int(total or 0)

    async def all_plans(self, filters: PlanFilters) -> list[ActionPlan]:
        """Every action of the filters, oldest first (the order of the Excel)."""

        async with self.database.session_factory() as session:
            ids = (
                await session.scalars(
                    select(ActionPlanModel.id)
                    .where(*self._all_conditions(filters))
                    .order_by(
                        ActionPlanModel.reported_on,
                        ActionPlanModel.number,
                        ActionPlanModel.attempt,
                    )
                )
            ).all()
            return await self._load(session, list(ids))

    async def similar(self, reporter_nit: str, cause_label: str | None, exclude: UUID) -> int:
        """Earlier actions of the same customer (and cause, when known)."""

        conditions = [ActionPlanModel.reporter_nit == reporter_nit, ActionPlanModel.id != exclude]
        if cause_label:
            conditions.append(func.lower(ActionPlanModel.cause_label) == cause_label.lower())
        async with self.database.session_factory() as session:
            return int(
                await session.scalar(
                    select(func.count()).select_from(ActionPlanModel).where(*conditions)
                )
                or 0
            )

    async def delete(self, plan_id: UUID) -> list[str]:
        """Delete an action and return the keys of its files."""

        async with self.database.session_factory() as session:
            keys = (
                await session.scalars(
                    select(PlanFileModel.storage_key).where(PlanFileModel.plan_id == plan_id)
                )
            ).all()
            await session.execute(delete(ActionPlanModel).where(ActionPlanModel.id == plan_id))
            await session.commit()
        return list(keys)

    # --- History and files ---

    async def add_event(self, event: PlanEvent) -> None:
        """Append to the history."""

        async with self.database.session_factory() as session:
            session.add(_event_model(event))
            await session.commit()

    async def list_events(self, plan_id: UUID) -> list[PlanEvent]:
        """The history, oldest first."""

        query = (
            select(PlanEventModel)
            .where(PlanEventModel.plan_id == plan_id)
            .order_by(PlanEventModel.created_at)
        )
        async with self.database.session_factory() as session:
            return [_event(m) for m in (await session.scalars(query)).all()]

    async def add_file(self, file: PlanFile) -> None:
        """Record an evidence file."""

        async with self.database.session_factory() as session:
            session.add(
                PlanFileModel(
                    id=file.id,
                    plan_id=file.plan_id,
                    activity_id=file.activity_id,
                    correction_id=file.correction_id,
                    original_filename=file.original_filename,
                    content_type=file.content_type,
                    size_bytes=file.size_bytes,
                    storage_key=file.storage_key,
                    uploaded_by=file.uploaded_by,
                )
            )
            await session.commit()

    async def list_files(self, plan_id: UUID) -> list[PlanFile]:
        """The evidence of an action, oldest first."""

        query = (
            select(PlanFileModel)
            .where(PlanFileModel.plan_id == plan_id)
            .order_by(PlanFileModel.created_at)
        )
        async with self.database.session_factory() as session:
            return [_file(m) for m in (await session.scalars(query)).all()]

    async def get_file(self, plan_id: UUID, file_id: UUID) -> PlanFile | None:
        """One evidence file of an action."""

        async with self.database.session_factory() as session:
            model = await session.get(PlanFileModel, file_id)
        return _file(model) if model and model.plan_id == plan_id else None

    async def delete_file(self, file_id: UUID) -> None:
        """Forget an evidence file."""

        async with self.database.session_factory() as session:
            await session.execute(delete(PlanFileModel).where(PlanFileModel.id == file_id))
            await session.commit()

    # --- Helpers ---

    @staticmethod
    def _all_conditions(filters: PlanFilters) -> list:
        conditions = _conditions(filters)
        if filters.status:
            condition = _status_condition(filters.status)
            if condition is not None:
                conditions.append(condition)
        if filters.effectiveness:
            condition = _effectiveness_condition(filters.effectiveness)
            if condition is not None:
                conditions.append(condition)
        return conditions

    @staticmethod
    def _header(model: ActionPlanModel, plan: ActionPlan) -> ActionPlanModel:
        for name in _HEADER:
            setattr(model, name, getattr(plan, name))
        model.process_id = plan.process.id
        return model

    @staticmethod
    async def _load(session: AsyncSession, ids: list[UUID]) -> list[ActionPlan]:
        if not ids:
            return []
        rows = (
            await session.execute(
                select(ActionPlanModel, ProcessModel)
                .join(ProcessModel, ProcessModel.id == ActionPlanModel.process_id)
                .where(ActionPlanModel.id.in_(ids))
            )
        ).all()
        corrections: dict[UUID, list[Correction]] = defaultdict(list)
        causes: dict[UUID, list[Cause]] = defaultdict(list)
        activities: dict[UUID, list[Activity]] = defaultdict(list)
        for model in await session.scalars(
            select(CorrectionModel)
            .where(CorrectionModel.plan_id.in_(ids))
            .order_by(CorrectionModel.sort_order)
        ):
            corrections[model.plan_id].append(_correction(model))
        for model in await session.scalars(
            select(CauseModel).where(CauseModel.plan_id.in_(ids)).order_by(CauseModel.sort_order)
        ):
            causes[model.plan_id].append(_cause(model))
        for model in await session.scalars(
            select(ActivityModel)
            .where(ActivityModel.plan_id.in_(ids))
            .order_by(ActivityModel.sort_order)
        ):
            activities[model.plan_id].append(_activity(model))
        by_id = {}
        for model, process in rows:
            plan = ActionPlan(
                id=model.id,
                number=model.number,
                attempt=model.attempt,
                origin=model.origin,
                process=_process(process),
                action_type=model.action_type,
                rating=model.rating,
                reported_on=model.reported_on,
                corrections=corrections[model.id],
                causes=causes[model.id],
                activities=activities[model.id],
                created_at=model.created_at,
                updated_at=model.updated_at,
            )
            for name in _HEADER:
                if name not in {"number", "attempt", "origin", "action_type", "rating"}:
                    setattr(plan, name, getattr(model, name))
            by_id[model.id] = plan
        return [by_id[i] for i in ids if i in by_id]
