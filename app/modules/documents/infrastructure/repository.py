"""PostgreSQL implementation of the document repository."""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import delete, exists, func, or_, select
from sqlalchemy.exc import IntegrityError

from app.core.errors import ConflictError
from app.infrastructure.database import Database
from app.modules.documents.domain.entities import Document, DocumentFolder
from app.modules.documents.domain.ports import SearchFilters
from app.modules.documents.infrastructure.models import DocumentFolderModel, DocumentModel

_DOCUMENT_FIELDS = (
    "id",
    "area_id",
    "folder_id",
    "name",
    "original_filename",
    "extension",
    "content_type",
    "size_bytes",
    "checksum_sha256",
    "storage_key",
    "description",
    "tags",
    "uploaded_by",
    "created_at",
    "updated_at",
)


def _folder(m: DocumentFolderModel) -> DocumentFolder:
    return DocumentFolder(
        id=m.id,
        area_id=m.area_id,
        name=m.name,
        description=m.description,
        created_by=m.created_by,
        created_at=m.created_at,
        updated_at=m.updated_at,
    )


def _document(m: DocumentModel) -> Document:
    return Document(**{name: getattr(m, name) for name in _DOCUMENT_FIELDS})


class SqlAlchemyDocumentRepository:
    """Persist folders and the document index; each operation owns its transaction."""

    def __init__(self, database: Database) -> None:
        """Create the repository.

        Args:
            database (Database): Shared engine and session factory.
        """

        self.database = database

    async def _commit(self, session, conflict: str) -> None:
        try:
            await session.commit()
        except IntegrityError as exc:
            raise ConflictError(conflict) from exc

    # --- Folders ---

    async def list_folders(self, area_id: UUID) -> list[tuple[DocumentFolder, int]]:
        """List the folders of an area with their document count.

        Args:
            area_id (UUID): The area.

        Returns:
            list[tuple[DocumentFolder, int]]: Folders ordered by name, each with
                how many documents it holds.
        """

        counts = (
            select(DocumentModel.folder_id, func.count().label("n"))
            .where(DocumentModel.area_id == area_id)
            .group_by(DocumentModel.folder_id)
            .subquery()
        )
        query = (
            select(DocumentFolderModel, func.coalesce(counts.c.n, 0))
            .outerjoin(counts, counts.c.folder_id == DocumentFolderModel.id)
            .where(DocumentFolderModel.area_id == area_id)
            .order_by(func.lower(DocumentFolderModel.name))
        )
        async with self.database.session_factory() as session:
            result = await session.execute(query)
            return [(_folder(f), int(n)) for f, n in result.all()]

    async def get_folder(self, area_id: UUID, folder_id: UUID) -> DocumentFolder | None:
        """Find a folder inside a given area.

        Args:
            area_id (UUID): Area that must own the folder.
            folder_id (UUID): The folder.

        Returns:
            DocumentFolder | None: The folder when it belongs to the area.
        """

        async with self.database.session_factory() as session:
            result = await session.execute(
                select(DocumentFolderModel).where(
                    DocumentFolderModel.area_id == area_id, DocumentFolderModel.id == folder_id
                )
            )
            model = result.scalar_one_or_none()
            return _folder(model) if model else None

    async def folder_name_taken(
        self, area_id: UUID, name: str, exclude_id: UUID | None = None
    ) -> bool:
        """Check whether the area already has a folder with this name.

        Args:
            area_id (UUID): The area.
            name (str): Name compared case-insensitively.
            exclude_id (UUID | None): Folder to ignore (the one being edited).

        Returns:
            bool: ``True`` when the name is in use.
        """

        condition = (DocumentFolderModel.area_id == area_id) & (
            func.lower(DocumentFolderModel.name) == name.lower()
        )
        if exclude_id is not None:
            condition = condition & (DocumentFolderModel.id != exclude_id)
        async with self.database.session_factory() as session:
            return bool((await session.execute(select(exists().where(condition)))).scalar())

    async def create_folder(
        self, area_id: UUID, name: str, description: str | None, created_by: str
    ) -> DocumentFolder:
        """Persist a folder.

        Args:
            area_id (UUID): Owning area.
            name (str): Folder name, unique inside the area.
            description (str | None): Optional description.
            created_by (str): Email of the creator.

        Returns:
            DocumentFolder: The stored folder.
        """

        model = DocumentFolderModel(
            area_id=area_id, name=name, description=description, created_by=created_by
        )
        async with self.database.session_factory() as session:
            session.add(model)
            await self._commit(session, "Ya existe una carpeta con ese nombre en el área")
            await session.refresh(model)
            return _folder(model)

    async def save_folder(self, folder: DocumentFolder) -> DocumentFolder:
        """Persist changes to a folder.

        Args:
            folder (DocumentFolder): Folder with updated fields.

        Returns:
            DocumentFolder: The stored folder.
        """

        async with self.database.session_factory() as session:
            model = await session.get(DocumentFolderModel, folder.id)
            model.name = folder.name
            model.description = folder.description
            await self._commit(session, "Ya existe una carpeta con ese nombre en el área")
            await session.refresh(model)
            return _folder(model)

    async def delete_folder(self, folder_id: UUID) -> None:
        """Delete a folder.

        Args:
            folder_id (UUID): Folder to delete; fails with a conflict while it
                still holds documents.

        Returns:
            None
        """

        async with self.database.session_factory() as session:
            await session.execute(
                delete(DocumentFolderModel).where(DocumentFolderModel.id == folder_id)
            )
            await self._commit(session, "La carpeta tiene documentos")

    async def count_folder_documents(self, folder_id: UUID) -> int:
        """Count the documents inside a folder.

        Args:
            folder_id (UUID): The folder.

        Returns:
            int: Number of documents.
        """

        async with self.database.session_factory() as session:
            result = await session.execute(
                select(func.count())
                .select_from(DocumentModel)
                .where(DocumentModel.folder_id == folder_id)
            )
            return int(result.scalar() or 0)

    # --- Documents ---

    async def get_document(self, document_id: UUID) -> Document | None:
        """Find a document by identifier.

        Args:
            document_id (UUID): The document.

        Returns:
            Document | None: The document when it exists.
        """

        async with self.database.session_factory() as session:
            model = await session.get(DocumentModel, document_id)
            return _document(model) if model else None

    async def document_name_taken(
        self, folder_id: UUID, name: str, exclude_id: UUID | None = None
    ) -> bool:
        """Check whether the folder already has a document with this name.

        Args:
            folder_id (UUID): The folder.
            name (str): Name compared case-insensitively.
            exclude_id (UUID | None): Document to ignore (the one being edited).

        Returns:
            bool: ``True`` when the name is in use.
        """

        condition = (DocumentModel.folder_id == folder_id) & (
            func.lower(DocumentModel.name) == name.lower()
        )
        if exclude_id is not None:
            condition = condition & (DocumentModel.id != exclude_id)
        async with self.database.session_factory() as session:
            return bool((await session.execute(select(exists().where(condition)))).scalar())

    async def add_document(self, document: Document) -> Document:
        """Persist a new document index entry.

        Args:
            document (Document): Entry with its identifier already assigned
                (the storage key is derived from it).

        Returns:
            Document: The stored entry.
        """

        values = {n: getattr(document, n) for n in _DOCUMENT_FIELDS}
        values.pop("created_at")
        values.pop("updated_at")
        model = DocumentModel(**values)
        async with self.database.session_factory() as session:
            session.add(model)
            await self._commit(session, "Ya existe un documento con ese nombre en la carpeta")
            await session.refresh(model)
            return _document(model)

    async def save_document(self, document: Document) -> Document:
        """Persist metadata changes of a document.

        Args:
            document (Document): Entry with updated name, description or tags.

        Returns:
            Document: The stored entry.
        """

        async with self.database.session_factory() as session:
            model = await session.get(DocumentModel, document.id)
            model.name = document.name
            model.description = document.description
            model.tags = list(document.tags)
            await self._commit(session, "Ya existe un documento con ese nombre en la carpeta")
            await session.refresh(model)
            return _document(model)

    async def delete_document(self, document_id: UUID) -> None:
        """Delete a document index entry.

        Args:
            document_id (UUID): The document.

        Returns:
            None
        """

        async with self.database.session_factory() as session:
            await session.execute(delete(DocumentModel).where(DocumentModel.id == document_id))
            await session.commit()

    async def search(
        self, filters: SearchFilters, page: int, size: int
    ) -> tuple[list[Document], int]:
        """Search documents, newest first.

        Args:
            filters (SearchFilters): Criteria; tags must all match.
            page (int): 1-based page number.
            size (int): Page size.

        Returns:
            tuple[list[Document], int]: The page of documents and the total count.
        """

        query = select(DocumentModel)
        if filters.area_ids is not None:
            if not filters.area_ids:
                return [], 0
            query = query.where(DocumentModel.area_id.in_(list(filters.area_ids)))
        if filters.folder_id is not None:
            query = query.where(DocumentModel.folder_id == filters.folder_id)
        if filters.text:
            pattern = f"%{filters.text}%"
            query = query.where(
                or_(
                    DocumentModel.name.ilike(pattern),
                    DocumentModel.original_filename.ilike(pattern),
                    DocumentModel.description.ilike(pattern),
                )
            )
        if filters.tags:
            # Every requested tag (AND); served by the GIN index.
            query = query.where(DocumentModel.tags.contains(list(filters.tags)))
        if filters.extension:
            query = query.where(DocumentModel.extension == filters.extension)

        async with self.database.session_factory() as session:
            total = (
                await session.execute(select(func.count()).select_from(query.subquery()))
            ).scalar() or 0
            result = await session.execute(
                query.order_by(DocumentModel.created_at.desc(), DocumentModel.id)
                .offset((page - 1) * size)
                .limit(size)
            )
            return [_document(m) for m in result.scalars()], int(total)
