"""PostgreSQL repository for PQRSF follow-up cases and their catalogs."""

from __future__ import annotations

from datetime import date, datetime
from uuid import UUID

from sqlalchemy import Select, and_, delete, func, or_, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.core.errors import ConflictError
from app.infrastructure.database import Database
from app.modules.pqrsf.domain.tracking import (
    ACCESS_EVENT_TYPES,
    ActivityFilters,
    CaseEvent,
    CaseFilters,
    CaseItem,
    CasePage,
    CaseProduct,
    CaseStatus,
    CaseSummary,
    CatalogRef,
    CountBucket,
    Dependency,
    DocumentType,
    EventType,
    LastSeen,
    TrackingCase,
    case_code,
    case_series,
)
from app.modules.pqrsf.infrastructure.models import (
    CaseEventModel,
    CaseItemModel,
    CaseModel,
    CaseProductModel,
    CaseSequenceModel,
    CaseStatusModel,
    CauseModel,
    DependencyModel,
    DocumentTypeModel,
    RequestTypeModel,
    SubjectModel,
)
from app.modules.pqrsf.infrastructure.repository import request_type_from_model
from app.modules.sites.domain.entities import Site
from app.modules.sites.infrastructure.models import SiteModel

_SUGGESTION_COLUMNS = {
    "customer": CaseModel.customer_name,
    "supplier": CaseModel.supplier_name,
    "sales_executive": CaseModel.sales_executive_name,
    "requester": CaseModel.requester_name,
}
_SORTS = {
    "received_on": (CaseModel.received_on.asc(), CaseModel.number.asc()),
    "-received_on": (CaseModel.received_on.desc(), CaseModel.number.desc()),
    "due_on": (CaseModel.due_on.asc().nulls_last(), CaseModel.received_on.asc()),
    "-due_on": (CaseModel.due_on.desc().nulls_last(), CaseModel.received_on.desc()),
    "code": (CaseModel.code.asc(),),
    "-code": (CaseModel.code.desc(),),
    "updated_at": (CaseModel.updated_at.asc(),),
    "-updated_at": (CaseModel.updated_at.desc(),),
}
_HEADER_FIELDS = (
    "subject",
    "description",
    "received_on",
    "due_on",
    "assignee_user_id",
    "customer_name",
    "customer_nit",
    "supplier_name",
    "sales_executive_name",
    "sales_executive_code",
    "sales_executive_email",
    "sales_executive_user_id",
    "sales_executive_source",
    "requester_name",
    "requester_email",
    "first_read_at",
    "closed_at",
    "legacy",
    "route",
    "pickup_order_requested",
    "pickup_order_number",
    "delivery_date",
    "resolution",
    "internal_failure",
    "resolution_note",
    "resolved_at",
)
_ITEM_FIELDS = (
    "description",
    "quantity",
    "sla_days",
    "due_on",
    "responded_on",
    "response_days",
    "delay_cause",
    "delay_detail",
    "sort_order",
)


def _status(model: CaseStatusModel) -> CaseStatus:
    return CaseStatus(
        id=model.id,
        code=model.code,
        name=model.name,
        color=model.color,
        sort_order=model.sort_order,
        is_initial=model.is_initial,
        is_terminal=model.is_terminal,
        is_active=model.is_active,
        created_at=model.created_at,
        updated_at=model.updated_at,
    )


def _dependency(model: DependencyModel) -> Dependency:
    return Dependency(
        id=model.id,
        code=model.code,
        name=model.name,
        sort_order=model.sort_order,
        is_active=model.is_active,
        created_at=model.created_at,
        updated_at=model.updated_at,
    )


def _document_type(model: DocumentTypeModel) -> DocumentType:
    return DocumentType(
        id=model.id,
        name=model.name,
        sla_days=model.sla_days,
        is_active=model.is_active,
        created_at=model.created_at,
        updated_at=model.updated_at,
        allows_direct_upload=model.allows_direct_upload,
    )


def _site(model: SiteModel) -> Site:
    return Site(
        id=model.id,
        code=model.code,
        name=model.name,
        is_active=model.is_active,
        created_at=model.created_at,
        updated_at=model.updated_at,
        abbreviation=model.abbreviation,
        city=model.city,
        address=model.address,
        om_code=model.om_code,
    )


def _item(model: CaseItemModel, document_type: DocumentTypeModel | None) -> CaseItem:
    return CaseItem(
        id=model.id,
        case_id=model.case_id,
        document_type=_document_type(document_type) if document_type else None,
        description=model.description,
        quantity=model.quantity,
        sla_days=model.sla_days,
        due_on=model.due_on,
        responded_on=model.responded_on,
        response_days=model.response_days,
        delay_cause=model.delay_cause,
        delay_detail=model.delay_detail,
        sort_order=model.sort_order,
        created_at=model.created_at,
        updated_at=model.updated_at,
    )


def _event(model: CaseEventModel) -> CaseEvent:
    return CaseEvent(
        id=model.id,
        case_id=model.case_id,
        event_type=model.event_type,
        actor_user_id=model.actor_user_id,
        from_status_id=model.from_status_id,
        to_status_id=model.to_status_id,
        note=model.note,
        data=dict(model.data or {}),
        created_at=model.created_at,
        case_code=model.case_code,
    )


def _ref(model: DependencyModel | SubjectModel | CauseModel | None) -> CatalogRef | None:
    return CatalogRef(id=model.id, code=model.code, name=model.name) if model else None


def _product(model: CaseProductModel) -> CaseProduct:
    return CaseProduct(
        id=model.id,
        case_id=model.case_id,
        source=model.source,
        invoice_number=model.invoice_number,
        sku=model.sku,
        lot=model.lot,
        expiry_date=model.expiry_date,
        affected_quantity=model.affected_quantity,
        description=model.description,
        supplier_code=model.supplier_code,
        supplier_name=model.supplier_name,
        sort_order=model.sort_order,
    )


def _product_model(product: CaseProduct) -> CaseProductModel:
    return CaseProductModel(
        id=product.id,
        case_id=product.case_id,
        source=product.source,
        invoice_number=product.invoice_number,
        sku=product.sku,
        lot=product.lot,
        expiry_date=product.expiry_date,
        affected_quantity=product.affected_quantity,
        description=product.description,
        supplier_code=product.supplier_code,
        supplier_name=product.supplier_name,
        sort_order=product.sort_order,
    )


def _case(
    model: CaseModel,
    request_type: RequestTypeModel,
    status: CaseStatusModel,
    site: SiteModel | None,
    dependency: DependencyModel | None,
    area: DependencyModel | None = None,
    subject: SubjectModel | None = None,
    cause: CauseModel | None = None,
) -> TrackingCase:
    return TrackingCase(
        requested_area=_ref(area),
        subject_ref=_ref(subject),
        cause_ref=_ref(cause),
        route=model.route,
        pickup_order_requested=model.pickup_order_requested,
        pickup_order_number=model.pickup_order_number,
        delivery_date=model.delivery_date,
        id=model.id,
        code=model.code,
        number=model.number,
        year=model.year,
        series=model.series,
        request_type=request_type_from_model(request_type),
        status=_status(status),
        origin=model.origin,
        subject=model.subject,
        description=model.description,
        received_on=model.received_on,
        due_on=model.due_on,
        site=_site(site) if site else None,
        dependency=_dependency(dependency) if dependency else None,
        assignee_user_id=model.assignee_user_id,
        customer_name=model.customer_name,
        customer_nit=model.customer_nit,
        supplier_name=model.supplier_name,
        sales_executive_name=model.sales_executive_name,
        sales_executive_code=model.sales_executive_code,
        sales_executive_email=model.sales_executive_email,
        sales_executive_user_id=model.sales_executive_user_id,
        sales_executive_source=model.sales_executive_source,
        requester_name=model.requester_name,
        requester_email=model.requester_email,
        request_id=model.request_id,
        created_by_user_id=model.created_by_user_id,
        first_read_at=model.first_read_at,
        news_at=model.news_at,
        resolution=model.resolution,
        internal_failure=model.internal_failure,
        resolution_note=model.resolution_note,
        resolved_at=model.resolved_at,
        om_sent_at=model.om_sent_at,
        closed_at=model.closed_at,
        legacy=dict(model.legacy) if model.legacy is not None else None,
        created_at=model.created_at,
        updated_at=model.updated_at,
    )


def _event_model(event: CaseEvent) -> CaseEventModel:
    # The consecutive is copied in so the entry still reads after a deletion;
    # when the caller did not give it, it is taken from the case at insert.
    case_code = event.case_code
    if case_code is None and event.case_id is not None:
        case_code = select(CaseModel.code).where(CaseModel.id == event.case_id).scalar_subquery()
    return CaseEventModel(
        id=event.id,
        case_id=event.case_id,
        case_code=case_code,
        event_type=event.event_type,
        actor_user_id=event.actor_user_id,
        from_status_id=event.from_status_id,
        to_status_id=event.to_status_id,
        note=event.note,
        data=event.data,
        created_at=event.created_at,
    )


def _set_classification(model: CaseModel, case: TrackingCase) -> None:
    model.requested_area_id = case.requested_area.id if case.requested_area else None
    model.subject_id = case.subject_ref.id if case.subject_ref else None
    model.cause_id = case.cause_ref.id if case.cause_ref else None


def _item_model(item: CaseItem) -> CaseItemModel:
    model = CaseItemModel(
        id=item.id,
        case_id=item.case_id,
        document_type_id=item.document_type.id if item.document_type else None,
    )
    for name in _ITEM_FIELDS:
        setattr(model, name, getattr(item, name))
    return model


_AREA = aliased(DependencyModel)
_CASE_QUERY: Select = (
    select(
        CaseModel,
        RequestTypeModel,
        CaseStatusModel,
        SiteModel,
        DependencyModel,
        _AREA,
        SubjectModel,
        CauseModel,
    )
    .join(RequestTypeModel, CaseModel.request_type_id == RequestTypeModel.id)
    .join(CaseStatusModel, CaseModel.status_id == CaseStatusModel.id)
    .outerjoin(SiteModel, CaseModel.site_id == SiteModel.id)
    .outerjoin(DependencyModel, CaseModel.dependency_id == DependencyModel.id)
    .outerjoin(_AREA, CaseModel.requested_area_id == _AREA.id)
    .outerjoin(SubjectModel, CaseModel.subject_id == SubjectModel.id)
    .outerjoin(CauseModel, CaseModel.cause_id == CauseModel.id)
)


def _conditions(filters: CaseFilters, today: date) -> list:
    conditions: list = []
    if filters.status_ids:
        conditions.append(CaseModel.status_id.in_(filters.status_ids))
    if filters.request_type_id:
        conditions.append(CaseModel.request_type_id == filters.request_type_id)
    if filters.unassigned:
        conditions.append(CaseModel.assignee_user_id.is_(None))
    elif filters.assignee_user_id:
        conditions.append(CaseModel.assignee_user_id == filters.assignee_user_id)
    if filters.dependency_id:
        conditions.append(CaseModel.dependency_id == filters.dependency_id)
    if filters.site_id:
        conditions.append(CaseModel.site_id == filters.site_id)
    if filters.open_only or filters.overdue or filters.due_today:
        conditions.append(CaseStatusModel.is_terminal.is_(False))
    if filters.overdue:
        conditions.append(CaseModel.due_on < today)
    if filters.due_today:
        conditions.append(CaseModel.due_on == today)
    if filters.with_news:
        conditions.append(CaseModel.news_at.is_not(None))
    if filters.resolution:
        conditions.append(CaseModel.resolution == filters.resolution)
    if filters.internal_failure is not None:
        conditions.append(CaseModel.internal_failure.is_(filters.internal_failure))
    if filters.om_sent is not None:
        conditions.append(
            CaseModel.om_sent_at.is_not(None) if filters.om_sent else CaseModel.om_sent_at.is_(None)
        )
    if filters.received_from:
        conditions.append(CaseModel.received_on >= filters.received_from)
    if filters.received_to:
        conditions.append(CaseModel.received_on <= filters.received_to)
    if filters.query:
        query = filters.query.strip()
        pattern = f"%{query}%"
        # Quality still quotes the P.13.F.01 number (2718); only a whole-number
        # match, so "27" does not bring every case whose sheet number has it.
        excel_number = (
            [CaseModel.legacy["original_consecutive"].astext == str(int(query))]
            if query.isdigit()
            else []
        )
        conditions.append(
            or_(
                *excel_number,
                CaseModel.code.ilike(pattern),
                # The code a case had before the per-site renumbering.
                CaseModel.legacy["previous_code"].astext.ilike(pattern),
                CaseModel.customer_name.ilike(pattern),
                CaseModel.customer_nit.ilike(pattern),
                CaseModel.supplier_name.ilike(pattern),
                CaseModel.sales_executive_name.ilike(pattern),
                CaseModel.requester_name.ilike(pattern),
                CaseModel.subject.ilike(pattern),
            )
        )
    if filters.visible_to:
        conditions.append(
            or_(
                CaseModel.assignee_user_id == filters.visible_to,
                CaseModel.created_by_user_id == filters.visible_to,
                CaseModel.sales_executive_user_id == filters.visible_to,
            )
        )
    return conditions


class SqlAlchemyCaseRepository:
    """Persist cases, items and timeline with short-lived transactions."""

    def __init__(self, database: Database) -> None:
        """Create the repository.

        Args:
            database (Database): Shared database resource.
        """

        self.database = database

    # --- Catalogs ---

    async def list_statuses(self, include_inactive: bool = False) -> list[CaseStatus]:
        """List statuses in display order.

        Args:
            include_inactive (bool): Include disabled statuses.

        Returns:
            list[CaseStatus]: Statuses ordered by position and name.
        """

        query = select(CaseStatusModel).order_by(CaseStatusModel.sort_order, CaseStatusModel.name)
        if not include_inactive:
            query = query.where(CaseStatusModel.is_active.is_(True))
        async with self.database.session_factory() as session:
            return [_status(m) for m in (await session.scalars(query)).all()]

    async def get_status(self, status_id: UUID) -> CaseStatus | None:
        """Find a status by identifier.

        Args:
            status_id (UUID): Status identifier.

        Returns:
            CaseStatus | None: The status when it exists.
        """

        async with self.database.session_factory() as session:
            model = await session.get(CaseStatusModel, status_id)
            return _status(model) if model else None

    async def get_status_by_code(self, code: str) -> CaseStatus | None:
        """Find a status by code.

        Args:
            code (str): Status code.

        Returns:
            CaseStatus | None: The status when it exists.
        """

        async with self.database.session_factory() as session:
            model = await session.scalar(
                select(CaseStatusModel).where(CaseStatusModel.code == code)
            )
            return _status(model) if model else None

    async def save_status(self, status: CaseStatus) -> CaseStatus:
        """Insert or update a status; an initial one clears the flag on the rest.

        Args:
            status (CaseStatus): Status to store.

        Returns:
            CaseStatus: The stored status.
        """

        async with self.database.session_factory() as session:
            if status.is_initial:
                await session.execute(
                    update(CaseStatusModel)
                    .where(CaseStatusModel.id != status.id)
                    .values(is_initial=False)
                )
            model = await session.get(CaseStatusModel, status.id)
            if model is None:
                model = CaseStatusModel(id=status.id, code=status.code)
                session.add(model)
            model.name = status.name
            model.color = status.color
            model.sort_order = status.sort_order
            model.is_initial = status.is_initial
            model.is_terminal = status.is_terminal
            model.is_active = status.is_active
            try:
                await session.commit()
            except IntegrityError as exc:
                await session.rollback()
                raise ConflictError("No se pudo guardar el estado") from exc
            await session.refresh(model)
            return _status(model)

    async def list_dependencies(self, include_inactive: bool = False) -> list[Dependency]:
        """List internal dependencies in display order.

        Args:
            include_inactive (bool): Include disabled dependencies.

        Returns:
            list[Dependency]: Dependencies ordered by position and name.
        """

        query = select(DependencyModel).order_by(DependencyModel.sort_order, DependencyModel.name)
        if not include_inactive:
            query = query.where(DependencyModel.is_active.is_(True))
        async with self.database.session_factory() as session:
            return [_dependency(m) for m in (await session.scalars(query)).all()]

    async def get_dependency(self, dependency_id: UUID) -> Dependency | None:
        """Find a dependency by identifier.

        Args:
            dependency_id (UUID): Dependency identifier.

        Returns:
            Dependency | None: The dependency when it exists.
        """

        async with self.database.session_factory() as session:
            model = await session.get(DependencyModel, dependency_id)
            return _dependency(model) if model else None

    async def save_dependency(self, dependency: Dependency) -> Dependency:
        """Insert or update a dependency.

        Args:
            dependency (Dependency): Dependency to store.

        Returns:
            Dependency: The stored dependency.
        """

        async with self.database.session_factory() as session:
            model = await session.get(DependencyModel, dependency.id)
            if model is None:
                model = DependencyModel(id=dependency.id, code=dependency.code)
                session.add(model)
            model.name = dependency.name
            model.sort_order = dependency.sort_order
            model.is_active = dependency.is_active
            try:
                await session.commit()
            except IntegrityError as exc:
                await session.rollback()
                raise ConflictError("Ya existe una dependencia con ese código") from exc
            await session.refresh(model)
            return _dependency(model)

    async def list_document_types(self, include_inactive: bool = False) -> list[DocumentType]:
        """List document types by name.

        Args:
            include_inactive (bool): Include disabled types.

        Returns:
            list[DocumentType]: Document types ordered by name.
        """

        query = select(DocumentTypeModel).order_by(DocumentTypeModel.name)
        if not include_inactive:
            query = query.where(DocumentTypeModel.is_active.is_(True))
        async with self.database.session_factory() as session:
            return [_document_type(m) for m in (await session.scalars(query)).all()]

    async def get_document_type(self, document_type_id: UUID) -> DocumentType | None:
        """Find a document type by identifier.

        Args:
            document_type_id (UUID): Document type identifier.

        Returns:
            DocumentType | None: The type when it exists.
        """

        async with self.database.session_factory() as session:
            model = await session.get(DocumentTypeModel, document_type_id)
            return _document_type(model) if model else None

    async def save_document_type(self, document_type: DocumentType) -> DocumentType:
        """Insert or update a document type.

        Args:
            document_type (DocumentType): Type to store.

        Returns:
            DocumentType: The stored type.
        """

        async with self.database.session_factory() as session:
            model = await session.get(DocumentTypeModel, document_type.id)
            if model is None:
                model = DocumentTypeModel(id=document_type.id)
                session.add(model)
            model.name = document_type.name
            model.sla_days = document_type.sla_days
            model.is_active = document_type.is_active
            model.allows_direct_upload = document_type.allows_direct_upload
            try:
                await session.commit()
            except IntegrityError as exc:
                await session.rollback()
                raise ConflictError("Ya existe un tipo de documento con ese nombre") from exc
            await session.refresh(model)
            return _document_type(model)

    # --- Cases ---

    async def _next_number(
        self,
        session: AsyncSession,
        request_type_id: UUID,
        year: int,
        series: str,
        number: int | None,
    ) -> int:
        """Reserve the consecutive inside the case transaction (row-locked upsert)."""

        table = CaseSequenceModel.__table__
        statement = insert(table).values(
            request_type_id=request_type_id, year=year, series=series, last_value=number or 1
        )
        key = [table.c.request_type_id, table.c.year, table.c.series]
        if number is None:
            statement = statement.on_conflict_do_update(
                index_elements=key,
                set_={"last_value": table.c.last_value + 1},
            )
        else:
            statement = statement.on_conflict_do_update(
                index_elements=key,
                set_={
                    "last_value": func.greatest(table.c.last_value, statement.excluded.last_value)
                },
            )
        allocated = await session.scalar(statement.returning(table.c.last_value))
        return number if number is not None else int(allocated)

    async def create_case(
        self, case: TrackingCase, event: CaseEvent, number: int | None = None
    ) -> TrackingCase:
        """Insert a case with its items, allocating the next consecutive.

        Args:
            case (TrackingCase): Case to insert; its code and number are assigned here.
            event (CaseEvent): The creation event.
            number (int | None): Historical consecutive to keep (imports).

        Returns:
            TrackingCase: The stored case.
        """

        async with self.database.session_factory() as session:
            series = case_series(case.site)
            allocated = await self._next_number(
                session, case.request_type.id, case.year, series, number
            )
            model = CaseModel(
                id=case.id,
                code=case_code(case.request_type.prefix, case.year, series, allocated),
                number=allocated,
                year=case.year,
                series=series,
                request_type_id=case.request_type.id,
                status_id=case.status.id,
                origin=case.origin,
                site_id=case.site.id if case.site else None,
                dependency_id=case.dependency.id if case.dependency else None,
                request_id=case.request_id,
                created_by_user_id=case.created_by_user_id,
            )
            for name in _HEADER_FIELDS:
                setattr(model, name, getattr(case, name))
            _set_classification(model, case)
            session.add(model)
            await session.flush()
            session.add_all([_item_model(item) for item in case.items])
            session.add_all([_product_model(product) for product in case.products])
            session.add(_event_model(event))
            try:
                await session.commit()
            except IntegrityError as exc:
                await session.rollback()
                raise ConflictError("El caso ya existe o su consecutivo está en uso") from exc
        stored = await self.get_case(case.id)
        if stored is None:
            raise ConflictError("El caso no quedó disponible después de guardarlo")
        return stored

    async def _load_items(
        self, session: AsyncSession, case_ids: list[UUID]
    ) -> dict[UUID, list[CaseItem]]:
        if not case_ids:
            return {}
        rows = (
            await session.execute(
                select(CaseItemModel, DocumentTypeModel)
                .outerjoin(
                    DocumentTypeModel, CaseItemModel.document_type_id == DocumentTypeModel.id
                )
                .where(CaseItemModel.case_id.in_(case_ids))
                .order_by(CaseItemModel.case_id, CaseItemModel.sort_order, CaseItemModel.id)
            )
        ).all()
        items: dict[UUID, list[CaseItem]] = {case_id: [] for case_id in case_ids}
        for model, document_type in rows:
            items[model.case_id].append(_item(model, document_type))
        return items

    async def _load_products(
        self, session: AsyncSession, case_ids: list[UUID]
    ) -> dict[UUID, list[CaseProduct]]:
        if not case_ids:
            return {}
        rows = await session.scalars(
            select(CaseProductModel)
            .where(CaseProductModel.case_id.in_(case_ids))
            .order_by(CaseProductModel.case_id, CaseProductModel.sort_order, CaseProductModel.id)
        )
        products: dict[UUID, list[CaseProduct]] = {case_id: [] for case_id in case_ids}
        for model in rows.all():
            products[model.case_id].append(_product(model))
        return products

    async def _one(self, condition) -> TrackingCase | None:
        async with self.database.session_factory() as session:
            row = (await session.execute(_CASE_QUERY.where(condition))).one_or_none()
            if row is None:
                return None
            case = _case(*row)
            case.items = (await self._load_items(session, [case.id]))[case.id]
            case.products = (await self._load_products(session, [case.id]))[case.id]
            return case

    async def get_case(self, case_id: UUID) -> TrackingCase | None:
        """Find a case with its items.

        Args:
            case_id (UUID): Case identifier.

        Returns:
            TrackingCase | None: The case when it exists.
        """

        return await self._one(CaseModel.id == case_id)

    async def get_case_by_request(self, request_id: UUID) -> TrackingCase | None:
        """Find the case opened for a public request.

        Args:
            request_id (UUID): Public request identifier.

        Returns:
            TrackingCase | None: The case when it exists.
        """

        return await self._one(CaseModel.request_id == request_id)

    async def legacy_key_exists(self, source_key: str) -> bool:
        """Whether an imported row was already loaded.

        Args:
            source_key (str): Key the import stored in ``legacy.source_key``.

        Returns:
            bool: ``True`` when a case carries that key.
        """

        async with self.database.session_factory() as session:
            found = await session.scalar(
                select(CaseModel.id)
                .where(
                    CaseModel.legacy.is_not(None),
                    CaseModel.legacy["source_key"].astext == source_key,
                )
                .limit(1)
            )
            return found is not None

    async def list_cases(self, filters: CaseFilters, page: int, size: int, today: date) -> CasePage:
        """Return one page of cases with their items.

        Args:
            filters (CaseFilters): Inbox filters.
            page (int): 1-based page.
            size (int): Page size.
            today (date): Local date used by the overdue filter.

        Returns:
            CasePage: The page.
        """

        conditions = _conditions(filters, today)
        count_query = (
            select(func.count())
            .select_from(CaseModel)
            .join(CaseStatusModel, CaseModel.status_id == CaseStatusModel.id)
            .where(*conditions)
        )
        query = (
            _CASE_QUERY.where(*conditions)
            .order_by(*_SORTS[filters.sort], CaseModel.id)
            .offset((page - 1) * size)
            .limit(size)
        )
        async with self.database.session_factory() as session:
            total = int(await session.scalar(count_query) or 0)
            cases = [_case(*row) for row in (await session.execute(query)).all()]
            items = await self._load_items(session, [c.id for c in cases])
            products = await self._load_products(session, [c.id for c in cases])
            for case in cases:
                case.items = items[case.id]
                case.products = products[case.id]
            return CasePage(items=cases, total=total, page=page, size=size)

    async def summarize(self, filters: CaseFilters, today: date) -> CaseSummary:
        """Count the cases matching the filters.

        Args:
            filters (CaseFilters): Inbox filters.
            today (date): Local date for overdue and due-today counts.

        Returns:
            CaseSummary: Totals, overdue, due today, unassigned and breakdowns.
        """

        conditions = _conditions(filters, today)
        is_open = CaseStatusModel.is_terminal.is_(False)
        base = (
            select()
            .select_from(CaseModel)
            .join(CaseStatusModel, CaseModel.status_id == CaseStatusModel.id)
            .where(*conditions)
        )
        totals_query = base.add_columns(
            func.count(),
            func.count().filter(is_open),
            func.count().filter(and_(is_open, CaseModel.due_on < today)),
            func.count().filter(and_(is_open, CaseModel.due_on == today)),
            func.count().filter(and_(is_open, CaseModel.assignee_user_id.is_(None))),
            func.count().filter(and_(is_open, CaseModel.news_at.is_not(None))),
            func.count().filter(CaseModel.om_sent_at.is_not(None)),
        )
        by_status_query = base.add_columns(CaseModel.status_id, func.count()).group_by(
            CaseModel.status_id
        )
        by_type_query = base.add_columns(CaseModel.request_type_id, func.count()).group_by(
            CaseModel.request_type_id
        )
        async with self.database.session_factory() as session:
            total, open_count, overdue, due_today, unassigned, with_news, om_sent = (
                await session.execute(totals_query)
            ).one()
            status_counts = dict((await session.execute(by_status_query)).tuples().all())
            type_counts = dict((await session.execute(by_type_query)).tuples().all())
            statuses = (
                await session.scalars(
                    select(CaseStatusModel).order_by(
                        CaseStatusModel.sort_order, CaseStatusModel.name
                    )
                )
            ).all()
            types = (await session.scalars(select(RequestTypeModel))).all()
        return CaseSummary(
            total=int(total),
            open=int(open_count),
            overdue=int(overdue),
            due_today=int(due_today),
            unassigned=int(unassigned),
            with_news=int(with_news),
            om_sent=int(om_sent),
            by_status=[
                CountBucket(s.id, s.code, s.name, int(status_counts.get(s.id, 0)), s.color)
                for s in statuses
                if s.is_active or s.id in status_counts
            ],
            by_type=[
                CountBucket(t.id, t.code, t.name, int(type_counts.get(t.id, 0)))
                for t in sorted(types, key=lambda t: t.prefix)
                if t.is_active or t.id in type_counts
            ],
        )

    async def save_case(self, case: TrackingCase, events: list[CaseEvent]) -> TrackingCase:
        """Save the header and make the stored items match ``case.items``.

        Args:
            case (TrackingCase): Case in its new state.
            events (list[CaseEvent]): Timeline entries produced by the change.

        Returns:
            TrackingCase: The stored case.
        """

        async with self.database.session_factory() as session:
            model = await session.get(CaseModel, case.id)
            if model is None:
                raise ConflictError("El caso ya no existe")
            for name in _HEADER_FIELDS:
                setattr(model, name, getattr(case, name))
            model.status_id = case.status.id
            model.site_id = case.site.id if case.site else None
            model.dependency_id = case.dependency.id if case.dependency else None
            # The product lines are what the requester sent: saving the case never
            # rewrites them.
            _set_classification(model, case)

            stored = {
                m.id: m
                for m in (
                    await session.scalars(
                        select(CaseItemModel).where(CaseItemModel.case_id == case.id)
                    )
                ).all()
            }
            keep = {item.id for item in case.items}
            removed = [item_id for item_id in stored if item_id not in keep]
            if removed:
                await session.execute(delete(CaseItemModel).where(CaseItemModel.id.in_(removed)))
            for item in case.items:
                item_model = stored.get(item.id)
                if item_model is None:
                    session.add(_item_model(item))
                    continue
                item_model.document_type_id = item.document_type.id if item.document_type else None
                for name in _ITEM_FIELDS:
                    setattr(item_model, name, getattr(item, name))
            session.add_all([_event_model(event) for event in events])
            try:
                await session.commit()
            except IntegrityError as exc:
                await session.rollback()
                raise ConflictError("No se pudo guardar el caso") from exc
        result = await self.get_case(case.id)
        if result is None:
            raise ConflictError("El caso ya no existe")
        return result

    async def add_event(self, event: CaseEvent) -> None:
        """Append a timeline entry.

        Args:
            event (CaseEvent): Entry to store.
        """

        async with self.database.session_factory() as session:
            session.add(_event_model(event))
            # Looking at a case is not a change to it: access records leave
            # «updated_at» (and the sort by it) alone.
            if event.case_id is not None and event.event_type not in ACCESS_EVENT_TYPES:
                await session.execute(
                    update(CaseModel)
                    .where(CaseModel.id == event.case_id)
                    .values(updated_at=func.now())
                )
            await session.commit()

    async def set_news(self, case_id: UUID, at: datetime) -> bool:
        """Flag news on a case; an earlier unseen one keeps its time.

        Args:
            case_id (UUID): Case identifier.
            at (datetime): When the email arrived.

        Returns:
            bool: Whether the case had no unseen news before.
        """

        async with self.database.session_factory() as session:
            result = await session.execute(
                update(CaseModel)
                .where(CaseModel.id == case_id, CaseModel.news_at.is_(None))
                .values(news_at=at)
            )
            await session.commit()
            return bool(result.rowcount)

    async def set_om_sent(self, case_id: UUID, at: datetime) -> None:
        """Mark that an improvement opportunity went to the supplier; the first keeps its time.

        Args:
            case_id (UUID): Case identifier.
            at (datetime): When the OM was queued.
        """

        async with self.database.session_factory() as session:
            await session.execute(
                update(CaseModel)
                .where(CaseModel.id == case_id, CaseModel.om_sent_at.is_(None))
                .values(om_sent_at=at)
            )
            await session.commit()

    async def clear_news(self, case_id: UUID) -> bool:
        """Clear the news flag of a case.

        Args:
            case_id (UUID): Case identifier.

        Returns:
            bool: Whether the case had news.
        """

        async with self.database.session_factory() as session:
            result = await session.execute(
                update(CaseModel)
                .where(CaseModel.id == case_id, CaseModel.news_at.is_not(None))
                .values(news_at=None)
            )
            await session.commit()
            return bool(result.rowcount)

    async def list_events(self, case_id: UUID) -> list[CaseEvent]:
        """List the timeline oldest first.

        Args:
            case_id (UUID): Case identifier.

        Returns:
            list[CaseEvent]: Timeline entries.
        """

        query = (
            select(CaseEventModel)
            .where(CaseEventModel.case_id == case_id)
            .order_by(CaseEventModel.created_at, CaseEventModel.id)
        )
        async with self.database.session_factory() as session:
            return [_event(m) for m in (await session.scalars(query)).all()]

    async def delete_case(self, case_id: UUID, event: CaseEvent) -> None:
        """Delete a case and its documents in one transaction with its record.

        The history rows keep their consecutive and lose only the link.

        Args:
            case_id (UUID): Case to delete.
            event (CaseEvent): The deletion entry, with ``case_code`` set.
        """

        async with self.database.session_factory() as session:
            session.add(_event_model(event))
            await session.flush()
            await session.execute(delete(CaseModel).where(CaseModel.id == case_id))
            await session.commit()

    async def list_activity(self, filters: ActivityFilters) -> list[CaseEvent]:
        """History of the whole module, newest first.

        Args:
            filters (ActivityFilters): Cursor, size and filters.

        Returns:
            list[CaseEvent]: Up to ``filters.limit`` entries.
        """

        query = select(CaseEventModel)
        if filters.before is not None:
            query = query.where(CaseEventModel.created_at < filters.before)
        if filters.event_types:
            query = query.where(CaseEventModel.event_type.in_(filters.event_types))
        if not filters.include_access:
            query = query.where(CaseEventModel.event_type.not_in(ACCESS_EVENT_TYPES))
        if filters.actor_user_id is not None:
            query = query.where(CaseEventModel.actor_user_id == filters.actor_user_id)
        if filters.case_code:
            query = query.where(CaseEventModel.case_code.ilike(f"%{filters.case_code}%"))
        query = query.order_by(CaseEventModel.created_at.desc(), CaseEventModel.id.desc()).limit(
            filters.limit
        )
        async with self.database.session_factory() as session:
            return [_event(m) for m in (await session.scalars(query)).all()]

    async def has_recent_access(
        self, event_type: str, actor_user_id: UUID, case_id: UUID | None, since: datetime
    ) -> bool:
        """Whether the person left this access record after ``since``."""

        query = select(CaseEventModel.id).where(
            CaseEventModel.event_type == event_type,
            CaseEventModel.actor_user_id == actor_user_id,
            CaseEventModel.created_at >= since,
            CaseEventModel.case_id.is_(None)
            if case_id is None
            else CaseEventModel.case_id == case_id,
        )
        async with self.database.session_factory() as session:
            return await session.scalar(query.limit(1)) is not None

    async def purge_access_events(self, before: datetime) -> int:
        """Delete inbox visits older than ``before``.

        Openings of a case (``viewed``) are kept: they feed later analytics.

        Returns:
            int: How many rows went.
        """

        statement = delete(CaseEventModel).where(
            CaseEventModel.event_type == EventType.INBOX_OPENED,
            CaseEventModel.created_at < before,
        )
        async with self.database.session_factory() as session:
            result = await session.execute(statement)
            await session.commit()
            return result.rowcount or 0

    async def last_seen(self, since: datetime) -> list[LastSeen]:
        """Last inbox opening per person since ``since``, most recent first."""

        last = func.max(CaseEventModel.created_at)
        query = (
            select(CaseEventModel.actor_user_id, last)
            .where(
                CaseEventModel.event_type == EventType.INBOX_OPENED,
                CaseEventModel.actor_user_id.is_not(None),
                CaseEventModel.created_at >= since,
            )
            .group_by(CaseEventModel.actor_user_id)
            .order_by(last.desc())
        )
        async with self.database.session_factory() as session:
            rows = (await session.execute(query)).all()
        return [LastSeen(user_id, seen_at) for user_id, seen_at in rows]

    async def suggest(self, field: str, query: str, limit: int) -> list[str]:
        """Distinct values previously typed in a free-text field.

        Args:
            field (str): customer, supplier, sales_executive or requester.
            query (str): Text the value contains.
            limit (int): Maximum number of values.

        Returns:
            list[str]: Values ordered alphabetically.
        """

        column = _SUGGESTION_COLUMNS[field]
        statement = select(column).where(column.is_not(None)).distinct().order_by(column)
        if query:
            statement = statement.where(column.ilike(f"%{query}%"))
        async with self.database.session_factory() as session:
            return list((await session.scalars(statement.limit(limit))).all())
