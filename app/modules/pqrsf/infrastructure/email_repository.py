"""PostgreSQL repository for emails received at the PQRSF mailbox."""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert

from app.infrastructure.database import Database
from app.modules.pqrsf.domain.email import FAILED, RECEIVED, CaseAttachment, EmailMessage
from app.modules.pqrsf.infrastructure.models import (
    CaseAttachmentModel,
    CaseModel,
    EmailMessageModel,
    MailSyncStateModel,
)


def _message(model: EmailMessageModel) -> EmailMessage:
    return EmailMessage(
        id=model.id,
        internet_message_id=model.internet_message_id,
        graph_id=model.graph_id,
        conversation_id=model.conversation_id,
        mailbox=model.mailbox,
        sender_address=model.sender_address,
        sender_name=model.sender_name,
        subject=model.subject,
        body_text=model.body_text,
        received_at=model.received_at,
        status=model.status,
        case_id=model.case_id,
        attempts=model.attempts,
        error=model.error,
        detail=dict(model.detail or {}),
    )


def _attachment(model: CaseAttachmentModel) -> CaseAttachment:
    return CaseAttachment(
        id=model.id,
        case_id=model.case_id,
        message_id=model.message_id,
        original_filename=model.original_filename,
        extension=model.extension,
        content_type=model.content_type,
        size_bytes=model.size_bytes,
        checksum_sha256=model.checksum_sha256,
        storage_key=model.storage_key,
        created_at=model.created_at,
    )


class SqlAlchemyEmailRepository:
    """Persist received emails, case attachments and the sync cursor."""

    def __init__(self, database: Database) -> None:
        """Create the repository.

        Args:
            database (Database): Shared database resource.
        """

        self.database = database

    async def add_message(self, message: EmailMessage) -> bool:
        """Insert a message unless its Internet message id exists already.

        Args:
            message (EmailMessage): Message to store.

        Returns:
            bool: ``False`` when it was already stored (a duplicate delivery).
        """

        statement = (
            insert(EmailMessageModel)
            .values(
                id=message.id,
                internet_message_id=message.internet_message_id,
                graph_id=message.graph_id,
                conversation_id=message.conversation_id,
                mailbox=message.mailbox,
                sender_address=message.sender_address,
                sender_name=message.sender_name,
                subject=message.subject,
                body_text=message.body_text,
                received_at=message.received_at,
                status=message.status,
                case_id=message.case_id,
                attempts=message.attempts,
                error=message.error,
                detail=message.detail,
            )
            .on_conflict_do_nothing(constraint="pqrsf_email_messages_internet_id_key")
            .returning(EmailMessageModel.id)
        )
        async with self.database.session_factory() as session:
            inserted = await session.scalar(statement)
            await session.commit()
        return inserted is not None

    async def get_message(self, internet_message_id: str) -> EmailMessage | None:
        """Find a stored message by its Internet message id."""

        async with self.database.session_factory() as session:
            model = await session.scalar(
                select(EmailMessageModel).where(
                    EmailMessageModel.internet_message_id == internet_message_id
                )
            )
            return _message(model) if model else None

    async def save_message(self, message: EmailMessage) -> None:
        """Update the mutable fields of a stored message."""

        async with self.database.session_factory() as session:
            await session.execute(
                update(EmailMessageModel)
                .where(EmailMessageModel.id == message.id)
                .values(
                    status=message.status,
                    case_id=message.case_id,
                    attempts=message.attempts,
                    error=message.error,
                    detail=message.detail,
                    updated_at=func.now(),
                )
            )
            await session.commit()

    async def list_retryable(self, max_attempts: int) -> list[EmailMessage]:
        """Messages left claimed or failed, oldest first."""

        async with self.database.session_factory() as session:
            rows = await session.scalars(
                select(EmailMessageModel)
                .where(
                    EmailMessageModel.status.in_((RECEIVED, FAILED)),
                    EmailMessageModel.attempts < max_attempts,
                )
                .order_by(EmailMessageModel.received_at)
                .limit(100)
            )
            return [_message(model) for model in rows]

    async def find_case_by_conversation(self, conversation_id: str) -> UUID | None:
        """The case of the latest message of a conversation that opened or joined one.

        The latest, not the first: a reply after a case closed opens a new case, and
        the rest of the conversation belongs to that one.
        """

        async with self.database.session_factory() as session:
            return await session.scalar(
                select(EmailMessageModel.case_id)
                .where(
                    EmailMessageModel.conversation_id == conversation_id,
                    EmailMessageModel.case_id.is_not(None),
                )
                .order_by(EmailMessageModel.received_at.desc())
                .limit(1)
            )

    async def find_case_by_code(self, code: str) -> UUID | None:
        """The case with this consecutive."""

        async with self.database.session_factory() as session:
            return await session.scalar(select(CaseModel.id).where(CaseModel.code == code))

    async def get_delta_link(self, mailbox: str) -> str | None:
        """The stored cursor, or ``None`` before the first sync."""

        async with self.database.session_factory() as session:
            return await session.scalar(
                select(MailSyncStateModel.delta_link).where(MailSyncStateModel.mailbox == mailbox)
            )

    async def save_delta_link(self, mailbox: str, delta_link: str) -> None:
        """Insert or replace the cursor of a mailbox."""

        statement = insert(MailSyncStateModel).values(mailbox=mailbox, delta_link=delta_link)
        statement = statement.on_conflict_do_update(
            index_elements=[MailSyncStateModel.mailbox],
            set_={"delta_link": delta_link, "updated_at": func.now()},
        )
        async with self.database.session_factory() as session:
            await session.execute(statement)
            await session.commit()

    async def add_attachments(self, attachments: list[CaseAttachment]) -> None:
        """Persist attachment metadata."""

        if not attachments:
            return
        async with self.database.session_factory() as session:
            session.add_all(
                CaseAttachmentModel(
                    id=item.id,
                    case_id=item.case_id,
                    message_id=item.message_id,
                    original_filename=item.original_filename,
                    extension=item.extension,
                    content_type=item.content_type,
                    size_bytes=item.size_bytes,
                    checksum_sha256=item.checksum_sha256,
                    storage_key=item.storage_key,
                )
                for item in attachments
            )
            await session.commit()

    async def list_attachments(self, case_id: UUID) -> list[CaseAttachment]:
        """A case's attachments, oldest first."""

        async with self.database.session_factory() as session:
            rows = await session.scalars(
                select(CaseAttachmentModel)
                .where(CaseAttachmentModel.case_id == case_id)
                .order_by(CaseAttachmentModel.created_at, CaseAttachmentModel.id)
            )
            return [_attachment(model) for model in rows]

    async def get_attachment(self, case_id: UUID, attachment_id: UUID) -> CaseAttachment | None:
        """One attachment owned by the case."""

        async with self.database.session_factory() as session:
            model = await session.scalar(
                select(CaseAttachmentModel).where(
                    CaseAttachmentModel.case_id == case_id,
                    CaseAttachmentModel.id == attachment_id,
                )
            )
            return _attachment(model) if model else None
