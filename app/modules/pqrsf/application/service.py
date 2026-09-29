"""PQRSF submission and administrator query use cases."""

from __future__ import annotations

import hashlib
import json
import logging
from collections.abc import Mapping, Sequence
from dataclasses import replace
from datetime import UTC, datetime
from uuid import UUID, uuid4

from app.core.errors import (
    ConflictError,
    DependencyUnavailableError,
    InvalidInputError,
    NotFoundError,
)
from app.modules.pqrsf.domain.entities import (
    CreateResult,
    ImageUpload,
    PqrsfAttachment,
    PqrsfFilters,
    PqrsfPage,
    PqrsfRequest,
    RequestedDocument,
    RequestType,
    SanitizedImage,
    clean_optional,
    clean_required,
    storage_key_for,
    validate_idempotency_key,
)
from app.modules.pqrsf.domain.ports import (
    BinaryStorage,
    CaseOpener,
    ImageProcessor,
    MalwareScanner,
    PqrsfRepository,
)
from app.modules.sites.application.service import SiteService

logger = logging.getLogger(__name__)

# Documents one public request can ask for; a case itself admits many more.
_MAX_DOCUMENTS = 30


class PqrsfService:
    """Coordinate the public form and protected administrator views."""

    def __init__(
        self,
        repository: PqrsfRepository,
        sites: SiteService,
        storage: BinaryStorage | None = None,
        image_processor: ImageProcessor | None = None,
        malware_scanner: MalwareScanner | None = None,
        cases: CaseOpener | None = None,
        max_attachments: int = 5,
        max_attachment_bytes: int = 5 * 1024 * 1024,
        max_total_attachment_bytes: int = 25 * 1024 * 1024,
    ) -> None:
        """Create the service.

        Args:
            repository (PqrsfRepository): PQRSF persistence port.
            sites (SiteService): Site catalog use cases.
            storage (BinaryStorage | None): Private object storage adapter.
            image_processor (ImageProcessor | None): Image sanitation adapter.
            malware_scanner (MalwareScanner | None): Antivirus adapter.
            cases (CaseOpener | None): Opens the follow-up case of new requests.
            max_attachments (int): Maximum number of images per request.
            max_attachment_bytes (int): Maximum bytes per image.
            max_total_attachment_bytes (int): Maximum bytes across images.
        """

        self.repository = repository
        self.sites = sites
        self.storage = storage
        self.image_processor = image_processor
        self.malware_scanner = malware_scanner
        self.cases = cases
        self.max_attachments = max_attachments
        self.max_attachment_bytes = max_attachment_bytes
        self.max_total_attachment_bytes = max_total_attachment_bytes

    async def catalog(self) -> tuple[list, list, list]:
        """Return active request types, sites and document categories for the public form."""

        return (
            await self.repository.list_request_types(),
            await self.sites.list_sites(),
            await self.repository.list_document_categories(),
        )

    async def create_request(
        self,
        *,
        request_type_id: UUID,
        site_id: UUID,
        full_name: str,
        business_name: str,
        nit: str,
        address: str,
        department: str,
        city: str,
        email: str,
        subject: str,
        description: str,
        idempotency_key: str,
        idempotency_payload: Mapping[str, object],
        images: Sequence[ImageUpload] = (),
        document_request: bool = False,
        document_category_id: UUID | None = None,
        document_type_ids: Sequence[UUID] = (),
        document_other: str | None = None,
    ) -> CreateResult:
        """Validate and persist an idempotent public request.

        Args:
            request_type_id (UUID): Fixed request classification.
            site_id (UUID): Active submission site.
            full_name (str): Required requester name.
            business_name (str): Required legal/business name.
            nit (str): Required taxpayer identifier.
            address (str): Required address.
            department (str): Required department text.
            city (str): Required city text.
            email (str): Required requester email.
            subject (str): Required one-line summary of what is needed.
            description (str): Required request detail.
            idempotency_key (str): UUID used to make retries safe.
            idempotency_payload (Mapping[str, object]): Canonical hash input.
            images (Sequence[ImageUpload]): Optional public image uploads; not
                accepted on a document request.
            document_request (bool): Whether the customer asks for documents.
            document_category_id (UUID | None): Category of the documents asked for.
            document_type_ids (Sequence[UUID]): Catalog documents asked for.
            document_other (str | None): Free-text document not in the catalog.

        Returns:
            CreateResult: The stored request and whether it was replayed.
        """

        result = await self._store_request(
            request_type_id=request_type_id,
            site_id=site_id,
            full_name=full_name,
            business_name=business_name,
            nit=nit,
            address=address,
            department=department,
            city=city,
            email=email,
            subject=subject,
            description=description,
            idempotency_key=idempotency_key,
            idempotency_payload=idempotency_payload,
            images=images,
            document_request=document_request,
            document_category_id=document_category_id,
            document_type_ids=document_type_ids,
            document_other=document_other,
        )
        return await self._with_case(result)

    async def _with_case(self, result: CreateResult) -> CreateResult:
        """Open (or find) the follow-up case without failing the submission.

        The request is already stored; a failure here is logged and the case
        can be opened later with the recovery endpoint.
        """

        if self.cases is None:
            return result
        try:
            if result.replayed:
                code = await self.cases.code_for_request(result.request.id)
                if code is None:
                    code = (await self.cases.open_for_request(result.request)).code
            else:
                code = (await self.cases.open_for_request(result.request)).code
        except Exception:
            logger.exception("Could not open the follow-up case of PQRSF %s", result.request.id)
            return result
        return replace(result, tracking_code=code)

    async def open_case(self, request_id: UUID) -> str:
        """Open the follow-up case of a stored request that has none.

        Args:
            request_id (UUID): Public request identifier.

        Returns:
            str: The case consecutive.
        """

        if self.cases is None:
            raise DependencyUnavailableError("El seguimiento de casos no está configurado")
        return (await self.cases.open_for_request(await self.get_request(request_id))).code

    async def _store_request(
        self,
        *,
        request_type_id: UUID,
        site_id: UUID,
        full_name: str,
        business_name: str,
        nit: str,
        address: str,
        department: str,
        city: str,
        email: str,
        subject: str,
        description: str,
        idempotency_key: str,
        idempotency_payload: Mapping[str, object],
        images: Sequence[ImageUpload],
        document_request: bool,
        document_category_id: UUID | None,
        document_type_ids: Sequence[UUID],
        document_other: str | None,
    ) -> CreateResult:
        key = validate_idempotency_key(idempotency_key)
        request_type = await self.repository.get_request_type(request_type_id)
        if request_type is None or not request_type.is_active:
            raise NotFoundError("Tipo de solicitud no encontrado")
        site = await self.sites.get_site(site_id)
        if not site.is_active:
            raise InvalidInputError("La sede seleccionada no está disponible")

        subject_value = clean_required(subject, "asunto", 200)
        documents = await self._requested_documents(
            request_type,
            images,
            document_request,
            document_category_id,
            document_type_ids,
            document_other,
        )

        processed_images = await self._process_images(request_type.allows_attachments, images)
        payload = dict(idempotency_payload)
        payload["images"] = [
            {
                "checksum_sha256": checksum,
                "size_bytes": len(image.content),
                "extension": image.extension,
            }
            for image, checksum in processed_images
        ]
        payload_hash = _payload_hash(payload)
        previous = await self.repository.get_by_idempotency_key(key)
        if previous is not None:
            if previous.idempotency_hash != payload_hash:
                raise ConflictError("Idempotency-Key ya fue usado con otros datos")
            return CreateResult(previous, replayed=True)

        request_id = uuid4()
        request = PqrsfRequest(
            id=request_id,
            reference=f"PQRSF-{request_id}",
            request_type=request_type,
            site=site,
            full_name=clean_required(full_name, "nombre completo", 200),
            business_name=clean_required(business_name, "razón social", 200),
            nit=clean_required(nit, "NIT", 30),
            address=clean_required(address, "dirección", 300),
            department=clean_required(department, "departamento", 100),
            city=clean_required(city, "ciudad", 100),
            email=clean_required(email, "correo", 320).lower(),
            subject=subject_value,
            description=clean_required(description, "descripción", 10_000),
            idempotency_key=key,
            idempotency_hash=payload_hash,
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
            document_request=document_request,
            document_category_id=document_category_id if document_request else None,
            documents=documents,
        )
        try:
            stored = await self.repository.create_request(request)
        except ConflictError:
            previous = await self.repository.get_by_idempotency_key(key)
            if previous is not None and previous.idempotency_hash == payload_hash:
                return CreateResult(previous, replayed=True)
            raise

        if not processed_images:
            return CreateResult(stored, replayed=False)

        stored_objects: list[str] = []
        attachments: list[PqrsfAttachment] = []
        try:
            for image, image_checksum in processed_images:
                attachment_id = uuid4()
                key = storage_key_for(stored.id, attachment_id, image.extension)
                if self.storage is None:
                    raise DependencyUnavailableError(
                        "El almacenamiento de imágenes no está configurado"
                    )
                await self.storage.put_object(key, image.content, image.content_type)
                stored_objects.append(key)
                attachments.append(
                    PqrsfAttachment(
                        id=attachment_id,
                        request_id=stored.id,
                        original_filename=image.original_filename,
                        extension=image.extension,
                        content_type=image.content_type,
                        size_bytes=len(image.content),
                        checksum_sha256=image_checksum,
                        storage_key=key,
                        created_at=datetime.now(UTC),
                    )
                )
            await self.repository.add_attachments(attachments)
        except Exception as exc:
            for key in stored_objects:
                try:
                    if self.storage is not None:
                        await self.storage.delete_object(key)
                except Exception:
                    pass
            try:
                await self.repository.delete_request(stored.id)
            except Exception:
                pass
            if isinstance(exc, (InvalidInputError, DependencyUnavailableError)):
                raise
            raise DependencyUnavailableError("No se pudieron guardar las imágenes") from exc
        stored.attachments = attachments
        return CreateResult(stored, replayed=False)

    async def _requested_documents(
        self,
        request_type: RequestType,
        images: Sequence[ImageUpload],
        document_request: bool,
        category_id: UUID | None,
        type_ids: Sequence[UUID],
        other: str | None,
    ) -> list[RequestedDocument]:
        """Validate the documents of a document request against the public catalog."""

        other_value = clean_optional(other, "otro documento", 300)
        if not document_request:
            if category_id is not None or type_ids or other_value is not None:
                raise InvalidInputError(
                    "Los documentos solo se indican en una solicitud de documentos"
                )
            return []
        if not request_type.allows_document_request:
            raise InvalidInputError("Este tipo de solicitud no admite solicitud de documentos")
        if images:
            raise InvalidInputError("Una solicitud de documentos no admite evidencias")
        if category_id is None:
            raise InvalidInputError("Selecciona la categoría del documento")
        category = next(
            (
                item
                for item in await self.repository.list_document_categories()
                if item.id == category_id
            ),
            None,
        )
        if category is None:
            raise InvalidInputError("La categoría del documento no está disponible")
        if len(set(type_ids)) != len(type_ids):
            raise InvalidInputError("Un documento no puede repetirse")
        offered = {document.id for document in category.documents}
        if any(type_id not in offered for type_id in type_ids):
            raise InvalidInputError("Un documento no pertenece a la categoría seleccionada")
        documents = [RequestedDocument(document_type_id=type_id) for type_id in type_ids]
        if other_value is not None:
            documents.append(RequestedDocument(document_type_id=None, description=other_value))
        if not documents:
            raise InvalidInputError("Indica qué documento necesitas")
        if len(documents) > _MAX_DOCUMENTS:
            raise InvalidInputError(f"Máximo {_MAX_DOCUMENTS} documentos por solicitud")
        return documents

    async def _process_images(
        self, allows_attachments: bool, images: Sequence[ImageUpload]
    ) -> list[tuple[SanitizedImage, str]]:
        """Sanitize and scan every image before the request is persisted."""

        if not images:
            return []
        if not allows_attachments:
            raise InvalidInputError("Este tipo de solicitud no admite imágenes")
        if len(images) > self.max_attachments:
            raise InvalidInputError(f"Máximo {self.max_attachments} imágenes por solicitud")
        if self.image_processor is None or self.malware_scanner is None:
            raise DependencyUnavailableError("La validación de imágenes no está configurada")
        total_bytes = 0
        processed: list[tuple[SanitizedImage, str]] = []
        for upload in images:
            if len(upload.content) > self.max_attachment_bytes:
                raise InvalidInputError("Una imagen supera el tamaño máximo permitido")
            total_bytes += len(upload.content)
            if total_bytes > self.max_total_attachment_bytes:
                raise InvalidInputError("El total de imágenes supera el tamaño máximo permitido")
            image = await self.image_processor.sanitize(
                upload.filename, upload.content_type, upload.content
            )
            await self.malware_scanner.scan(image.content)
            processed.append((image, _checksum(image.content)))
        return processed

    async def get_request(self, request_id: UUID) -> PqrsfRequest:
        """Return one administrator request."""

        request = await self.repository.get_request(request_id)
        if request is None:
            raise NotFoundError("Solicitud PQRSF no encontrada")
        return request

    async def list_requests(self, filters: PqrsfFilters, page: int, size: int) -> PqrsfPage:
        """Return one administrator page with bounded pagination."""

        return await self.repository.list_requests(filters, max(1, page), min(max(1, size), 100))

    async def download_attachment(
        self, request_id: UUID, attachment_id: UUID
    ) -> tuple[PqrsfAttachment, bytes]:
        """Read one private image for an administrator."""

        if await self.repository.get_request(request_id) is None:
            raise NotFoundError("Solicitud PQRSF no encontrada")
        attachment = await self.repository.get_attachment(request_id, attachment_id)
        if attachment is None:
            raise NotFoundError("Adjunto PQRSF no encontrado")
        if self.storage is None:
            raise DependencyUnavailableError("El almacenamiento de imágenes no está configurado")
        try:
            content = await self.storage.get_object(attachment.storage_key)
        except Exception as exc:
            raise DependencyUnavailableError("El almacenamiento de imágenes no responde") from exc
        return attachment, content


def _payload_hash(payload: Mapping[str, object]) -> str:
    """Hash canonical form values and sanitized attachment descriptors."""

    encoded = json.dumps(
        payload, default=str, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _checksum(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()
