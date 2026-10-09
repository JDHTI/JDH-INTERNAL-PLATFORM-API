"""PostgreSQL repository for the answers given to the requested documents of a case."""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import select

from app.infrastructure.database import Database
from app.modules.documents.infrastructure.models import DocumentFolderModel, DocumentModel
from app.modules.identity.infrastructure.models import AreaModel
from app.modules.pqrsf.domain.item_delivery import EMAIL_NOT_SENT, ItemDelivery
from app.modules.pqrsf.infrastructure.models import ItemDeliveryModel, NotificationModel


def _delivery(
    model: ItemDeliveryModel, email: NotificationModel | None, path: str | None
) -> ItemDelivery:
    return ItemDelivery(
        id=model.id,
        case_id=model.case_id,
        item_id=model.item_id,
        source=model.source,
        filename=model.filename,
        created_by=model.created_by,
        created_at=model.created_at,
        document_id=model.document_id,
        attachment_id=model.attachment_id,
        notification_id=model.notification_id,
        email_status=email.status if email else EMAIL_NOT_SENT,
        email_sent_at=email.sent_at if email else None,
        email_error=email.error if email and email.status == "failed" else None,
        document_path=path,
    )


class SqlAlchemyItemDeliveryRepository:
    """Persist which file answered each requested document."""

    def __init__(self, database: Database) -> None:
        """Create the repository.

        Args:
            database (Database): Shared database resource.
        """

        self.database = database

    async def add(self, delivery: ItemDelivery) -> None:
        """Store an answer."""

        async with self.database.session_factory() as session:
            session.add(
                ItemDeliveryModel(
                    id=delivery.id,
                    case_id=delivery.case_id,
                    item_id=delivery.item_id,
                    source=delivery.source,
                    document_id=delivery.document_id,
                    attachment_id=delivery.attachment_id,
                    filename=delivery.filename,
                    notification_id=delivery.notification_id,
                    created_by=delivery.created_by,
                )
            )
            await session.commit()

    async def list_for_case(self, case_id: UUID) -> list[ItemDelivery]:
        """The answers of a case, oldest first, with where their email stands."""

        async with self.database.session_factory() as session:
            rows = await session.execute(
                select(
                    ItemDeliveryModel,
                    NotificationModel,
                    DocumentModel,
                    DocumentFolderModel,
                    AreaModel,
                )
                .outerjoin(
                    NotificationModel, ItemDeliveryModel.notification_id == NotificationModel.id
                )
                .outerjoin(DocumentModel, ItemDeliveryModel.document_id == DocumentModel.id)
                .outerjoin(DocumentFolderModel, DocumentModel.folder_id == DocumentFolderModel.id)
                .outerjoin(AreaModel, DocumentModel.area_id == AreaModel.id)
                .where(ItemDeliveryModel.case_id == case_id)
                .order_by(ItemDeliveryModel.created_at)
            )
            return [
                _delivery(
                    model,
                    email,
                    f"{area.name} / {folder.name} / {document.name}" if document else None,
                )
                for model, email, document, folder, area in rows
            ]
