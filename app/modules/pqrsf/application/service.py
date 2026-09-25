"""PQRSF submission and administrator query use cases."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
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
    SanitizedImage,
    clean_optional,
    clean_required,
    storage_key_for,
    validate_idempotency_key,
)
from app.modules.pqrsf.domain.ports import (
    BinaryStorage,
    ImageProcessor,
    MalwareScanner,
    PqrsfRepository,
)
from app.modules.sites.application.service import SiteService


class PqrsfService:
    """Coordinate the public form and protected administrator views."""

    def __init__(
        self,
        repository: PqrsfRepository,
        sites: SiteService,
        storage: BinaryStorage | None = None,
        image_processor: ImageProcessor | None = None,
        malware_scanner: MalwareScanner | None = None,
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
            max_attachments (int): Maximum number of images per request.
            max_attachment_bytes (int): Maximum bytes per image.
            max_total_attachment_bytes (int): Maximum bytes across images.
        """

        self.repository = repository
        self.sites = sites
        self.storage = storage
        self.image_processor = image_processor
        self.malware_scanner = malware_scanner
        self.max_attachments = max_attachments
        self.max_attachment_bytes = max_attachment_bytes
        self.max_total_attachment_bytes = max_total_attachment_bytes

    async def catalog(self) -> tuple[list, list]:
        """Return active request types and sites for the public form."""

        return await self.repository.list_request_types(), await self.sites.list_sites()

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
        reason: str | None,
        description: str,
        idempotency_key: str,
        idempotency_payload: Mapping[str, object],
        images: Sequence[ImageUpload] = (),
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
            reason (str | None): Required only for the first three types.
            description (str): Required request detail.
            idempotency_key (str): UUID used to make retries safe.
            idempotency_payload (Mapping[str, object]): Canonical hash input.
            images (Sequence[ImageUpload]): Optional public image uploads.

        Returns:
            CreateResult: The stored request and whether it was replayed.
        """

        key = validate_idempotency_key(idempotency_key)
        request_type = await self.repository.get_request_type(request_type_id)
        if request_type is None or not request_type.is_active:
            raise NotFoundError("Tipo de solicitud no encontrado")
        site = await self.sites.get_site(site_id)
        if not site.is_active:
            raise InvalidInputError("La sede seleccionada no está disponible")

        reason_value = clean_optional(reason, "motivo", 200)
        if request_type.allows_reason and reason_value is None:
            raise InvalidInputError("El motivo es obligatorio para este tipo de solicitud")
        if not request_type.allows_reason and reason_value is not None:
            raise InvalidInputError("Este tipo de solicitud no admite motivo")

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
            reason=reason_value,
            description=clean_required(description, "descripción", 10_000),
            idempotency_key=key,
            idempotency_hash=payload_hash,
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
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
