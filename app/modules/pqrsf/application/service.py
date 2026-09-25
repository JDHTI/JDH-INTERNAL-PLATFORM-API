"""PQRSF submission and administrator query use cases."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from datetime import UTC, datetime
from uuid import UUID, uuid4

from app.core.errors import ConflictError, InvalidInputError, NotFoundError
from app.modules.pqrsf.domain.entities import (
    CreateResult,
    PqrsfFilters,
    PqrsfPage,
    PqrsfRequest,
    clean_optional,
    clean_required,
    validate_idempotency_key,
)
from app.modules.pqrsf.domain.ports import PqrsfRepository
from app.modules.sites.application.service import SiteService


class PqrsfService:
    """Coordinate the public form and protected administrator views."""

    def __init__(self, repository: PqrsfRepository, sites: SiteService) -> None:
        """Create the service.

        Args:
            repository (PqrsfRepository): PQRSF persistence port.
            sites (SiteService): Site catalog use cases.
        """

        self.repository = repository
        self.sites = sites

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

        Returns:
            CreateResult: The stored request and whether it was replayed.
        """

        key = validate_idempotency_key(idempotency_key)
        payload_hash = _payload_hash(idempotency_payload)
        previous = await self.repository.get_by_idempotency_key(key)
        if previous is not None:
            if previous.idempotency_hash != payload_hash:
                raise ConflictError("Idempotency-Key ya fue usado con otros datos")
            return CreateResult(previous, replayed=True)

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
        return CreateResult(stored, replayed=False)

    async def get_request(self, request_id: UUID) -> PqrsfRequest:
        """Return one administrator request."""

        request = await self.repository.get_request(request_id)
        if request is None:
            raise NotFoundError("Solicitud PQRSF no encontrada")
        return request

    async def list_requests(self, filters: PqrsfFilters, page: int, size: int) -> PqrsfPage:
        """Return one administrator page with bounded pagination."""

        return await self.repository.list_requests(filters, max(1, page), min(max(1, size), 100))


def _payload_hash(payload: Mapping[str, object]) -> str:
    """Hash canonical form values; attachments extend this in the upload layer."""

    encoded = json.dumps(
        payload, default=str, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()
