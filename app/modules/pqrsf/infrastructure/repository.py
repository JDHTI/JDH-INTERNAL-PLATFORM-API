"""PostgreSQL repository for PQRSF requests."""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import case, delete, func, select
from sqlalchemy.exc import IntegrityError

from app.core.errors import ConflictError
from app.infrastructure.database import Database
from app.modules.pqrsf.domain.entities import (
    PqrsfAttachment,
    PqrsfFilters,
    PqrsfPage,
    PqrsfRequest,
    RequestType,
)
from app.modules.pqrsf.infrastructure.models import (
    PqrsfAttachmentModel,
    PqrsfRequestModel,
    RequestTypeModel,
)
from app.modules.sites.domain.entities import Site
from app.modules.sites.infrastructure.models import SiteModel


def _type(model: RequestTypeModel) -> RequestType:
    return RequestType(
        id=model.id,
        code=model.code,
        name=model.name,
        allows_reason=model.allows_reason,
        allows_attachments=model.allows_attachments,
        is_active=model.is_active,
        created_at=model.created_at,
        updated_at=model.updated_at,
        prefix=model.prefix,
        requires_follow_up=model.requires_follow_up,
        sla_days=model.sla_days,
    )


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


def _request(
    model: PqrsfRequestModel, request_type: RequestTypeModel, site: SiteModel
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
        reason=model.reason,
        description=model.description,
        idempotency_key=model.idempotency_key,
        idempotency_hash=model.idempotency_hash,
        created_at=model.created_at,
        updated_at=model.updated_at,
        attachments=[],
    )


_REQUEST_QUERY = (
    select(PqrsfRequestModel, RequestTypeModel, SiteModel)
    .join(RequestTypeModel, PqrsfRequestModel.request_type_id == RequestTypeModel.id)
    .join(SiteModel, PqrsfRequestModel.site_id == SiteModel.id)
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
            try:
                await session.commit()
            except IntegrityError as exc:
                await session.rollback()
                raise ConflictError("Otro tipo de solicitud ya usa ese prefijo") from exc
            await session.refresh(model)
            return _type(model)

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
            reason=request.reason,
            description=request.description,
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
