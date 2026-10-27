"""PostgreSQL repository for suggestions and compliments."""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from uuid import UUID
from zoneinfo import ZoneInfo

from sqlalchemy import Select, and_, func, or_, select, update
from sqlalchemy.dialects.postgresql import insert

from app.infrastructure.database import Database
from app.modules.pqrsf.domain.entities import HANDLING_ACKNOWLEDGE
from app.modules.pqrsf.domain.feedback import (
    REPLY_PENDING,
    REPLY_SKIPPED,
    Feedback,
    FeedbackFile,
    FeedbackFilters,
    FeedbackKindCount,
    FeedbackPage,
    FeedbackSummary,
)
from app.modules.pqrsf.infrastructure.models import (
    DependencyModel,
    FeedbackFileModel,
    FeedbackModel,
    NotificationModel,
    RequestTypeModel,
)
from app.modules.pqrsf.infrastructure.repository import area_ref, request_type_from_model
from app.modules.sites.domain.entities import Site
from app.modules.sites.infrastructure.models import SiteModel

_LOCAL = ZoneInfo("America/Bogota")
_FILES = (
    select(func.count())
    .where(FeedbackFileModel.feedback_id == FeedbackModel.id)
    .correlate(FeedbackModel)
    .scalar_subquery()
)
_QUERY: Select = (
    select(
        FeedbackModel,
        RequestTypeModel,
        SiteModel,
        NotificationModel.status,
        NotificationModel.sent_at,
        _FILES,
        DependencyModel,
    )
    .join(RequestTypeModel, FeedbackModel.request_type_id == RequestTypeModel.id)
    .outerjoin(SiteModel, FeedbackModel.site_id == SiteModel.id)
    .outerjoin(DependencyModel, FeedbackModel.dependency_id == DependencyModel.id)
    .outerjoin(NotificationModel, FeedbackModel.reply_notification_id == NotificationModel.id)
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


def _feedback(row) -> Feedback:
    model, kind, site, reply_status, reply_sent_at, files, area = row
    if model.reply_skipped_reason:
        status = REPLY_SKIPPED
    else:
        status = reply_status or REPLY_PENDING
    return Feedback(
        id=model.id,
        request_type=request_type_from_model(kind),
        origin=model.origin,
        full_name=model.full_name,
        email=model.email,
        body=model.body,
        created_at=model.created_at,
        site=_site(site),
        business_name=model.business_name,
        subject=model.subject,
        request_id=model.request_id,
        email_message_id=model.email_message_id,
        reply_notification_id=model.reply_notification_id,
        reply_skipped_reason=model.reply_skipped_reason,
        read_at=model.read_at,
        read_by_user_id=model.read_by_user_id,
        reply_status=status,
        reply_sent_at=reply_sent_at,
        files_count=int(files or 0),
        area=area_ref(area),
    )


def _file(model: FeedbackFileModel) -> FeedbackFile:
    return FeedbackFile(
        id=model.id,
        feedback_id=model.feedback_id,
        original_filename=model.original_filename,
        extension=model.extension,
        content_type=model.content_type,
        size_bytes=model.size_bytes,
        checksum_sha256=model.checksum_sha256,
        storage_key=model.storage_key,
        created_at=model.created_at,
    )


def _start_of(day: date) -> datetime:
    return datetime.combine(day, time.min, tzinfo=_LOCAL)


class SqlAlchemyFeedbackRepository:
    """Persist suggestions and compliments with short-lived transactions."""

    def __init__(self, database: Database) -> None:
        """Create the repository.

        Args:
            database (Database): Shared database resource.
        """

        self.database = database

    async def add(self, feedback: Feedback) -> tuple[Feedback, bool]:
        """Insert a message unless the same request or email was stored before."""

        statement = (
            insert(FeedbackModel)
            .values(
                id=feedback.id,
                request_type_id=feedback.request_type.id,
                origin=feedback.origin,
                request_id=feedback.request_id,
                email_message_id=feedback.email_message_id,
                site_id=feedback.site.id if feedback.site else None,
                dependency_id=feedback.area.id if feedback.area else None,
                full_name=feedback.full_name,
                business_name=feedback.business_name,
                email=feedback.email,
                subject=feedback.subject,
                body=feedback.body,
            )
            .on_conflict_do_nothing()
            .returning(FeedbackModel.id)
        )
        async with self.database.session_factory() as session:
            inserted = await session.scalar(statement)
            await session.commit()
            if inserted is not None:
                row = (await session.execute(_QUERY.where(FeedbackModel.id == inserted))).one()
                return _feedback(row), True
            conditions = []
            if feedback.request_id is not None:
                conditions.append(FeedbackModel.request_id == feedback.request_id)
            if feedback.email_message_id is not None:
                conditions.append(FeedbackModel.email_message_id == feedback.email_message_id)
            row = (await session.execute(_QUERY.where(or_(*conditions)))).one()
            return _feedback(row), False

    async def get(self, feedback_id: UUID) -> Feedback | None:
        """One message with its files."""

        async with self.database.session_factory() as session:
            row = (
                await session.execute(_QUERY.where(FeedbackModel.id == feedback_id))
            ).one_or_none()
            if row is None:
                return None
            feedback = _feedback(row)
            files = await session.scalars(
                select(FeedbackFileModel)
                .where(FeedbackFileModel.feedback_id == feedback_id)
                .order_by(FeedbackFileModel.created_at, FeedbackFileModel.original_filename)
            )
            feedback.files = [_file(model) for model in files]
            return feedback

    async def list_feedback(self, filters: FeedbackFilters, page: int, size: int) -> FeedbackPage:
        """One page of messages, newest first."""

        conditions = []
        if filters.request_type_code:
            conditions.append(RequestTypeModel.code == filters.request_type_code)
        if filters.unread_only:
            conditions.append(FeedbackModel.read_at.is_(None))
        if filters.created_from:
            conditions.append(FeedbackModel.created_at >= _start_of(filters.created_from))
        if filters.created_to:
            conditions.append(
                FeedbackModel.created_at < _start_of(filters.created_to) + timedelta(days=1)
            )
        if filters.query:
            pattern = f"%{filters.query.strip()}%"
            conditions.append(
                or_(
                    FeedbackModel.full_name.ilike(pattern),
                    FeedbackModel.email.ilike(pattern),
                    FeedbackModel.subject.ilike(pattern),
                    FeedbackModel.body.ilike(pattern),
                    FeedbackModel.business_name.ilike(pattern),
                )
            )
        query = _QUERY.where(and_(*conditions)) if conditions else _QUERY
        count = (
            select(func.count())
            .select_from(FeedbackModel)
            .join(RequestTypeModel, FeedbackModel.request_type_id == RequestTypeModel.id)
        )
        if conditions:
            count = count.where(and_(*conditions))
        async with self.database.session_factory() as session:
            total = await session.scalar(count) or 0
            rows = await session.execute(
                query.order_by(FeedbackModel.created_at.desc(), FeedbackModel.id)
                .limit(size)
                .offset((page - 1) * size)
            )
            items = [_feedback(row) for row in rows]
        return FeedbackPage(items=items, total=int(total), page=page, size=size)

    async def summarize(self) -> FeedbackSummary:
        """Totals and unread counts per type that is only thanked."""

        unread = func.count(FeedbackModel.id).filter(FeedbackModel.read_at.is_(None))
        query = (
            select(
                RequestTypeModel.code, RequestTypeModel.name, func.count(FeedbackModel.id), unread
            )
            .outerjoin(FeedbackModel, FeedbackModel.request_type_id == RequestTypeModel.id)
            .where(RequestTypeModel.handling == HANDLING_ACKNOWLEDGE)
            .group_by(RequestTypeModel.code, RequestTypeModel.name)
            .order_by(RequestTypeModel.name)
        )
        async with self.database.session_factory() as session:
            rows = (await session.execute(query)).all()
        kinds = [FeedbackKindCount(code, name, int(n), int(u)) for code, name, n, u in rows]
        return FeedbackSummary(
            total=sum(k.count for k in kinds),
            unread=sum(k.unread for k in kinds),
            by_kind=kinds,
        )

    async def mark_read(self, feedback_id: UUID, user_id: UUID, at: datetime) -> None:
        """Record the first opening only."""

        async with self.database.session_factory() as session:
            await session.execute(
                update(FeedbackModel)
                .where(FeedbackModel.id == feedback_id, FeedbackModel.read_at.is_(None))
                .values(read_at=at, read_by_user_id=user_id)
            )
            await session.commit()

    async def set_reply(
        self, feedback_id: UUID, notification_id: UUID | None, skipped_reason: str | None
    ) -> None:
        """Remember the queued thank-you or why there is none."""

        async with self.database.session_factory() as session:
            await session.execute(
                update(FeedbackModel)
                .where(FeedbackModel.id == feedback_id)
                .values(reply_notification_id=notification_id, reply_skipped_reason=skipped_reason)
            )
            await session.commit()

    async def add_files(self, files: list[FeedbackFile]) -> None:
        """Persist file metadata."""

        async with self.database.session_factory() as session:
            session.add_all(
                FeedbackFileModel(
                    id=f.id,
                    feedback_id=f.feedback_id,
                    original_filename=f.original_filename,
                    extension=f.extension,
                    content_type=f.content_type,
                    size_bytes=f.size_bytes,
                    checksum_sha256=f.checksum_sha256,
                    storage_key=f.storage_key,
                )
                for f in files
            )
            await session.commit()

    async def list_files(self, feedback_id: UUID) -> list[FeedbackFile]:
        """The files of a message, oldest first."""

        async with self.database.session_factory() as session:
            rows = await session.scalars(
                select(FeedbackFileModel)
                .where(FeedbackFileModel.feedback_id == feedback_id)
                .order_by(FeedbackFileModel.created_at, FeedbackFileModel.original_filename)
            )
            return [_file(model) for model in rows]

    async def get_file(self, feedback_id: UUID, file_id: UUID) -> FeedbackFile | None:
        """One file owned by the message."""

        async with self.database.session_factory() as session:
            model = await session.scalar(
                select(FeedbackFileModel).where(
                    FeedbackFileModel.id == file_id, FeedbackFileModel.feedback_id == feedback_id
                )
            )
            return _file(model) if model else None
