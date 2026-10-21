"""Panel of the certificates sent automatically (PQRSF managers)."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from dependency_injector.wiring import Provide, inject
from fastapi import APIRouter, Depends, Query, Response, status
from pydantic import BaseModel

from app.app_container import ApplicationContainer
from app.modules.identity.domain.principal import Principal
from app.modules.identity.presentation.principal import get_current_principal
from app.modules.pqrsf.application.certificates import CertificateService
from app.modules.pqrsf.domain.certificates import CertificateEntry, ManagerDocument

router = APIRouter(prefix="/pqrsf/certificates")
_service = Depends(Provide[ApplicationContainer.v1_container.pqrsf_certificate_service])
_principal = Depends(get_current_principal)


class ManagerDocumentResponse(BaseModel):
    """A file of the quality document manager, with where it is kept."""

    id: UUID
    name: str
    filename: str
    content_type: str
    size_bytes: int
    folder_id: UUID
    folder_name: str
    area_slug: str
    # «Calidad / Certificados / RUT JDH»: the route of the file in the manager.
    path: str


class CertificateResponse(BaseModel):
    """The file a document type is answered with."""

    is_active: bool
    # False when the file was removed from the manager: nothing is sent until it is replaced.
    has_file: bool
    # Whether a request for the document type is answered without a person right now.
    automatic: bool
    document: ManagerDocumentResponse | None
    updated_at: datetime


class CertificateEntryResponse(BaseModel):
    """A document type of the public form and its certificate, if it has one."""

    document_type_id: UUID
    name: str
    category_code: str | None
    category_name: str | None
    certificate: CertificateResponse | None


class SaveCertificateRequest(BaseModel):
    """The file of a document type."""

    document_id: UUID
    is_active: bool = True


def _document(value: ManagerDocument) -> ManagerDocumentResponse:
    return ManagerDocumentResponse(**vars(value), path=value.path)


def _entry(value: CertificateEntry) -> CertificateEntryResponse:
    certificate = value.certificate
    return CertificateEntryResponse(
        document_type_id=value.document_type_id,
        name=value.name,
        category_code=value.category_code,
        category_name=value.category_name,
        certificate=(
            CertificateResponse(
                is_active=certificate.is_active,
                has_file=certificate.document is not None,
                automatic=certificate.automatic,
                document=_document(certificate.document) if certificate.document else None,
                updated_at=certificate.updated_at,
            )
            if certificate
            else None
        ),
    )


@router.get(
    "", response_model=list[CertificateEntryResponse], summary="Certificates sent automatically"
)
@inject
async def list_certificates(
    principal: Principal = _principal, service: CertificateService = _service
) -> list[CertificateEntryResponse]:
    """Every document type the public form offers (except «Otro»), by category."""

    return [_entry(entry) for entry in await service.list_entries(principal)]


@router.get(
    "/documents",
    response_model=list[ManagerDocumentResponse],
    summary="Files of the quality document manager",
)
@inject
async def search_documents(
    q: str | None = Query(default=None, max_length=100),  # noqa: B008
    folder_id: UUID | None = None,
    principal: Principal = _principal,
    service: CertificateService = _service,
) -> list[ManagerDocumentResponse]:
    """Files of the quality area to choose a certificate from (at most 100)."""

    return [_document(d) for d in await service.search_documents(principal, q, folder_id)]


@router.put(
    "/{document_type_id}",
    response_model=CertificateEntryResponse,
    summary="Set the certificate of a document type",
)
@inject
async def save_certificate(
    document_type_id: UUID,
    body: SaveCertificateRequest,
    principal: Principal = _principal,
    service: CertificateService = _service,
) -> CertificateEntryResponse:
    """Choose the file of a document type and whether it is sent automatically.

    The file must belong to the quality area of the document manager.
    """

    return _entry(await service.save(document_type_id, body.document_id, body.is_active, principal))


@router.delete(
    "/{document_type_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Remove the certificate of a document type",
)
@inject
async def remove_certificate(
    document_type_id: UUID,
    principal: Principal = _principal,
    service: CertificateService = _service,
) -> Response:
    """Its requests open cases again."""

    await service.remove(document_type_id, principal)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


__all__ = ["router"]
