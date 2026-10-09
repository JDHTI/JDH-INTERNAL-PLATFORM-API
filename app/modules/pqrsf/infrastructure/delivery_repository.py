"""PostgreSQL repository for the requests answered automatically with certificates."""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from uuid import UUID
from zoneinfo import ZoneInfo

from sqlalchemy import Select, and_, func, or_, select, update
from sqlalchemy.dialects.postgresql import insert

from app.infrastructure.database import Database
from app.modules.pqrsf.domain.certificates import (
    DELIVERY_FAILED,
    DELIVERY_PENDING,
    DELIVERY_SENT,
    DeliveryFilters,
    DeliveryItem,
    DeliveryPage,
    DeliverySummary,
    DocumentDelivery,
)
from app.modules.pqrsf.domain.notifications import FAILED, PENDING
from app.modules.pqrsf.infrastructure.models import DocumentDeliveryModel, NotificationModel
from app.modules.sites.domain.entities import Site
from app.modules.sites.infrastructure.models import SiteModel

_LOCAL = ZoneInfo("America/Bogota")
_QUERY: Select = select(DocumentDeliveryModel, SiteModel).outerjoin(
    SiteModel, DocumentDeliveryModel.site_id == SiteModel.id
)


def _site(model: SiteModel | None) -> Site | None:
    if model is None:
        return None
    return Site(
        id=model.id,
        code=model.code,
        name=model.name,
        is_active=model.is_active,
        created_at=model.created_at,
        updated_at=model.updated_at,
        abbreviation=model.abbreviation,
    )


def _delivery(
    model: DocumentDeliveryModel, site: SiteModel | None, emails: list[NotificationModel]
) -> DocumentDelivery:
    if any(n.status == FAILED for n in emails):
        status = DELIVERY_FAILED
    elif not emails or any(n.status == PENDING for n in emails):
        status = DELIVERY_PENDING
    else:
        status = DELIVERY_SENT
    sent = [n.sent_at for n in emails if n.sent_at]
    errors = [n.error for n in emails if n.error and n.status == FAILED]
    return DocumentDelivery(
        id=model.id,
        full_name=model.full_name,
        email=model.email,
        created_at=model.created_at,
        items=[DeliveryItem.from_json(item) for item in model.items],
        request_id=model.request_id,
        site=_site(site),
        business_name=model.business_name,
        nit=model.nit,
        manual_items=list(model.manual_items or []),
        case_code=model.case_code,
        notification_ids=[UUID(value) for value in model.notification_ids or []],
        read_at=model.read_at,
        read_by_user_id=model.read_by_user_id,
        status=status,
        sent_at=max(sent) if status == DELIVERY_SENT and sent else None,
        error=errors[0] if errors else None,
    )


def _start_of(day: date) -> datetime:
    return datetime.combine(day, time.min, tzinfo=_LOCAL)


class SqlAlchemyDeliveryRepository:
    """Persist the log of automatic document deliveries."""

    def __init__(self, database: Database) -> None:
        """Create the repository.

        Args:
            database (Database): Shared database resource.
        """

        self.database = database

    async def _emails(self, session, rows: list[DocumentDeliveryModel]):
        ids = {UUID(value) for row in rows for value in row.notification_ids or []}
        if not ids:
            return {}
        found = await session.scalars(
            select(NotificationModel).where(NotificationModel.id.in_(ids))
        )
        return {n.id: n for n in found}

    async def add(self, delivery: DocumentDelivery) -> tuple[DocumentDelivery, bool]:
        """Store a delivery once per request."""

        statement = (
            insert(DocumentDeliveryModel)
            .values(
                id=delivery.id,
                request_id=delivery.request_id,
                site_id=delivery.site.id if delivery.site else None,
                full_name=delivery.full_name,
                business_name=delivery.business_name,
                nit=delivery.nit,
                email=delivery.email,
                items=[item.to_json() for item in delivery.items],
                manual_items=delivery.manual_items,
                case_code=delivery.case_code,
                notification_ids=[str(i) for i in delivery.notification_ids],
            )
            .on_conflict_do_nothing()
            .returning(DocumentDeliveryModel.id)
        )
        async with self.database.session_factory() as session:
            inserted = await session.scalar(statement)
            await session.commit()
        stored = await (
            self.get(inserted) if inserted else self.get_by_request(delivery.request_id)
        )
        assert stored is not None
        return stored, inserted is not None

    async def get(self, delivery_id: UUID) -> DocumentDelivery | None:
        """One delivery with the state of its emails."""

        return await self._one(DocumentDeliveryModel.id == delivery_id)

    async def get_by_request(self, request_id: UUID) -> DocumentDelivery | None:
        """The delivery of a public request."""

        return await self._one(DocumentDeliveryModel.request_id == request_id)

    async def _one(self, condition) -> DocumentDelivery | None:
        async with self.database.session_factory() as session:
            row = (await session.execute(_QUERY.where(condition))).one_or_none()
            if row is None:
                return None
            model, site = row
            emails = await self._emails(session, [model])
            return _delivery(
                model, site, [emails[UUID(i)] for i in model.notification_ids if UUID(i) in emails]
            )

    async def list_deliveries(self, filters: DeliveryFilters, page: int, size: int) -> DeliveryPage:
        """One page, newest first."""

        conditions = []
        if filters.unread_only:
            conditions.append(DocumentDeliveryModel.read_at.is_(None))
        if filters.created_from:
            conditions.append(DocumentDeliveryModel.created_at >= _start_of(filters.created_from))
        if filters.created_to:
            conditions.append(
                DocumentDeliveryModel.created_at < _start_of(filters.created_to) + timedelta(days=1)
            )
        if filters.query:
            pattern = f"%{filters.query.strip()}%"
            conditions.append(
                or_(
                    DocumentDeliveryModel.full_name.ilike(pattern),
                    DocumentDeliveryModel.email.ilike(pattern),
                    DocumentDeliveryModel.business_name.ilike(pattern),
                    DocumentDeliveryModel.nit.ilike(pattern),
                )
            )
        where = and_(*conditions) if conditions else None
        count = select(func.count()).select_from(DocumentDeliveryModel)
        query = _QUERY
        if where is not None:
            count, query = count.where(where), query.where(where)
        async with self.database.session_factory() as session:
            total = await session.scalar(count) or 0
            rows = (
                await session.execute(
                    query.order_by(
                        DocumentDeliveryModel.created_at.desc(), DocumentDeliveryModel.id
                    )
                    .limit(size)
                    .offset((page - 1) * size)
                )
            ).all()
            emails = await self._emails(session, [model for model, _ in rows])
            items = [
                _delivery(
                    model,
                    site,
                    [emails[UUID(i)] for i in model.notification_ids if UUID(i) in emails],
                )
                for model, site in rows
            ]
        return DeliveryPage(items=items, total=int(total), page=page, size=size)

    async def summarize(self) -> DeliverySummary:
        """Totals and unread."""

        async with self.database.session_factory() as session:
            total, unread = (
                await session.execute(
                    select(
                        func.count(DocumentDeliveryModel.id),
                        func.count(DocumentDeliveryModel.id).filter(
                            DocumentDeliveryModel.read_at.is_(None)
                        ),
                    )
                )
            ).one()
        return DeliverySummary(total=int(total), unread=int(unread))

    async def mark_read(self, delivery_id: UUID, user_id: UUID, at: datetime) -> None:
        """Record the first opening only."""

        async with self.database.session_factory() as session:
            await session.execute(
                update(DocumentDeliveryModel)
                .where(
                    DocumentDeliveryModel.id == delivery_id, DocumentDeliveryModel.read_at.is_(None)
                )
                .values(read_at=at, read_by_user_id=user_id)
            )
            await session.commit()

    async def set_notifications(self, delivery_id: UUID, notification_ids: list[UUID]) -> None:
        """Remember the emails queued for the delivery."""

        async with self.database.session_factory() as session:
            await session.execute(
                update(DocumentDeliveryModel)
                .where(DocumentDeliveryModel.id == delivery_id)
                .values(notification_ids=[str(i) for i in notification_ids])
            )
            await session.commit()

    async def set_case(self, delivery_id: UUID, case_code: str) -> None:
        """Remember the case opened for what a person answers."""

        async with self.database.session_factory() as session:
            await session.execute(
                update(DocumentDeliveryModel)
                .where(DocumentDeliveryModel.id == delivery_id)
                .values(case_code=case_code)
            )
            await session.commit()
