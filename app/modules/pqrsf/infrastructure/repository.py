"""PostgreSQL repository for PQRSF requests."""

from __future__ import annotations

from datetime import date
from uuid import UUID

from sqlalchemy import case, delete, func, select
from sqlalchemy.exc import IntegrityError

from app.core.errors import ConflictError
from app.infrastructure.database import Database
from app.modules.pqrsf.domain.entities import (
    Cause,
    ClaimedProduct,
    DocumentCategory,
    DocumentOption,
    PqrsfAttachment,
    PqrsfFilters,
    PqrsfPage,
    PqrsfRequest,
    RequestedDocument,
    RequestType,
    Subject,
)
from app.modules.pqrsf.domain.tracking import Dependency
from app.modules.pqrsf.infrastructure.models import (
    CauseModel,
    DependencyModel,
    DocumentCategoryModel,
    DocumentTypeModel,
    PqrsfAttachmentModel,
    PqrsfRequestModel,
    RequestTypeModel,
    SubjectModel,
)
from app.modules.sites.domain.entities import Site
from app.modules.sites.infrastructure.models import SiteModel


def _type(model: RequestTypeModel) -> RequestType:
    return RequestType(
        id=model.id,
        code=model.code,
        name=model.name,
        allows_document_request=model.allows_document_request,
        allows_attachments=model.allows_attachments,
        is_active=model.is_active,
        created_at=model.created_at,
        updated_at=model.updated_at,
        prefix=model.prefix,
        requires_follow_up=model.requires_follow_up,
        sla_days=model.sla_days,
        handling=model.handling,
        description=model.description,
    )


def _cause(model: CauseModel) -> Cause:
    return Cause(
        id=model.id,
        subject_id=model.subject_id,
        code=model.code,
        name=model.name,
        description=model.description,
        dependency_id=model.dependency_id,
        route=model.route,
        requires_product=model.requires_product,
        allows_product=model.allows_product,
        is_other=model.is_other,
        sort_order=model.sort_order,
        is_active=model.is_active,
    )


def _subject(model: SubjectModel, causes: tuple[Cause, ...] = ()) -> Subject:
    return Subject(
        id=model.id,
        code=model.code,
        name=model.name,
        description=model.description,
        request_type_codes=tuple(model.request_type_codes or ()),
        dependency_id=model.dependency_id,
        is_other=model.is_other,
        causes=causes,
        sort_order=model.sort_order,
        is_active=model.is_active,
    )


def _products(items: list | None) -> list[ClaimedProduct]:
    return [
        ClaimedProduct(
            invoice_number=item["invoice_number"],
            sku=item["sku"],
            lot=item["lot"],
            expiry_date=date.fromisoformat(item["expiry_date"]),
            affected_quantity=int(item["affected_quantity"]),
            description=item.get("description"),
            supplier_code=item.get("supplier_code"),
            supplier_name=item.get("supplier_name"),
        )
        for item in items or []
    ]


def _product_rows(products: list[ClaimedProduct]) -> list[dict] | None:
    return [
        {
            "invoice_number": product.invoice_number,
            "sku": product.sku,
            "description": product.description,
            "supplier_code": product.supplier_code,
            "supplier_name": product.supplier_name,
            "lot": product.lot,
            "expiry_date": product.expiry_date.isoformat(),
            "affected_quantity": product.affected_quantity,
        }
        for product in products
    ] or None


def request_type_from_model(model: RequestTypeModel) -> RequestType:
    """Map a request type row; shared with the case repository."""

    return _type(model)


def _site(model: SiteModel) -> Site:
    return Site(
        id=model.id,
        code=model.code,
        name=model.name,
        is_active=model.is_active,
        created_at=model.created_at,
        updated_at=model.updated_at,
        abbreviation=model.abbreviation,
    )


def _attachment(model: PqrsfAttachmentModel) -> PqrsfAttachment:
    return PqrsfAttachment(
        id=model.id,
        request_id=model.request_id,
        original_filename=model.original_filename,
        extension=model.extension,
        content_type=model.content_type,
        size_bytes=model.size_bytes,
        checksum_sha256=model.checksum_sha256,
        storage_key=model.storage_key,
        created_at=model.created_at,
    )


def _documents(items: list | None) -> list[RequestedDocument]:
    return [
        RequestedDocument(
            document_type_id=UUID(item["document_type_id"])
            if item.get("document_type_id")
            else None,
            description=item.get("description"),
        )
        for item in items or []
    ]


def _request(
    model: PqrsfRequestModel,
    request_type: RequestTypeModel,
    site: SiteModel,
    document_dependency_id: UUID | None = None,
    subject: SubjectModel | None = None,
    cause: CauseModel | None = None,
) -> PqrsfRequest:
    return PqrsfRequest(
        id=model.id,
        reference=model.reference,
        request_type=_type(request_type),
        site=_site(site),
        full_name=model.full_name,
        business_name=model.business_name,
        nit=model.nit,
        address=model.address,
        department=model.department,
        city=model.city,
        email=model.email,
        subject=model.subject,
        description=model.description,
        idempotency_key=model.idempotency_key,
        idempotency_hash=model.idempotency_hash,
        created_at=model.created_at,
        updated_at=model.updated_at,
        attachments=[],
        document_request=model.document_request,
        document_category_id=model.document_category_id,
        documents=_documents(model.document_items),
        document_dependency_id=document_dependency_id,
        subject_ref=_subject(subject) if subject is not None else None,
        cause_ref=_cause(cause) if cause is not None else None,
        products=_products(model.products),
        pickup_order_requested=model.pickup_order_requested,
        pickup_order_number=model.pickup_order_number,
    )


_REQUEST_QUERY = (
    select(
        PqrsfRequestModel,
        RequestTypeModel,
        SiteModel,
        DocumentCategoryModel.dependency_id,
        SubjectModel,
        CauseModel,
    )
    .join(RequestTypeModel, PqrsfRequestModel.request_type_id == RequestTypeModel.id)
    .join(SiteModel, PqrsfRequestModel.site_id == SiteModel.id)
    .outerjoin(
        DocumentCategoryModel, PqrsfRequestModel.document_category_id == DocumentCategoryModel.id
    )
    .outerjoin(SubjectModel, PqrsfRequestModel.subject_id == SubjectModel.id)
    .outerjoin(CauseModel, PqrsfRequestModel.cause_id == CauseModel.id)
)
_REQUEST_TYPE_ORDER = case(
    {
        "peticion": 0,
        "queja": 1,
        "reclamo": 2,
        "sugerencia": 3,
        "felicitacion": 4,
    },
    value=RequestTypeModel.code,
    else_=99,
)


class SqlAlchemyPqrsfRepository:
    """Persist request types and requests with short-lived transactions."""

    def __init__(self, database: Database) -> None:
        """Create the repository.

        Args:
            database (Database): Shared database resource.
        """

        self.database = database

    async def list_request_types(self, active_only: bool = True) -> list[RequestType]:
        """List request types by display order."""

        query = select(RequestTypeModel).order_by(_REQUEST_TYPE_ORDER, RequestTypeModel.id)
        if active_only:
            query = query.where(RequestTypeModel.is_active.is_(True))
        async with self.database.session_factory() as session:
            result = await session.execute(query)
            return [_type(model) for model in result.scalars()]

    async def list_document_categories(self) -> list[DocumentCategory]:
        """List the active categories that offer at least one active document.

        Document types without a category are left out: they are not offered
        to customers.
        """

        query = (
            select(DocumentCategoryModel, DocumentTypeModel)
            .join(DocumentTypeModel, DocumentTypeModel.category_id == DocumentCategoryModel.id)
            .where(DocumentCategoryModel.is_active.is_(True), DocumentTypeModel.is_active.is_(True))
            .order_by(
                DocumentCategoryModel.sort_order,
                DocumentCategoryModel.name,
                DocumentTypeModel.name,
            )
        )
        categories: dict[UUID, DocumentCategory] = {}
        async with self.database.session_factory() as session:
            for category, document in (await session.execute(query)).all():
                entry = categories.setdefault(
                    category.id,
                    DocumentCategory(
                        id=category.id,
                        code=category.code,
                        name=category.name,
                        dependency_id=category.dependency_id,
                        documents=[],
                    ),
                )
                entry.documents.append(DocumentOption(id=document.id, name=document.name))
        return list(categories.values())

    async def get_request_type(self, request_type_id: UUID) -> RequestType | None:
        """Find one request type."""

        async with self.database.session_factory() as session:
            model = await session.get(RequestTypeModel, request_type_id)
            return _type(model) if model else None

    async def save_request_type(self, request_type: RequestType) -> RequestType:
        """Persist the follow-up settings of a request type.

        Args:
            request_type (RequestType): Type with the new settings.

        Returns:
            RequestType: The stored type.
        """

        async with self.database.session_factory() as session:
            model = await session.get(RequestTypeModel, request_type.id)
            if model is None:
                raise ConflictError("El tipo de solicitud ya no existe")
            model.prefix = request_type.prefix
            model.requires_follow_up = request_type.requires_follow_up
            model.sla_days = request_type.sla_days
            model.handling = request_type.handling
            model.description = request_type.description
            model.allows_attachments = request_type.allows_attachments
            try:
                await session.commit()
            except IntegrityError as exc:
                await session.rollback()
                raise ConflictError("Otro tipo de solicitud ya usa ese prefijo") from exc
            await session.refresh(model)
            return _type(model)

    async def list_subjects(self, active_only: bool = True) -> list[Subject]:
        """List subjects in display order, each with its causes in display order.

        Args:
            active_only (bool): Leave out disabled subjects and causes.

        Returns:
            list[Subject]: The subject catalog.
        """

        subjects = select(SubjectModel).order_by(SubjectModel.sort_order, SubjectModel.name)
        causes = select(CauseModel).order_by(CauseModel.sort_order, CauseModel.name)
        if active_only:
            subjects = subjects.where(SubjectModel.is_active.is_(True))
            causes = causes.where(CauseModel.is_active.is_(True))
        async with self.database.session_factory() as session:
            subject_rows = list((await session.execute(subjects)).scalars())
            by_subject: dict[UUID, list[Cause]] = {}
            for model in (await session.execute(causes)).scalars():
                by_subject.setdefault(model.subject_id, []).append(_cause(model))
        return [_subject(model, tuple(by_subject.get(model.id, ()))) for model in subject_rows]

    async def list_subject_dependencies(self) -> list[Dependency]:
        """List the active dependencies some active subject belongs to, in display order.

        Returns:
            list[Dependency]: The areas the public form can filter subjects by.
        """

        query = (
            select(DependencyModel)
            .where(
                DependencyModel.is_active.is_(True),
                DependencyModel.id.in_(
                    select(SubjectModel.dependency_id).where(SubjectModel.is_active.is_(True))
                ),
            )
            .order_by(DependencyModel.sort_order, DependencyModel.name)
        )
        async with self.database.session_factory() as session:
            return [
                Dependency(
                    id=model.id,
                    code=model.code,
                    name=model.name,
                    sort_order=model.sort_order,
                    is_active=model.is_active,
                    created_at=model.created_at,
                    updated_at=model.updated_at,
                )
                for model in (await session.execute(query)).scalars()
            ]

    async def save_subject(self, subject: Subject) -> Subject:
        """Insert or update a subject (its causes are saved apart).

        Args:
            subject (Subject): Subject to store.

        Returns:
            Subject: The stored subject, without causes.
        """

        async with self.database.session_factory() as session:
            model = await session.get(SubjectModel, subject.id)
            if model is None:
                model = SubjectModel(id=subject.id, code=subject.code)
                session.add(model)
            model.name = subject.name
            model.description = subject.description
            model.request_type_codes = list(subject.request_type_codes)
            model.dependency_id = subject.dependency_id
            model.is_other = subject.is_other
            model.sort_order = subject.sort_order
            model.is_active = subject.is_active
            try:
                await session.commit()
            except IntegrityError as exc:
                await session.rollback()
                raise ConflictError("No se pudo guardar el asunto: código repetido") from exc
            await session.refresh(model)
            return _subject(model)

    async def save_cause(self, cause: Cause) -> Cause:
        """Insert or update a cause.

        Args:
            cause (Cause): Cause to store.

        Returns:
            Cause: The stored cause.
        """

        async with self.database.session_factory() as session:
            model = await session.get(CauseModel, cause.id)
            if model is None:
                model = CauseModel(id=cause.id, subject_id=cause.subject_id, code=cause.code)
                session.add(model)
            model.name = cause.name
            model.description = cause.description
            model.dependency_id = cause.dependency_id
            model.route = cause.route
            model.requires_product = cause.requires_product
            model.allows_product = cause.allows_product
            model.is_other = cause.is_other
            model.sort_order = cause.sort_order
            model.is_active = cause.is_active
            try:
                await session.commit()
            except IntegrityError as exc:
                await session.rollback()
                raise ConflictError("No se pudo guardar la causal: código repetido") from exc
            await session.refresh(model)
            return _cause(model)

    async def get_by_idempotency_key(self, key: str) -> PqrsfRequest | None:
        """Find a request by its client idempotency key."""

        query = _REQUEST_QUERY.where(PqrsfRequestModel.idempotency_key == key)
        async with self.database.session_factory() as session:
            row = (await session.execute(query)).one_or_none()
            return _request(*row) if row else None

    async def create_request(self, request: PqrsfRequest) -> PqrsfRequest:
        """Insert a request and reload server timestamps."""

        model = PqrsfRequestModel(
            id=request.id,
            reference=request.reference,
            request_type_id=request.request_type.id,
            site_id=request.site.id,
            full_name=request.full_name,
            business_name=request.business_name,
            nit=request.nit,
            address=request.address,
            department=request.department,
            city=request.city,
            email=request.email,
            subject=request.subject,
            description=request.description,
            document_request=request.document_request,
            document_category_id=request.document_category_id,
            document_items=[
                {
                    "document_type_id": str(item.document_type_id)
                    if item.document_type_id
                    else None,
                    "description": item.description,
                }
                for item in request.documents
            ]
            or None,
            subject_id=request.subject_ref.id if request.subject_ref else None,
            cause_id=request.cause_ref.id if request.cause_ref else None,
            products=_product_rows(request.products),
            pickup_order_requested=request.pickup_order_requested,
            pickup_order_number=request.pickup_order_number,
            idempotency_key=request.idempotency_key,
            idempotency_hash=request.idempotency_hash,
        )
        async with self.database.session_factory() as session:
            session.add(model)
            try:
                await session.commit()
            except IntegrityError as exc:
                await session.rollback()
                raise ConflictError("No se pudo registrar la solicitud con esa clave") from exc
        stored = await self.get_request(request.id)
        if stored is None:
            raise ConflictError("La solicitud no quedó disponible después de guardarla")
        return stored

    async def get_request(self, request_id: UUID) -> PqrsfRequest | None:
        """Find one request with its catalogs."""

        query = _REQUEST_QUERY.where(PqrsfRequestModel.id == request_id)
        async with self.database.session_factory() as session:
            row = (await session.execute(query)).one_or_none()
            if row is None:
                return None
            request = _request(*row)
            attachments = await session.execute(
                select(PqrsfAttachmentModel)
                .where(PqrsfAttachmentModel.request_id == request_id)
                .order_by(PqrsfAttachmentModel.created_at, PqrsfAttachmentModel.id)
            )
            request.attachments = [_attachment(model) for model in attachments.scalars()]
            return request

    async def list_requests(self, filters: PqrsfFilters, page: int, size: int) -> PqrsfPage:
        """Return a page ordered newest first."""

        conditions = []
        if filters.request_type_id:
            conditions.append(PqrsfRequestModel.request_type_id == filters.request_type_id)
        if filters.site_id:
            conditions.append(PqrsfRequestModel.site_id == filters.site_id)
        if filters.reference:
            conditions.append(PqrsfRequestModel.reference.ilike(f"%{filters.reference}%"))

        query = _REQUEST_QUERY.where(*conditions)
        count_query = select(func.count()).select_from(PqrsfRequestModel).where(*conditions)
        query = query.order_by(PqrsfRequestModel.created_at.desc(), PqrsfRequestModel.id.desc())
        query = query.offset((page - 1) * size).limit(size)
        async with self.database.session_factory() as session:
            total = int((await session.execute(count_query)).scalar() or 0)
            rows = (await session.execute(query)).all()
            return PqrsfPage(
                items=[_request(*row) for row in rows], total=total, page=page, size=size
            )

    async def add_attachments(self, attachments: list[PqrsfAttachment]) -> None:
        """Persist attachment metadata after all objects are stored."""

        if not attachments:
            return
        models = [
            PqrsfAttachmentModel(
                id=attachment.id,
                request_id=attachment.request_id,
                original_filename=attachment.original_filename,
                extension=attachment.extension,
                content_type=attachment.content_type,
                size_bytes=attachment.size_bytes,
                checksum_sha256=attachment.checksum_sha256,
                storage_key=attachment.storage_key,
            )
            for attachment in attachments
        ]
        async with self.database.session_factory() as session:
            session.add_all(models)
            try:
                await session.commit()
            except IntegrityError as exc:
                await session.rollback()
                raise ConflictError("No se pudieron registrar los adjuntos") from exc

    async def list_attachments(self, request_id: UUID) -> list[PqrsfAttachment]:
        """List attachment metadata for a request."""

        query = (
            select(PqrsfAttachmentModel)
            .where(PqrsfAttachmentModel.request_id == request_id)
            .order_by(PqrsfAttachmentModel.created_at, PqrsfAttachmentModel.id)
        )
        async with self.database.session_factory() as session:
            result = await session.execute(query)
            return [_attachment(model) for model in result.scalars()]

    async def get_attachment(self, request_id: UUID, attachment_id: UUID) -> PqrsfAttachment | None:
        """Find an attachment that belongs to the request."""

        query = select(PqrsfAttachmentModel).where(
            PqrsfAttachmentModel.request_id == request_id,
            PqrsfAttachmentModel.id == attachment_id,
        )
        async with self.database.session_factory() as session:
            model = (await session.execute(query)).scalar_one_or_none()
            return _attachment(model) if model else None

    async def delete_request(self, request_id: UUID) -> None:
        """Delete a request after a failed object-storage transaction."""

        async with self.database.session_factory() as session:
            await session.execute(
                delete(PqrsfRequestModel).where(PqrsfRequestModel.id == request_id)
            )
            await session.commit()
