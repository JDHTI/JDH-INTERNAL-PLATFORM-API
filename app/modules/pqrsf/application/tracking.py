"""Follow-up use cases: catalogs, case inbox, assignment, statuses and documents."""

from __future__ import annotations

import logging
from collections.abc import Container
from dataclasses import replace
from datetime import UTC, date, datetime
from uuid import UUID, uuid4

from app.core.errors import ConflictError, ForbiddenError, InvalidInputError, NotFoundError
from app.modules.identity.domain.principal import DirectoryUser, Principal
from app.modules.pqrsf.application.dto import (
    ActivityPage,
    CaseAccess,
    CaseChanges,
    CaseDetail,
    CaseView,
    CaseViewPage,
    EventView,
    ImportedCase,
    ItemChanges,
    LastSeenView,
    TrackingCatalog,
)
from app.modules.pqrsf.domain.email import EVENT_EMAIL_RECEIVED, EmailCaseInput
from app.modules.pqrsf.domain.entities import (
    HANDLING_ACKNOWLEDGE,
    HANDLING_CASE,
    MAX_TYPE_DESCRIPTION,
    PqrsfRequest,
    RequestType,
    clean_multiline,
    clean_optional,
)
from app.modules.pqrsf.domain.ports import (
    AssignmentNotifier,
    BusinessCalendar,
    CaseRepository,
    ManagerPolicy,
    PeopleDirectory,
    PqrsfRepository,
)
from app.modules.pqrsf.domain.tracking import (
    ACCESS_REPEAT_WINDOW,
    ACCESS_RETENTION,
    CASE_SORTS,
    IN_PROGRESS_STATUS_CODE,
    READ_STATUS_CODE,
    ActivityFilters,
    CaseEvent,
    CaseFilters,
    CaseInput,
    CaseItem,
    CaseStatus,
    CaseSummary,
    Dependency,
    DocumentType,
    EventType,
    ItemInput,
    TrackingCase,
    add_business_days,
    business_days_between,
    clean_code,
    clean_color,
    clean_prefix,
    validate_quantity,
    validate_sla_days,
)
from app.modules.sites.application.service import SiteService
from app.modules.sites.domain.entities import Site

logger = logging.getLogger(__name__)

SUGGESTION_FIELDS = frozenset({"customer", "supplier", "sales_executive", "requester"})
EXPORT_LIMIT = 20_000
EXPORT_HEADER = [
    "Fecha de Petición",
    "Consecutivo",
    "Tipo",
    "Responsable",
    "Proveedor",
    "Dependencia (Interno)",
    "Usuario",
    "Cliente",
    "NIT Cliente",
    "Ejecutivo Comercial",
    "Tipo de documento",
    "Cantidad",
    "Sucursal",
    "Fecha de Respuesta",
    "Días de Respuesta",
    "Tiempo de respuesta Establecido",
    "Diferencia",
    "Fecha Límite",
    "Estado",
    "Causal Tiempo de Respuesta",
    "Detalle Tiempo de Respuesta",
]
_CLOSED_MESSAGE = "El caso está finalizado: reábrelo para modificarlo"
_ITEM_LIMIT = 200


class CaseTrackingService:
    """Coordinate the follow-up of PQRSF cases.

    Managers (platform administrators and the configured PQRSF area) see every
    case, assign responsibles and edit catalogs. A responsible works the cases
    assigned to them; whoever raised a case can follow and comment on it.
    """

    def __init__(
        self,
        repository: CaseRepository,
        requests: PqrsfRepository,
        sites: SiteService,
        people: PeopleDirectory,
        managers: ManagerPolicy,
        calendar: BusinessCalendar,
        notifier: AssignmentNotifier | None = None,
    ) -> None:
        """Create the service.

        Args:
            repository (CaseRepository): Case persistence port.
            requests (PqrsfRepository): Public request persistence port.
            sites (SiteService): Site catalog use cases.
            people (PeopleDirectory): Platform users.
            managers (ManagerPolicy): Decides who manages PQRSF.
            calendar (BusinessCalendar): Local dates and holidays.
            notifier (AssignmentNotifier | None): Emails the new responsible of a case.
        """

        self.notifier = notifier
        self.repository = repository
        self.requests = requests
        self.sites = sites
        self.people = people
        self.managers = managers
        self.calendar = calendar

    # --- Access ---

    def _access(self, case: TrackingCase, principal: Principal, is_manager: bool) -> CaseAccess:
        if is_manager:
            return CaseAccess(can_view=True, can_work=True, can_assign=True, can_delete=True)
        if case.assignee_user_id == principal.user_id:
            return CaseAccess(can_view=True, can_work=True, can_assign=False)
        if case.created_by_user_id == principal.user_id:
            return CaseAccess(can_view=True, can_work=False, can_assign=False)
        return CaseAccess(can_view=False, can_work=False, can_assign=False)

    async def _load(
        self, case_id: UUID, principal: Principal
    ) -> tuple[TrackingCase, CaseAccess, bool]:
        case = await self.repository.get_case(case_id)
        if case is None:
            raise NotFoundError("Caso PQRSF no encontrado")
        is_manager = await self.managers.is_manager(principal)
        access = self._access(case, principal, is_manager)
        if not access.can_view:
            raise ForbiddenError("Este caso no está asignado a ti")
        return case, access, is_manager

    async def _load_for_work(self, case_id: UUID, principal: Principal) -> TrackingCase:
        case, access, _ = await self._load(case_id, principal)
        if not access.can_work:
            raise ForbiddenError("Solo el responsable o quien gestiona PQRSF puede hacer cambios")
        return case

    async def _require_manager(self, principal: Principal) -> None:
        if not await self.managers.is_manager(principal):
            raise ForbiddenError("Solo quien gestiona PQRSF puede hacer esta operación")

    async def _view(self, case: TrackingCase, principal: Principal, is_manager: bool) -> CaseView:
        return (await self._views([case], principal, is_manager))[0]

    async def _views(
        self, cases: list[TrackingCase], principal: Principal, is_manager: bool
    ) -> list[CaseView]:
        ids = {c.assignee_user_id for c in cases} | {c.created_by_user_id for c in cases}
        users = await self.people.get_users(i for i in ids if i is not None)
        today = self.calendar.today()
        return [
            CaseView(
                case=case,
                assignee=users.get(case.assignee_user_id) if case.assignee_user_id else None,
                created_by=users.get(case.created_by_user_id) if case.created_by_user_id else None,
                access=self._access(case, principal, is_manager),
                is_overdue=case.is_overdue(today),
                overdue_item_ids=frozenset(
                    item.id
                    for item in case.items
                    if not case.is_closed
                    and item.responded_on is None
                    and item.due_on is not None
                    and item.due_on < today
                ),
            )
            for case in cases
        ]

    # --- Dates ---

    def _holidays(self, received_on: date) -> Container[date]:
        return self.calendar.holidays(
            date(received_on.year, 1, 1), date(received_on.year + 2, 12, 31)
        )

    def _refresh_dates(self, case: TrackingCase) -> None:
        """Recompute item and case due dates and item response times."""

        holidays = self._holidays(case.received_on)
        item_dues: list[date] = []
        for item in case.items:
            item.due_on = (
                add_business_days(case.received_on, item.sla_days, holidays)
                if item.sla_days is not None
                else None
            )
            item.response_days = (
                business_days_between(case.received_on, item.responded_on, holidays)
                if item.responded_on is not None
                else None
            )
            if item.due_on is not None:
                item_dues.append(item.due_on)
        if item_dues:
            case.due_on = max(item_dues)
        elif case.request_type.requires_follow_up and case.request_type.sla_days is not None:
            case.due_on = add_business_days(case.received_on, case.request_type.sla_days, holidays)
        else:
            case.due_on = None

    # --- Catalogs ---

    async def catalog(
        self, principal: Principal, include_inactive: bool = False
    ) -> TrackingCatalog:
        """Return the selectors of the follow-up screens.

        Args:
            principal (Principal): The caller.
            include_inactive (bool): Include disabled entries (managers only).

        Returns:
            TrackingCatalog: Request types, statuses, dependencies, document
                types, sites and whether the caller manages PQRSF.
        """

        is_manager = await self.managers.is_manager(principal)
        inactive = include_inactive and is_manager
        # Suggestions and compliments have their own section: they are not types of cases.
        request_types = [
            kind
            for kind in await self.requests.list_request_types(active_only=not inactive)
            if kind.handling == HANDLING_CASE
        ]
        return TrackingCatalog(
            request_types=request_types,
            statuses=await self.repository.list_statuses(inactive),
            dependencies=await self.repository.list_dependencies(inactive),
            document_types=await self.repository.list_document_types(inactive),
            sites=await self.sites.list_sites(include_inactive=True),
            is_manager=is_manager,
        )

    async def list_request_types(self, principal: Principal) -> list[RequestType]:
        """List every request type with its settings, including suggestions and compliments.

        Args:
            principal (Principal): The caller, who must manage PQRSF.

        Returns:
            list[RequestType]: The five types, active or not, in display order.
        """

        await self._require_manager(principal)
        return await self.requests.list_request_types(active_only=False)

    async def update_request_type(
        self,
        request_type_id: UUID,
        principal: Principal,
        *,
        fields: frozenset[str],
        prefix: str | None = None,
        requires_follow_up: bool | None = None,
        sla_days: int | None = None,
        handling: str | None = None,
        description: str | None = None,
        allows_attachments: bool | None = None,
        asks_area: bool | None = None,
    ) -> RequestType:
        """Change the follow-up settings of a request type.

        Args:
            request_type_id (UUID): Request type to change.
            principal (Principal): The caller, who must manage PQRSF.
            fields (frozenset[str]): Names of the fields to apply.
            prefix (str | None): Consecutive prefix.
            requires_follow_up (bool | None): Whether cases get a due date.
            sla_days (int | None): Default response time in business days.
            handling (str | None): ``case`` opens cases, ``acknowledge`` only thanks.
            description (str | None): Short line shown under the type on the form;
                ``None`` clears it.
            allows_attachments (bool | None): Whether the form accepts evidence files.
            asks_area (bool | None): Whether the public form asks for the area.

        Returns:
            RequestType: The updated type.
        """

        await self._require_manager(principal)
        request_type = await self.requests.get_request_type(request_type_id)
        if request_type is None:
            raise NotFoundError("Tipo de solicitud no encontrado")
        if "prefix" in fields and prefix is not None:
            request_type.prefix = clean_prefix(prefix)
        if "requires_follow_up" in fields and requires_follow_up is not None:
            request_type.requires_follow_up = requires_follow_up
        if "sla_days" in fields:
            request_type.sla_days = validate_sla_days(sla_days)
        if "handling" in fields and handling is not None:
            if handling not in (HANDLING_CASE, HANDLING_ACKNOWLEDGE):
                raise InvalidInputError("El manejo debe ser 'case' o 'acknowledge'")
            request_type.handling = handling
        if "description" in fields:
            request_type.description = clean_optional(
                description, "descripción del tipo", MAX_TYPE_DESCRIPTION
            )
        if "allows_attachments" in fields and allows_attachments is not None:
            request_type.allows_attachments = allows_attachments
        if "asks_area" in fields and asks_area is not None:
            request_type.asks_area = asks_area
        return await self.requests.save_request_type(request_type)

    async def create_status(
        self,
        principal: Principal,
        *,
        code: str,
        name: str,
        color: str | None = None,
        sort_order: int = 0,
        is_initial: bool = False,
        is_terminal: bool = False,
    ) -> CaseStatus:
        """Add a follow-up status.

        Args:
            principal (Principal): The caller, who must manage PQRSF.
            code (str): Stable code.
            name (str): Visible name.
            color (str | None): Badge color ``#rrggbb``.
            sort_order (int): Position in selectors and boards.
            is_initial (bool): New cases start here (only one status can be).
            is_terminal (bool): The case is closed in this status.

        Returns:
            CaseStatus: The new status.
        """

        await self._require_manager(principal)
        code = clean_code(code)
        if await self.repository.get_status_by_code(code):
            raise ConflictError("Ya existe un estado con ese código")
        if is_initial and is_terminal:
            raise InvalidInputError("Un estado no puede ser inicial y final a la vez")
        now = datetime.now(UTC)
        return await self.repository.save_status(
            CaseStatus(
                id=uuid4(),
                code=code,
                name=_clean_name(name),
                color=clean_color(color),
                sort_order=sort_order,
                is_initial=is_initial,
                is_terminal=is_terminal,
                is_active=True,
                created_at=now,
                updated_at=now,
            )
        )

    async def update_status(
        self,
        status_id: UUID,
        principal: Principal,
        *,
        fields: frozenset[str],
        name: str | None = None,
        color: str | None = None,
        sort_order: int | None = None,
        is_initial: bool | None = None,
        is_terminal: bool | None = None,
        is_active: bool | None = None,
    ) -> CaseStatus:
        """Edit a status; its code is immutable.

        Args:
            status_id (UUID): Status to change.
            principal (Principal): The caller, who must manage PQRSF.
            fields (frozenset[str]): Names of the fields to apply.
            name (str | None): Visible name.
            color (str | None): Badge color.
            sort_order (int | None): Position.
            is_initial (bool | None): Make it the starting status.
            is_terminal (bool | None): Whether it closes the case.
            is_active (bool | None): Offer it in selectors.

        Returns:
            CaseStatus: The updated status.
        """

        await self._require_manager(principal)
        status = await self.repository.get_status(status_id)
        if status is None:
            raise NotFoundError("Estado no encontrado")
        if "name" in fields and name is not None:
            status.name = _clean_name(name)
        if "color" in fields:
            status.color = clean_color(color)
        if "sort_order" in fields and sort_order is not None:
            status.sort_order = sort_order
        if "is_terminal" in fields and is_terminal is not None:
            status.is_terminal = is_terminal
        if "is_active" in fields and is_active is not None:
            status.is_active = is_active
        if "is_initial" in fields and is_initial is not None:
            if status.is_initial and not is_initial:
                raise InvalidInputError("Marca otro estado como inicial en lugar de quitar este")
            status.is_initial = is_initial
        if status.is_initial and (status.is_terminal or not status.is_active):
            raise InvalidInputError("El estado inicial debe estar activo y no puede ser final")
        status.updated_at = datetime.now(UTC)
        return await self.repository.save_status(status)

    async def create_dependency(
        self, principal: Principal, *, code: str, name: str, sort_order: int = 0
    ) -> Dependency:
        """Add an internal dependency.

        Args:
            principal (Principal): The caller, who must manage PQRSF.
            code (str): Stable code.
            name (str): Visible name.
            sort_order (int): Position in selectors.

        Returns:
            Dependency: The new dependency.
        """

        await self._require_manager(principal)
        code = clean_code(code)
        if any(d.code == code for d in await self.repository.list_dependencies(True)):
            raise ConflictError("Ya existe una dependencia con ese código")
        now = datetime.now(UTC)
        return await self.repository.save_dependency(
            Dependency(uuid4(), code, _clean_name(name), sort_order, True, now, now)
        )

    async def update_dependency(
        self,
        dependency_id: UUID,
        principal: Principal,
        *,
        fields: frozenset[str],
        name: str | None = None,
        sort_order: int | None = None,
        is_active: bool | None = None,
    ) -> Dependency:
        """Edit a dependency; its code is immutable.

        Args:
            dependency_id (UUID): Dependency to change.
            principal (Principal): The caller, who must manage PQRSF.
            fields (frozenset[str]): Names of the fields to apply.
            name (str | None): Visible name.
            sort_order (int | None): Position.
            is_active (bool | None): Offer it in selectors.

        Returns:
            Dependency: The updated dependency.
        """

        await self._require_manager(principal)
        dependency = await self.repository.get_dependency(dependency_id)
        if dependency is None:
            raise NotFoundError("Dependencia no encontrada")
        if "name" in fields and name is not None:
            dependency.name = _clean_name(name)
        if "sort_order" in fields and sort_order is not None:
            dependency.sort_order = sort_order
        if "is_active" in fields and is_active is not None:
            dependency.is_active = is_active
        dependency.updated_at = datetime.now(UTC)
        return await self.repository.save_dependency(dependency)

    async def create_document_type(
        self, principal: Principal, *, name: str, sla_days: int
    ) -> DocumentType:
        """Add a document type with its agreed response time.

        Args:
            principal (Principal): The caller, who must manage PQRSF.
            name (str): Visible name.
            sla_days (int): Response time in business days.

        Returns:
            DocumentType: The new document type.
        """

        await self._require_manager(principal)
        name = _clean_name(name, max_length=200)
        existing = await self.repository.list_document_types(True)
        if any(d.name.lower() == name.lower() for d in existing):
            raise ConflictError("Ya existe un tipo de documento con ese nombre")
        now = datetime.now(UTC)
        return await self.repository.save_document_type(
            DocumentType(uuid4(), name, validate_sla_days(sla_days) or 0, True, now, now)
        )

    async def update_document_type(
        self,
        document_type_id: UUID,
        principal: Principal,
        *,
        fields: frozenset[str],
        name: str | None = None,
        sla_days: int | None = None,
        is_active: bool | None = None,
        allows_direct_upload: bool | None = None,
    ) -> DocumentType:
        """Edit a document type. Existing items keep the time they were given.

        Args:
            document_type_id (UUID): Document type to change.
            principal (Principal): The caller, who must manage PQRSF.
            fields (frozenset[str]): Names of the fields to apply.
            name (str | None): Visible name.
            sla_days (int | None): Response time for new items.
            is_active (bool | None): Offer it in selectors.
            allows_direct_upload (bool | None): Its answer may skip the document manager.

        Returns:
            DocumentType: The updated document type.
        """

        await self._require_manager(principal)
        document_type = await self.repository.get_document_type(document_type_id)
        if document_type is None:
            raise NotFoundError("Tipo de documento no encontrado")
        if "name" in fields and name is not None:
            cleaned = _clean_name(name, max_length=200)
            existing = await self.repository.list_document_types(True)
            if any(
                d.name.lower() == cleaned.lower() and d.id != document_type.id for d in existing
            ):
                raise ConflictError("Ya existe un tipo de documento con ese nombre")
            document_type.name = cleaned
        if "sla_days" in fields and sla_days is not None:
            document_type.sla_days = validate_sla_days(sla_days) or 0
        if "is_active" in fields and is_active is not None:
            document_type.is_active = is_active
        if "allows_direct_upload" in fields and allows_direct_upload is not None:
            document_type.allows_direct_upload = allows_direct_upload
        document_type.updated_at = datetime.now(UTC)
        return await self.repository.save_document_type(document_type)

    async def search_assignees(
        self, query: str, principal: Principal, limit: int = 20
    ) -> list[DirectoryUser]:
        """Find active users a manager can make responsible.

        Args:
            query (str): Text in the name or email.
            principal (Principal): The caller, who must manage PQRSF.
            limit (int): Maximum number of results.

        Returns:
            list[DirectoryUser]: Matching users.
        """

        await self._require_manager(principal)
        return await self.people.search_users(query, min(max(1, limit), 50))

    # --- Opening cases ---

    async def _initial_status(self) -> CaseStatus:
        statuses = await self.repository.list_statuses()
        initial = next((s for s in statuses if s.is_initial), None)
        if initial is None:
            raise ConflictError("No hay un estado inicial configurado para PQRSF")
        return initial

    async def _active_status_by_code(self, code: str) -> CaseStatus | None:
        status = await self.repository.get_status_by_code(code)
        return status if status and status.is_active else None

    async def _dependency(self, dependency_id: UUID | None) -> Dependency | None:
        if dependency_id is None:
            return None
        dependency = await self.repository.get_dependency(dependency_id)
        if dependency is None:
            raise NotFoundError("Dependencia no encontrada")
        return dependency

    async def _site(self, site_id: UUID | None) -> Site | None:
        return await self.sites.get_site(site_id) if site_id else None

    async def _assignee(self, user_id: UUID) -> DirectoryUser:
        user = (await self.people.get_users([user_id])).get(user_id)
        if user is None:
            raise NotFoundError("El responsable no existe: debe iniciar sesión una vez")
        if not user.is_active:
            raise InvalidInputError("El responsable está inactivo")
        return user

    async def _new_item(self, case_id: UUID, value: ItemInput, sort_order: int) -> CaseItem:
        document_type = None
        if value.document_type_id is not None:
            document_type = await self.repository.get_document_type(value.document_type_id)
            if document_type is None:
                raise NotFoundError("Tipo de documento no encontrado")
        description = clean_optional(value.description, "descripción del documento", 300)
        if document_type is None and description is None:
            raise InvalidInputError("Indica el tipo de documento o una descripción")
        now = datetime.now(UTC)
        return CaseItem(
            id=uuid4(),
            case_id=case_id,
            document_type=document_type,
            description=description,
            quantity=validate_quantity(value.quantity),
            sla_days=document_type.sla_days if document_type else None,
            due_on=None,
            responded_on=None,
            response_days=None,
            delay_cause=None,
            delay_detail=None,
            sort_order=sort_order,
            created_at=now,
            updated_at=now,
        )

    def _blank_case(
        self,
        request_type: RequestType,
        status: CaseStatus,
        received_on: date,
        origin: str,
        created_by: UUID | None,
    ) -> TrackingCase:
        now = datetime.now(UTC)
        return TrackingCase(
            id=uuid4(),
            code="",
            number=0,
            year=received_on.year,
            series="",
            request_type=request_type,
            status=status,
            origin=origin,
            subject=None,
            description=None,
            received_on=received_on,
            due_on=None,
            site=None,
            dependency=None,
            assignee_user_id=None,
            customer_name=None,
            customer_nit=None,
            supplier_name=None,
            sales_executive_name=None,
            requester_name=None,
            requester_email=None,
            request_id=None,
            created_by_user_id=created_by,
            first_read_at=None,
            closed_at=None,
            legacy=None,
            created_at=now,
            updated_at=now,
        )

    def _event(
        self,
        case_id: UUID | None,
        event_type: str,
        actor: UUID | None,
        *,
        note: str | None = None,
        from_status: UUID | None = None,
        to_status: UUID | None = None,
        data: dict | None = None,
    ) -> CaseEvent:
        return CaseEvent(
            id=uuid4(),
            case_id=case_id,
            event_type=event_type,
            actor_user_id=actor,
            from_status_id=from_status,
            to_status_id=to_status,
            note=note,
            data=data or {},
            created_at=datetime.now(UTC),
        )

    async def open_for_request(self, request: PqrsfRequest) -> TrackingCase:
        """Open the case of a public request; calling it again returns the same case.

        Args:
            request (PqrsfRequest): The stored public request.

        Returns:
            TrackingCase: The request's case.
        """

        existing = await self.repository.get_case_by_request(request.id)
        if existing is not None:
            return existing
        case = self._blank_case(
            request.request_type,
            await self._initial_status(),
            self.calendar.local_date(request.created_at),
            "public_form",
            None,
        )
        case.request_id = request.id
        case.site = request.site
        case.subject = request.case_subject
        case.description = request.description
        case.customer_name = request.business_name
        case.customer_nit = request.nit
        case.requester_name = request.full_name
        case.requester_email = request.email
        suppliers = list(
            dict.fromkeys(p.supplier_name for p in request.products if p.supplier_name)
        )
        case.supplier_name = ", ".join(suppliers)[:200] or None
        # The cause picked (or the category of the documents) says who attends it.
        dependency = (
            await self.repository.get_dependency(request.dependency_id)
            if request.dependency_id
            else None
        )
        case.dependency = dependency if dependency and dependency.is_active else None
        if request.document_request:
            # The customer already said what they need: the documents become
            # items, with their response times.
            case.items = [
                await self._new_item(
                    case.id, ItemInput(item.document_type_id, item.description), position
                )
                for position, item in enumerate(request.documents)
            ]
        self._refresh_dates(case)
        event = self._event(case.id, EventType.CREATED, None, data={"origin": case.origin})
        try:
            return await self.repository.create_case(case, event)
        except ConflictError:
            existing = await self.repository.get_case_by_request(request.id)
            if existing is None:
                raise
            return existing

    async def code_for_request(self, request_id: UUID) -> str | None:
        """Return the consecutive of a request's case.

        Args:
            request_id (UUID): Public request identifier.

        Returns:
            str | None: The case code, or ``None`` when no case exists.
        """

        case = await self.repository.get_case_by_request(request_id)
        return case.code if case else None

    async def open_from_email(self, command: EmailCaseInput) -> TrackingCase:
        """Open the case of an email that matched no open one.

        Args:
            command (EmailCaseInput): Sender, parsed template fields and the stored message.

        Returns:
            TrackingCase: The new case, with ``origin`` ``email``.
        """

        case = self._blank_case(
            command.request_type,
            await self._initial_status(),
            self.calendar.local_date(command.received_at),
            "email",
            None,
        )
        case.site = command.site
        case.subject = command.subject
        case.description = command.description
        case.customer_name = command.business_name
        case.customer_nit = command.nit
        case.requester_name = command.requester_name
        case.requester_email = command.requester_email
        # The cause named in the template (else its subject) says who attends it.
        dependency_id = (command.cause_ref and command.cause_ref.dependency_id) or (
            command.subject_ref and command.subject_ref.dependency_id
        )
        if dependency_id:
            dependency = await self.repository.get_dependency(dependency_id)
            case.dependency = dependency if dependency and dependency.is_active else None
        self._refresh_dates(case)
        data: dict = {"origin": "email", "message_id": str(command.message_id)}
        if command.related_code:
            data["related_case"] = command.related_code
        if command.subject_ref:
            data["subject"] = {"code": command.subject_ref.code, "name": command.subject_ref.name}
        if command.cause_ref:
            data["cause"] = {"code": command.cause_ref.code, "name": command.cause_ref.name}
        return await self.repository.create_case(
            case, self._event(case.id, EventType.CREATED, None, data=data)
        )

    async def record_email(
        self, case_id: UUID, message_id: UUID, sender: str, note: str, data: dict
    ) -> None:
        """Add a received email to the timeline of a case.

        Args:
            case_id (UUID): The case the message joined.
            message_id (UUID): Stored message identifier.
            sender (str): Sender address.
            note (str): Short excerpt of the message.
            data (dict): Extra detail (rejected attachments, stored files).
        """

        await self.repository.add_event(
            self._event(
                case_id,
                EVENT_EMAIL_RECEIVED,
                None,
                note=note,
                data={"message_id": str(message_id), "from": sender, **data},
            )
        )

    async def find_case(self, case_id: UUID) -> TrackingCase | None:
        """Load a case without checking access (system use)."""

        return await self.repository.get_case(case_id)

    async def require_view(self, case_id: UUID, principal: Principal) -> TrackingCase:
        """Return the case when the caller may see it; used before serving its files."""

        case, _, _ = await self._load(case_id, principal)
        return case

    async def create_case(self, command: CaseInput, principal: Principal) -> CaseView:
        """Raise a case internally (the spreadsheet flow).

        Args:
            command (CaseInput): Case header and requested documents.
            principal (Principal): The caller; only managers may set a responsible.

        Returns:
            CaseView: The new case.
        """

        is_manager = await self.managers.is_manager(principal)
        if command.assignee_user_id is not None and not is_manager:
            raise ForbiddenError("Solo quien gestiona PQRSF puede asignar responsables")
        request_type = await self.requests.get_request_type(command.request_type_id)
        if request_type is None or not request_type.is_active:
            raise NotFoundError("Tipo de solicitud no encontrado")
        if request_type.handling != HANDLING_CASE:
            raise InvalidInputError(
                f"{request_type.name} no abre casos: se guarda en su propia sección"
            )
        if len(command.items) > _ITEM_LIMIT:
            raise InvalidInputError(f"Un caso admite máximo {_ITEM_LIMIT} documentos")
        received_on = command.received_on or self.calendar.today()
        if received_on > self.calendar.today():
            raise InvalidInputError("La fecha de recepción no puede ser futura")

        case = self._blank_case(
            request_type, await self._initial_status(), received_on, "internal", principal.user_id
        )
        case.subject = clean_optional(command.subject, "asunto", 200)
        case.description = clean_optional(command.description, "descripción", 10_000)
        case.site = await self._site(command.site_id)
        case.dependency = await self._dependency(command.dependency_id)
        case.customer_name = clean_optional(command.customer_name, "cliente", 200)
        case.customer_nit = clean_optional(command.customer_nit, "NIT", 30)
        case.supplier_name = clean_optional(command.supplier_name, "proveedor", 200)
        case.sales_executive_name = clean_optional(
            command.sales_executive_name, "ejecutivo comercial", 200
        )
        case.requester_name = clean_optional(command.requester_name, "usuario", 200)
        case.requester_email = _clean_email(command.requester_email)
        if command.assignee_user_id is not None:
            case.assignee_user_id = (await self._assignee(command.assignee_user_id)).id
        case.items = [
            await self._new_item(case.id, value, position)
            for position, value in enumerate(command.items)
        ]
        self._refresh_dates(case)
        event = self._event(
            case.id,
            EventType.CREATED,
            principal.user_id,
            data={"origin": case.origin, "assignee_user_id": _str(case.assignee_user_id)},
        )
        stored = await self.repository.create_case(case, event)
        if stored.assignee_user_id is not None:
            people = await self.people.get_users([stored.assignee_user_id])
            await self._notify_assigned(
                stored, people.get(stored.assignee_user_id), principal, None
            )
        return await self._view(stored, principal, is_manager)

    # --- Reading ---

    async def _scoped(
        self, filters: CaseFilters, principal: Principal, mine: bool
    ) -> tuple[CaseFilters, bool]:
        if filters.sort not in CASE_SORTS:
            raise InvalidInputError("Orden no soportado")
        is_manager = await self.managers.is_manager(principal)
        if mine:
            filters = replace(filters, assignee_user_id=principal.user_id, unassigned=False)
        if not is_manager:
            filters = replace(filters, visible_to=principal.user_id)
        return filters, is_manager

    async def list_cases(
        self, filters: CaseFilters, page: int, size: int, principal: Principal, mine: bool = False
    ) -> CaseViewPage:
        """Return one page of the inbox visible to the caller.

        Args:
            filters (CaseFilters): Inbox filters.
            page (int): 1-based page.
            size (int): Page size (max 100).
            principal (Principal): The caller.
            mine (bool): Only cases assigned to the caller.

        Returns:
            CaseViewPage: The page.
        """

        scoped, is_manager = await self._scoped(filters, principal, mine)
        result = await self.repository.list_cases(
            scoped, max(1, page), min(max(1, size), 100), self.calendar.today()
        )
        return CaseViewPage(
            items=await self._views(result.items, principal, is_manager),
            total=result.total,
            page=result.page,
            size=result.size,
        )

    async def summary(
        self, filters: CaseFilters, principal: Principal, mine: bool = False
    ) -> CaseSummary:
        """Count the visible cases for the inbox indicators.

        Args:
            filters (CaseFilters): Inbox filters.
            principal (Principal): The caller.
            mine (bool): Only cases assigned to the caller.

        Returns:
            CaseSummary: Totals by status and type, overdue and unassigned.
        """

        scoped, _ = await self._scoped(filters, principal, mine)
        return await self.repository.summarize(scoped, self.calendar.today())

    async def get_case(self, case_id: UUID, principal: Principal) -> CaseDetail:
        """Return a case with its timeline and intake record.

        Args:
            case_id (UUID): Case identifier.
            principal (Principal): The caller.

        Returns:
            CaseDetail: The case, its events and the public request if any.
        """

        case, _, is_manager = await self._load(case_id, principal)
        events = await self.repository.list_events(case.id)
        actors = await self.people.get_users(
            {e.actor_user_id for e in events if e.actor_user_id is not None}
        )
        request = await self.requests.get_request(case.request_id) if case.request_id else None
        return CaseDetail(
            view=await self._view(case, principal, is_manager),
            events=[
                EventView(e, actors.get(e.actor_user_id) if e.actor_user_id else None)
                for e in events
            ],
            request=request,
        )

    async def request_for_download(self, case_id: UUID, principal: Principal) -> UUID:
        """Authorize reading the intake attachments of a case.

        Args:
            case_id (UUID): Case identifier.
            principal (Principal): The caller.

        Returns:
            UUID: The public request whose attachments may be read.
        """

        case, _, _ = await self._load(case_id, principal)
        if case.request_id is None:
            raise NotFoundError("El caso no tiene una solicitud pública con adjuntos")
        return case.request_id

    async def suggest(self, field: str, query: str, principal: Principal) -> list[str]:
        """Offer values typed before in a free-text field (customer, supplier...).

        Args:
            field (str): One of customer, supplier, sales_executive, requester.
            query (str): Text the value contains.
            principal (Principal): The caller (any authenticated user).

        Returns:
            list[str]: Up to ten distinct values.
        """

        if field not in SUGGESTION_FIELDS:
            raise InvalidInputError("Campo de sugerencias no soportado")
        return await self.repository.suggest(field, query.strip(), 10)

    async def export_rows(
        self, filters: CaseFilters, principal: Principal, mine: bool = False
    ) -> list[list[str]]:
        """Build the spreadsheet export: one row per requested document.

        Args:
            filters (CaseFilters): Inbox filters.
            principal (Principal): The caller.
            mine (bool): Only cases assigned to the caller.

        Returns:
            list[list[str]]: Header followed by the rows, P.13.F.01 column order.
        """

        scoped, _ = await self._scoped(filters, principal, mine)
        today = self.calendar.today()
        cases: list[TrackingCase] = []
        page = 1
        while len(cases) < EXPORT_LIMIT:
            result = await self.repository.list_cases(scoped, page, 100, today)
            cases.extend(result.items)
            if page * 100 >= result.total:
                break
            page += 1
        users = await self.people.get_users(
            {c.assignee_user_id for c in cases if c.assignee_user_id is not None}
        )
        rows = [list(EXPORT_HEADER)]
        for case in cases[:EXPORT_LIMIT]:
            assignee = users.get(case.assignee_user_id) if case.assignee_user_id else None
            responsible = (assignee.display_name or assignee.email or "") if assignee else ""
            if not responsible and case.legacy:
                responsible = str(case.legacy.get("responsable") or "")
            header = [
                case.received_on.isoformat(),
                case.code,
                case.request_type.name,
                responsible,
                case.supplier_name or "",
                case.dependency.name if case.dependency else "",
                case.requester_name or "",
                case.customer_name or "",
                case.customer_nit or "",
                case.sales_executive_name or "",
            ]
            for item in case.items or [None]:
                rows.append(
                    header
                    + [
                        _item_name(item),
                        _text(item.quantity if item else None),
                        case.site.name if case.site else "",
                        _text(
                            item.responded_on.isoformat() if item and item.responded_on else None
                        ),
                        _text(item.response_days if item else None),
                        _text(item.sla_days if item else None),
                        _text(item.difference_days if item else None),
                        _text((item.due_on if item else case.due_on) or None),
                        case.status.name,
                        (item.delay_cause or "") if item else "",
                        (item.delay_detail or "") if item else "",
                    ]
                )
        return rows

    # --- Working a case ---

    async def update_case(
        self, case_id: UUID, changes: CaseChanges, principal: Principal
    ) -> CaseView:
        """Edit the case header.

        Args:
            case_id (UUID): Case identifier.
            changes (CaseChanges): Fields to apply.
            principal (Principal): The responsible or a manager.

        Returns:
            CaseView: The updated case.
        """

        case = await self._load_for_work(case_id, principal)
        if case.is_closed:
            raise ConflictError(_CLOSED_MESSAGE)
        fields = changes.fields
        changed: list[str] = []

        def apply(name: str, value: object) -> None:
            if name in fields and getattr(case, name) != value:
                setattr(case, name, value)
                changed.append(name)

        apply("subject", clean_optional(changes.subject, "asunto", 200))
        apply("description", clean_optional(changes.description, "descripción", 10_000))
        apply("customer_name", clean_optional(changes.customer_name, "cliente", 200))
        apply("customer_nit", clean_optional(changes.customer_nit, "NIT", 30))
        apply("supplier_name", clean_optional(changes.supplier_name, "proveedor", 200))
        apply(
            "sales_executive_name",
            clean_optional(changes.sales_executive_name, "ejecutivo comercial", 200),
        )
        apply("requester_name", clean_optional(changes.requester_name, "usuario", 200))
        apply("requester_email", _clean_email(changes.requester_email))
        if "site_id" in fields:
            apply("site", await self._site(changes.site_id))
        if "dependency_id" in fields:
            apply("dependency", await self._dependency(changes.dependency_id))
        if "received_on" in fields and changes.received_on is not None:
            if changes.received_on > self.calendar.today():
                raise InvalidInputError("La fecha de recepción no puede ser futura")
            if changes.received_on.year != case.year:
                raise InvalidInputError(
                    "La fecha de recepción debe quedar en el mismo año del consecutivo"
                )
            for item in case.items:
                if item.responded_on and item.responded_on < changes.received_on:
                    raise InvalidInputError(
                        "Hay documentos respondidos antes de esa fecha de recepción"
                    )
            apply("received_on", changes.received_on)
        if not changed:
            return await self._view(case, principal, await self.managers.is_manager(principal))
        self._refresh_dates(case)
        event = self._event(case.id, EventType.UPDATED, principal.user_id, data={"fields": changed})
        stored = await self.repository.save_case(case, [event])
        return await self._view(stored, principal, await self.managers.is_manager(principal))

    async def assign(
        self, case_id: UUID, assignee_user_id: UUID | None, note: str | None, principal: Principal
    ) -> CaseView:
        """Make someone responsible for the case, or leave it unassigned.

        Args:
            case_id (UUID): Case identifier.
            assignee_user_id (UUID | None): New responsible, ``None`` to unassign.
            note (str | None): Optional message for the timeline.
            principal (Principal): The caller, who must manage PQRSF.

        Returns:
            CaseView: The updated case.
        """

        await self._require_manager(principal)
        case, _, _ = await self._load(case_id, principal)
        if case.is_closed:
            raise ConflictError(_CLOSED_MESSAGE)
        if assignee_user_id is not None:
            await self._assignee(assignee_user_id)
        if case.assignee_user_id == assignee_user_id:
            return await self._view(case, principal, True)
        previous = case.assignee_user_id
        case.assignee_user_id = assignee_user_id
        # A new responsible has not read it yet.
        case.first_read_at = None
        # Names are kept as they were, so the timeline reads right after reassignments.
        people = await self.people.get_users(
            i for i in (previous, assignee_user_id) if i is not None
        )
        event = self._event(
            case.id,
            EventType.ASSIGNED,
            principal.user_id,
            note=clean_optional(note, "nota", 2_000),
            data={
                "from_user_id": _str(previous),
                "from_name": _person_name(people.get(previous) if previous else None),
                "to_user_id": _str(assignee_user_id),
                "to_name": _person_name(people.get(assignee_user_id) if assignee_user_id else None),
            },
        )
        stored = await self.repository.save_case(case, [event])
        if assignee_user_id is not None:
            await self._notify_assigned(stored, people.get(assignee_user_id), principal, note)
        return await self._view(stored, principal, True)

    async def _notify_assigned(
        self,
        case: TrackingCase,
        assignee: DirectoryUser | None,
        actor: Principal,
        note: str | None,
    ) -> None:
        """Tell the new responsible, unless they assigned the case to themselves.

        The case is already saved: a failure to queue the email is logged and never
        undoes or blocks the assignment.
        """

        if self.notifier is None or assignee is None or assignee.id == actor.user_id:
            return
        try:
            await self.notifier.case_assigned(
                case, assignee, actor, clean_optional(note, "nota", 2_000)
            )
        except Exception:
            logger.exception("Could not queue the assignment notice of case %s", case.code)

    async def change_status(
        self, case_id: UUID, status_id: UUID, note: str | None, principal: Principal
    ) -> CaseView:
        """Move the case to another status.

        Closing requires every document to have a response date and a delay
        cause when it was late; reopening a closed case requires a note.

        Args:
            case_id (UUID): Case identifier.
            status_id (UUID): Target status.
            note (str | None): Message for the timeline.
            principal (Principal): The responsible or a manager.

        Returns:
            CaseView: The updated case.
        """

        case = await self._load_for_work(case_id, principal)
        target = await self.repository.get_status(status_id)
        if target is None or not target.is_active:
            raise NotFoundError("Estado no encontrado")
        if target.id == case.status.id:
            raise InvalidInputError("El caso ya está en ese estado")
        note_value = clean_optional(note, "nota", 2_000)
        if target.is_terminal:
            pending = [i for i in case.items if i.responded_on is None]
            if pending:
                raise InvalidInputError(
                    f"Faltan {len(pending)} documento(s) por responder antes de finalizar"
                )
            if any(i.is_late and not i.delay_cause for i in case.items):
                raise InvalidInputError("Indica la causal de los documentos respondidos tarde")
            case.closed_at = datetime.now(UTC)
        elif case.is_closed:
            if note_value is None:
                raise InvalidInputError("Explica en la nota por qué se reabre el caso")
            case.closed_at = None
        previous = case.status
        case.status = target
        event = self._event(
            case.id,
            EventType.STATUS_CHANGED,
            principal.user_id,
            note=note_value,
            from_status=previous.id,
            to_status=target.id,
        )
        stored = await self.repository.save_case(case, [event])
        return await self._view(stored, principal, await self.managers.is_manager(principal))

    async def mark_read(self, case_id: UUID, principal: Principal) -> CaseView:
        """Record that someone opened the case.

        Opening the case moves it from the initial status to ``leida`` (when that status
        exists and is active) if the caller can work it: its responsible or a manager.
        Someone who can only see the case (who raised it) does not change its status,
        and every other status change stays manual. ``first_read_at`` is the
        responsible's own first opening, so a manager reading it first does not take it
        over. Any opening that changes nothing leaves a ``viewed`` access record (one per
        person and case every 30 minutes) so the history shows who saw the case and when.

        Args:
            case_id (UUID): Case identifier.
            principal (Principal): The caller.

        Returns:
            CaseView: The case after the change.
        """

        case, access, is_manager = await self._load(case_id, principal)
        first_by_responsible = (
            case.assignee_user_id == principal.user_id and case.first_read_at is None
        )
        read_status = (
            await self._active_status_by_code(READ_STATUS_CODE)
            if case.status.is_initial and access.can_work
            else None
        )
        if not first_by_responsible and read_status is None:
            await self._record_access(EventType.VIEWED, principal, case.id)
            return await self._view(case, principal, is_manager)
        if first_by_responsible:
            case.first_read_at = datetime.now(UTC)
        if read_status is not None:
            event = self._event(
                case.id,
                EventType.READ,
                principal.user_id,
                from_status=case.status.id,
                to_status=read_status.id,
            )
            case.status = read_status
        else:
            event = self._event(case.id, EventType.READ, principal.user_id)
        stored = await self.repository.save_case(case, [event])
        return await self._view(stored, principal, is_manager)

    async def require_work(self, case_id: UUID, principal: Principal) -> TrackingCase:
        """Return the case when the caller may work it (its responsible or a manager)."""

        return await self._load_for_work(case_id, principal)

    async def add_comment(
        self, case_id: UUID, body: str, principal: Principal, data: dict | None = None
    ) -> EventView:
        """Write in the case timeline.

        Args:
            case_id (UUID): Case identifier.
            body (str): Comment text.
            principal (Principal): Anyone who can see the case.
            data (dict | None): Extra detail kept with the entry (files, client reply).

        Returns:
            EventView: The new timeline entry.
        """

        case, _, _ = await self._load(case_id, principal)
        text = clean_multiline(body, "comentario", 5_000)
        if text is None:
            raise InvalidInputError("El comentario no puede estar vacío")
        event = self._event(case.id, EventType.COMMENT, principal.user_id, note=text, data=data)
        await self.repository.add_event(event)
        actor = (await self.people.get_users([principal.user_id])).get(principal.user_id)
        return EventView(event, actor)

    # --- History ---

    async def _record_access(
        self, event_type: str, principal: Principal, case_id: UUID | None
    ) -> None:
        now = datetime.now(UTC)
        if await self.repository.has_recent_access(
            event_type, principal.user_id, case_id, now - ACCESS_REPEAT_WINDOW
        ):
            return
        await self.repository.add_event(self._event(case_id, event_type, principal.user_id))

    async def record_inbox_visit(self, principal: Principal) -> None:
        """Record that the person opened the PQRSF inbox.

        One record per person every 30 minutes; each one also purges the access
        records past the 90-day retention, so the table stays bounded without a
        scheduler. Only access records are ever purged.

        Args:
            principal (Principal): The caller.
        """

        await self._record_access(EventType.INBOX_OPENED, principal, None)
        await self.repository.purge_access_events(datetime.now(UTC) - ACCESS_RETENTION)

    async def delete_case(self, case_id: UUID, reason: str | None, principal: Principal) -> None:
        """Delete a case (managers). Its history stays, with who deleted it and why.

        Args:
            case_id (UUID): Case identifier.
            reason (str | None): Why it is deleted, kept in the history.
            principal (Principal): The caller.
        """

        await self._require_manager(principal)
        case, _, _ = await self._load(case_id, principal)
        event = self._event(
            case.id,
            EventType.DELETED,
            principal.user_id,
            note=clean_optional(reason, "motivo", 2_000),
            data={
                "request_type": case.request_type.name,
                "status": case.status.name,
                "customer_name": case.customer_name,
                "received_on": case.received_on.isoformat(),
            },
        )
        event.case_code = case.code
        await self.repository.delete_case(case.id, event)

    async def list_activity(self, filters: ActivityFilters, principal: Principal) -> ActivityPage:
        """History of the whole module, newest first (managers).

        Args:
            filters (ActivityFilters): Cursor, size and filters.
            principal (Principal): The caller.

        Returns:
            ActivityPage: The entries and the cursor of the next page.
        """

        await self._require_manager(principal)
        limit = max(1, min(filters.limit, 100))
        events = await self.repository.list_activity(replace(filters, limit=limit + 1))
        page, more = events[:limit], len(events) > limit
        actors = await self.people.get_users(
            {e.actor_user_id for e in page if e.actor_user_id is not None}
        )
        return ActivityPage(
            items=[
                EventView(e, actors.get(e.actor_user_id) if e.actor_user_id else None) for e in page
            ],
            next_before=page[-1].created_at if more and page else None,
        )

    async def last_seen(self, principal: Principal) -> list[LastSeenView]:
        """Who opened the inbox within the retention window, and when (managers).

        Args:
            principal (Principal): The caller.

        Returns:
            list[LastSeenView]: One row per person, most recent first.
        """

        await self._require_manager(principal)
        rows = await self.repository.last_seen(datetime.now(UTC) - ACCESS_RETENTION)
        users = await self.people.get_users(row.user_id for row in rows)
        return [LastSeenView(users.get(row.user_id), row.user_id, row.last_seen_at) for row in rows]

    async def _start_progress(self, case: TrackingCase, actor: UUID, events: list) -> None:
        """Move a case that is still received/read to ``en_curso`` on first work."""

        if case.status.is_terminal:
            return
        target = await self._active_status_by_code(IN_PROGRESS_STATUS_CODE)
        if (
            target is None
            or target.id == case.status.id
            or case.status.sort_order >= target.sort_order
        ):
            return
        events.append(
            self._event(
                case.id,
                EventType.STATUS_CHANGED,
                actor,
                note="Cambio automático al registrar la primera respuesta",
                from_status=case.status.id,
                to_status=target.id,
            )
        )
        case.status = target

    async def add_item(self, case_id: UUID, value: ItemInput, principal: Principal) -> CaseView:
        """Add a requested document to the case.

        Args:
            case_id (UUID): Case identifier.
            value (ItemInput): Document type, description and quantity.
            principal (Principal): The responsible or a manager.

        Returns:
            CaseView: The updated case.
        """

        case = await self._load_for_work(case_id, principal)
        if case.is_closed:
            raise ConflictError(_CLOSED_MESSAGE)
        if len(case.items) >= _ITEM_LIMIT:
            raise InvalidInputError(f"Un caso admite máximo {_ITEM_LIMIT} documentos")
        position = max((i.sort_order for i in case.items), default=-1) + 1
        item = await self._new_item(case.id, value, position)
        case.items.append(item)
        self._refresh_dates(case)
        event = self._event(
            case.id,
            EventType.ITEM_ADDED,
            principal.user_id,
            data={"item_id": str(item.id), "document": _item_name(item)},
        )
        await self.repository.save_case(case, [event])
        return await self._reload_view(case.id, principal)

    def today(self) -> date:
        """Today in the business time zone."""

        return self.calendar.today()

    async def update_item(
        self, case_id: UUID, item_id: UUID, changes: ItemChanges, principal: Principal
    ) -> CaseView:
        """Edit a document or register its response.

        A response later than the agreed time requires a delay cause. The
        first response moves the case to ``en_curso`` when it was earlier.

        Args:
            case_id (UUID): Case identifier.
            item_id (UUID): Item identifier.
            changes (ItemChanges): Fields to apply.
            principal (Principal): The responsible or a manager.

        Returns:
            CaseView: The updated case.
        """

        case = await self._load_for_work(case_id, principal)
        if case.is_closed:
            raise ConflictError(_CLOSED_MESSAGE)
        item = next((i for i in case.items if i.id == item_id), None)
        if item is None:
            raise NotFoundError("Documento no encontrado en el caso")
        fields = changes.fields
        was_responded = item.responded_on is not None
        if "document_type_id" in fields:
            if changes.document_type_id is None:
                item.document_type = None
                item.sla_days = None
            else:
                document_type = await self.repository.get_document_type(changes.document_type_id)
                if document_type is None:
                    raise NotFoundError("Tipo de documento no encontrado")
                if item.document_type is None or item.document_type.id != document_type.id:
                    item.document_type = document_type
                    item.sla_days = document_type.sla_days
        if "description" in fields:
            item.description = clean_optional(changes.description, "descripción del documento", 300)
        if item.document_type is None and item.description is None:
            raise InvalidInputError("Indica el tipo de documento o una descripción")
        if "quantity" in fields:
            item.quantity = validate_quantity(changes.quantity)
        if "responded_on" in fields:
            if changes.responded_on is not None:
                if changes.responded_on < case.received_on:
                    raise InvalidInputError("La respuesta no puede ser anterior a la recepción")
                if changes.responded_on > self.calendar.today():
                    raise InvalidInputError("La fecha de respuesta no puede ser futura")
            item.responded_on = changes.responded_on
        if "delay_cause" in fields:
            item.delay_cause = clean_optional(changes.delay_cause, "causal", 300)
        if "delay_detail" in fields:
            item.delay_detail = clean_optional(changes.delay_detail, "detalle", 2_000)
        self._refresh_dates(case)
        if item.is_late and not item.delay_cause:
            raise InvalidInputError(
                f"La respuesta tomó {item.response_days} días hábiles y el tiempo establecido "
                f"es {item.sla_days}: indica la causal del retraso"
            )
        item.updated_at = datetime.now(UTC)
        events = [
            self._event(
                case.id,
                EventType.ITEM_UPDATED,
                principal.user_id,
                data={
                    "item_id": str(item.id),
                    "document": _item_name(item),
                    "fields": sorted(fields),
                    "responded_on": _str(item.responded_on),
                },
            )
        ]
        if not was_responded and item.responded_on is not None:
            await self._start_progress(case, principal.user_id, events)
        await self.repository.save_case(case, events)
        return await self._reload_view(case.id, principal)

    async def remove_item(self, case_id: UUID, item_id: UUID, principal: Principal) -> CaseView:
        """Remove a document from the case.

        Args:
            case_id (UUID): Case identifier.
            item_id (UUID): Item identifier.
            principal (Principal): The responsible or a manager.

        Returns:
            CaseView: The updated case.
        """

        case = await self._load_for_work(case_id, principal)
        if case.is_closed:
            raise ConflictError(_CLOSED_MESSAGE)
        item = next((i for i in case.items if i.id == item_id), None)
        if item is None:
            raise NotFoundError("Documento no encontrado en el caso")
        case.items = [i for i in case.items if i.id != item_id]
        self._refresh_dates(case)
        event = self._event(
            case.id,
            EventType.ITEM_REMOVED,
            principal.user_id,
            data={"item_id": str(item.id), "document": _item_name(item)},
        )
        await self.repository.save_case(case, [event])
        return await self._reload_view(case.id, principal)

    async def _reload_view(self, case_id: UUID, principal: Principal) -> CaseView:
        case, _, is_manager = await self._load(case_id, principal)
        return await self._view(case, principal, is_manager)

    # --- Import ---

    async def import_case(self, command: ImportedCase) -> TrackingCase | None:
        """Load a historical case from the spreadsheet; reruns skip loaded rows.

        Historical data is kept as found: late answers without a cause are
        accepted and the agreed time comes from the sheet, not the catalog.

        Args:
            command (ImportedCase): Case assembled by the import script.

        Returns:
            TrackingCase | None: The stored case, or ``None`` when already loaded.
        """

        if await self.repository.legacy_key_exists(command.source_key):
            return None
        request_type = await self.requests.get_request_type(command.request_type_id)
        if request_type is None:
            raise NotFoundError("Tipo de solicitud no encontrado")
        status = await self.repository.get_status_by_code(command.status_code)
        if status is None:
            raise NotFoundError(f"Estado {command.status_code} no encontrado")
        case = self._blank_case(request_type, status, command.received_on, "import", None)
        case.assignee_user_id = command.assignee_user_id
        case.dependency = await self._dependency(command.dependency_id)
        case.site = await self._site(command.site_id)
        case.customer_name = command.customer_name
        case.supplier_name = command.supplier_name
        case.sales_executive_name = command.sales_executive_name
        case.requester_name = command.requester_name
        case.legacy = {**command.legacy, "source_key": command.source_key}
        now = datetime.now(UTC)
        for position, value in enumerate(command.items):
            document_type = (
                await self.repository.get_document_type(value.document_type_id)
                if value.document_type_id
                else None
            )
            case.items.append(
                CaseItem(
                    id=uuid4(),
                    case_id=case.id,
                    document_type=document_type,
                    description=value.description,
                    quantity=value.quantity,
                    sla_days=value.sla_days,
                    due_on=None,
                    responded_on=value.responded_on,
                    response_days=None,
                    delay_cause=value.delay_cause,
                    delay_detail=value.delay_detail,
                    sort_order=position,
                    created_at=now,
                    updated_at=now,
                )
            )
        self._refresh_dates(case)
        if status.is_terminal:
            responded = [i.responded_on for i in case.items if i.responded_on]
            closed_on = max(responded) if responded else case.received_on
            case.closed_at = datetime(closed_on.year, closed_on.month, closed_on.day, tzinfo=UTC)
        event = self._event(case.id, EventType.CREATED, None, data={"origin": "import"})
        return await self.repository.create_case(case, event, number=command.number)


def _clean_name(value: str, max_length: int = 120) -> str:
    cleaned = clean_optional(value, "nombre", max_length)
    if cleaned is None:
        raise InvalidInputError("El nombre no puede estar vacío")
    return cleaned


def _clean_email(value: str | None) -> str | None:
    email = clean_optional(value, "correo", 320)
    if email is None:
        return None
    if "@" not in email or " " in email:
        raise InvalidInputError("El correo no es válido")
    return email.lower()


def _item_name(item: CaseItem | None) -> str:
    if item is None:
        return ""
    if item.document_type is not None:
        return item.document_type.name
    return item.description or ""


def _person_name(user: DirectoryUser | None) -> str | None:
    if user is None:
        return None
    return user.display_name or user.email


def _str(value: object) -> str | None:
    return None if value is None else str(value)


def _text(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, date):
        return value.isoformat()
    return str(value)
