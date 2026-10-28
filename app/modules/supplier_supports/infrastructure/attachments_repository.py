"""PostgreSQL repository for the links between supports and documents."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from uuid import UUID

from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert

from app.infrastructure.database import Database
from app.modules.commercial_masters.infrastructure.models import CommercialSupplierModel
from app.modules.documents.infrastructure.models import DocumentModel
from app.modules.supplier_supports.domain.attachments import Attachment
from app.modules.supplier_supports.infrastructure.models import (
    SupplierSupportAttachmentModel,
    SupplierSupportModel,
)

A = SupplierSupportAttachmentModel
_CHUNK = 1000


class SqlAlchemyAttachmentRepository:
    """Support-document links, one short-lived session per operation."""

    def __init__(self, database: Database) -> None:
        """Create the repository.

        Args:
            database (Database): Shared database resource.
        """

        self.database = database

    async def link_many(self, pairs: Sequence[tuple[UUID, UUID]], actor: str, now: datetime) -> int:
        """Link every ``(support, document)`` pair in one transaction; known pairs are kept.

        Returns:
            int: How many links were new.
        """

        created = 0
        async with self.database.session_factory() as session:
            for start in range(0, len(pairs), _CHUNK):
                chunk = pairs[start : start + _CHUNK]
                result = await session.execute(
                    insert(A)
                    .values(
                        [
                            {
                                "support_id": support_id,
                                "document_id": document_id,
                                "linked_by": actor,
                                "linked_at": now,
                            }
                            for support_id, document_id in chunk
                        ]
                    )
                    .on_conflict_do_nothing()
                )
                created += result.rowcount or 0
            await session.commit()
        return created

    async def unlink(self, support_id: UUID, document_id: UUID) -> bool:
        """Remove one link; the document stays in the document manager."""

        async with self.database.session_factory() as session:
            result = await session.execute(
                delete(A).where(A.support_id == support_id, A.document_id == document_id)
            )
            await session.commit()
            return bool(result.rowcount)

    async def _attachments(
        self, support_id: UUID, document_id: UUID | None = None
    ) -> list[Attachment]:
        total = select(A.document_id, func.count().label("n")).group_by(A.document_id).subquery()
        query = (
            select(A, DocumentModel, total.c.n)
            .join(DocumentModel, DocumentModel.id == A.document_id)
            .join(total, total.c.document_id == A.document_id)
            .where(A.support_id == support_id)
            .order_by(A.linked_at.desc(), DocumentModel.name)
        )
        if document_id is not None:
            query = query.where(A.document_id == document_id)
        async with self.database.session_factory() as session:
            rows = await session.execute(query)
            return [
                Attachment(
                    document_id=document.id,
                    name=document.name,
                    original_filename=document.original_filename,
                    extension=document.extension,
                    content_type=document.content_type,
                    size_bytes=document.size_bytes,
                    uploaded_by=document.uploaded_by,
                    uploaded_at=document.created_at,
                    linked_by=link.linked_by,
                    linked_at=link.linked_at,
                    support_count=int(count),
                )
                for link, document, count in rows.all()
            ]

    async def list_for_support(self, support_id: UUID) -> list[Attachment]:
        """The documents backing a support, newest link first, with how many supports each backs."""

        return await self._attachments(support_id)

    async def get_link(self, support_id: UUID, document_id: UUID) -> Attachment | None:
        """One document of a support, or ``None`` when that support does not have it."""

        found = await self._attachments(support_id, document_id)
        return found[0] if found else None

    async def existing_supports(self, ids: Sequence[UUID]) -> set[UUID]:
        """Which of the given supports exist and are not voided."""

        if not ids:
            return set()
        found: set[UUID] = set()
        async with self.database.session_factory() as session:
            for start in range(0, len(ids), _CHUNK):
                rows = await session.execute(
                    select(SupplierSupportModel.id).where(
                        SupplierSupportModel.id.in_(ids[start : start + _CHUNK]),
                        SupplierSupportModel.status != "anulado",
                    )
                )
                found.update(support_id for (support_id,) in rows.all())
        return found

    async def supplier_codes(self, ids: Sequence[UUID]) -> list[str]:
        """Commercial supplier codes of the given supports, in order and without repeats."""

        codes: set[str] = set()
        async with self.database.session_factory() as session:
            for start in range(0, len(ids), _CHUNK):
                rows = await session.execute(
                    select(CommercialSupplierModel.code)
                    .join(
                        SupplierSupportModel,
                        SupplierSupportModel.commercial_supplier_id == CommercialSupplierModel.id,
                    )
                    .where(SupplierSupportModel.id.in_(ids[start : start + _CHUNK]))
                    .distinct()
                )
                codes.update(code for (code,) in rows.all())
        return sorted(codes)


__all__ = ["SqlAlchemyAttachmentRepository"]
