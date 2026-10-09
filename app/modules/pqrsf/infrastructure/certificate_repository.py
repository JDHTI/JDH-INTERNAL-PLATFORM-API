"""PostgreSQL repository for the certificates and the files they point at."""

from __future__ import annotations

from uuid import UUID, uuid4

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert

from app.infrastructure.database import Database
from app.infrastructure.s3 import S3Storage
from app.modules.documents.infrastructure.models import DocumentFolderModel, DocumentModel
from app.modules.identity.infrastructure.models import AreaModel
from app.modules.mail.domain import OutgoingFile
from app.modules.pqrsf.domain.certificates import (
    MANUAL_CATEGORY,
    Certificate,
    CertificateEntry,
    ManagerDocument,
)
from app.modules.pqrsf.infrastructure.models import (
    CertificateModel,
    DocumentCategoryModel,
    DocumentTypeModel,
)


def _document(document: DocumentModel, folder: DocumentFolderModel, area: AreaModel):
    return ManagerDocument(
        id=document.id,
        name=document.name,
        filename=document.original_filename,
        content_type=document.content_type,
        size_bytes=document.size_bytes,
        folder_id=folder.id,
        folder_name=folder.name,
        area_slug=area.slug,
        area_name=area.name,
    )


def _certificate(model: CertificateModel, document: ManagerDocument | None) -> Certificate:
    return Certificate(
        id=model.id,
        document_type_id=model.document_type_id,
        document=document,
        is_active=model.is_active,
        updated_at=model.updated_at,
        updated_by=model.updated_by,
    )


# A certificate with the file it points at (the file may be gone: all its columns are null).
_ENTRIES = (
    select(
        DocumentTypeModel,
        DocumentCategoryModel,
        CertificateModel,
        DocumentModel,
        DocumentFolderModel,
        AreaModel,
    )
    .join(DocumentCategoryModel, DocumentTypeModel.category_id == DocumentCategoryModel.id)
    .outerjoin(CertificateModel, CertificateModel.document_type_id == DocumentTypeModel.id)
    .outerjoin(DocumentModel, CertificateModel.document_id == DocumentModel.id)
    .outerjoin(DocumentFolderModel, DocumentModel.folder_id == DocumentFolderModel.id)
    .outerjoin(AreaModel, DocumentModel.area_id == AreaModel.id)
    .where(
        DocumentTypeModel.is_active.is_(True),
        DocumentCategoryModel.is_active.is_(True),
        DocumentCategoryModel.code != MANUAL_CATEGORY,
    )
)


def _entry(row) -> CertificateEntry:
    document_type, category, certificate, document, folder, area = row
    stored = (
        _certificate(certificate, _document(document, folder, area) if document else None)
        if certificate
        else None
    )
    return CertificateEntry(
        document_type_id=document_type.id,
        name=document_type.name,
        category_code=category.code,
        category_name=category.name,
        certificate=stored,
    )


class SqlAlchemyCertificateRepository:
    """Persist which file answers each document type."""

    def __init__(self, database: Database) -> None:
        """Create the repository.

        Args:
            database (Database): Shared database resource.
        """

        self.database = database

    async def list_entries(self) -> list[CertificateEntry]:
        """The document types the form offers, by category and name."""

        async with self.database.session_factory() as session:
            rows = await session.execute(
                _ENTRIES.order_by(
                    DocumentCategoryModel.sort_order,
                    DocumentCategoryModel.name,
                    func.lower(DocumentTypeModel.name),
                )
            )
            return [_entry(row) for row in rows]

    async def get_entry(self, document_type_id: UUID) -> CertificateEntry | None:
        """One document type with its certificate."""

        async with self.database.session_factory() as session:
            row = (
                await session.execute(_ENTRIES.where(DocumentTypeModel.id == document_type_id))
            ).one_or_none()
            return _entry(row) if row else None

    async def type_names(self, document_type_ids: list[UUID]) -> dict[UUID, str]:
        """Names of document types, whatever their category."""

        if not document_type_ids:
            return {}
        async with self.database.session_factory() as session:
            rows = await session.execute(
                select(DocumentTypeModel.id, DocumentTypeModel.name).where(
                    DocumentTypeModel.id.in_(document_type_ids)
                )
            )
            return {row[0]: row[1] for row in rows}

    async def automatic_for(self, document_type_ids: list[UUID]) -> dict[UUID, Certificate]:
        """The certificates that are on and still have their file."""

        if not document_type_ids:
            return {}
        async with self.database.session_factory() as session:
            rows = await session.execute(
                select(CertificateModel, DocumentModel, DocumentFolderModel, AreaModel)
                .join(DocumentModel, CertificateModel.document_id == DocumentModel.id)
                .join(DocumentFolderModel, DocumentModel.folder_id == DocumentFolderModel.id)
                .join(AreaModel, DocumentModel.area_id == AreaModel.id)
                .where(
                    CertificateModel.document_type_id.in_(document_type_ids),
                    CertificateModel.is_active.is_(True),
                )
            )
            return {
                certificate.document_type_id: _certificate(
                    certificate, _document(document, folder, area)
                )
                for certificate, document, folder, area in rows
            }

    async def save(
        self, document_type_id: UUID, document_id: UUID | None, is_active: bool, user_id: UUID
    ) -> Certificate:
        """Create or replace the certificate of a document type."""

        statement = insert(CertificateModel).values(
            id=uuid4(),
            document_type_id=document_type_id,
            document_id=document_id,
            is_active=is_active,
            updated_by=user_id,
        )
        statement = statement.on_conflict_do_update(
            constraint="pqrsf_certificates_document_type_id_key",
            set_={
                "document_id": document_id,
                "is_active": is_active,
                "updated_by": user_id,
                "updated_at": func.now(),
            },
        )
        async with self.database.session_factory() as session:
            await session.execute(statement)
            await session.commit()
        entry = await self.get_entry(document_type_id)
        assert entry is not None and entry.certificate is not None
        return entry.certificate

    async def delete(self, document_type_id: UUID) -> None:
        """Remove the certificate of a document type."""

        async with self.database.session_factory() as session:
            await session.execute(
                CertificateModel.__table__.delete().where(
                    CertificateModel.document_type_id == document_type_id
                )
            )
            await session.commit()

    async def list_documents(
        self, area_slug: str, query: str | None, folder_id: UUID | None, limit: int
    ) -> list[ManagerDocument]:
        """Files of one area, by folder and name."""

        statement = (
            select(DocumentModel, DocumentFolderModel, AreaModel)
            .join(DocumentFolderModel, DocumentModel.folder_id == DocumentFolderModel.id)
            .join(AreaModel, DocumentModel.area_id == AreaModel.id)
            .where(AreaModel.slug == area_slug)
            .order_by(func.lower(DocumentFolderModel.name), func.lower(DocumentModel.name))
            .limit(limit)
        )
        if folder_id is not None:
            statement = statement.where(DocumentModel.folder_id == folder_id)
        if query and query.strip():
            pattern = f"%{query.strip()}%"
            statement = statement.where(
                DocumentModel.name.ilike(pattern) | DocumentModel.original_filename.ilike(pattern)
            )
        async with self.database.session_factory() as session:
            rows = await session.execute(statement)
            return [_document(*row) for row in rows]

    async def get_document(self, document_id: UUID) -> ManagerDocument | None:
        """One file with where it is kept."""

        async with self.database.session_factory() as session:
            row = (
                await session.execute(
                    select(DocumentModel, DocumentFolderModel, AreaModel)
                    .join(DocumentFolderModel, DocumentModel.folder_id == DocumentFolderModel.id)
                    .join(AreaModel, DocumentModel.area_id == AreaModel.id)
                    .where(DocumentModel.id == document_id)
                )
            ).one_or_none()
            return _document(*row) if row else None


class ManagerDocumentFiles:
    """Read the current content of a manager file when an email is about to carry it."""

    def __init__(self, database: Database, storage: S3Storage) -> None:
        """Create the source.

        Args:
            database (Database): Shared database resource.
            storage (S3Storage): The bucket of the document manager.
        """

        self.database = database
        self.storage = storage

    async def get_file(self, document_id: UUID) -> OutgoingFile | None:
        """The document as an attachment, or ``None`` when it was removed."""

        async with self.database.session_factory() as session:
            document = await session.get(DocumentModel, document_id)
        if document is None:
            return None
        content = await self.storage.get_object(document.storage_key)
        return OutgoingFile(document.original_filename, document.content_type, content)
