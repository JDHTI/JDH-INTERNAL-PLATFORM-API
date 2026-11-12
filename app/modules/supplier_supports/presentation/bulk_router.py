"""Endpoints of the supports creation form and the bulk load.

Mounted at ``/supplier-supports`` *before* the main router: ``/form-schema`` would otherwise be
read as a support id.
"""

from __future__ import annotations

import json
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Literal
from uuid import UUID

from dependency_injector.wiring import Provide, inject
from fastapi import APIRouter, Depends, File, Form, Query, Response, UploadFile
from pydantic import BaseModel

from app.app_container import ApplicationContainer
from app.core.errors import InvalidInputError
from app.modules.areas.presentation.dependencies import require_permission
from app.modules.identity.domain.principal import Principal
from app.modules.identity.presentation.principal import get_current_principal
from app.modules.supplier_supports.application.bulk import SupportBulkService
from app.modules.supplier_supports.domain import fields
from app.modules.supplier_supports.domain.bulk import BulkReport
from app.modules.supplier_supports.infrastructure.bulk_file import template_workbook

router = APIRouter(dependencies=[Depends(require_permission("apoyos"))])
_service = Depends(Provide[ApplicationContainer.v1_container.supplier_support_bulk_service])
_principal = Depends(get_current_principal)
_XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


class RowIssueResponse(BaseModel):
    """A problem of one row of the file."""

    row_number: int
    field: str | None
    message: str


class PreviewRowResponse(BaseModel):
    """A row of the file and what it does: ``crear``, ``actualizar`` or ``sin_cambio``."""

    row_number: int
    supplier_code: str | None
    kind: str
    calc_method: str
    program: str | None
    customer_nit: str | None
    product_sku: str | None
    supplier_value: Decimal | None
    valid_from: date | None
    valid_to: date | None
    action: Literal["crear", "actualizar", "sin_cambio"]
    # The stored support it updates or leaves as it is.
    support_id: UUID | None


class MissingSupportResponse(BaseModel):
    """A stored support the file no longer brings."""

    support_id: UUID
    customer_nit: str | None
    product_sku: str | None
    valid_from: date | None
    valid_to: date | None
    # Whether the load closes it (``expire_missing`` and in force).
    expires: bool


class BulkReportResponse(BaseModel):
    """Result of a bulk load, simulated or applied."""

    applied: bool
    blocked: bool
    sheet: str
    header_row: int
    rows_read: int
    valid: int
    invalid: int
    errors: list[RowIssueResponse]
    errors_truncated: bool
    preview: list[PreviewRowResponse]
    mapped_columns: dict[str, list[str]]
    ignored_columns: list[str]
    profile_used: bool
    created: list[UUID]
    with_conflicts: int
    to_create: int
    to_update: int
    unchanged: int
    missing: int
    to_expire: int
    missing_preview: list[MissingSupportResponse]
    updated: list[UUID]
    expired: list[UUID]
    # The load's trace id once applied (``supplier_support_loads``, ``carga_id`` in the DWH).
    load_id: UUID | None
    # A big load reaches the DWH from this moment (its quiet hours); null = right away.
    publish_not_before: datetime | None


def _json_object(text: str | None, name: str) -> dict[str, Any]:
    if not text or not text.strip():
        return {}
    try:
        value = json.loads(text)
    except json.JSONDecodeError as exc:
        raise InvalidInputError(f"{name} debe ser un objeto JSON válido") from exc
    if not isinstance(value, dict):
        raise InvalidInputError(f"{name} debe ser un objeto JSON")
    return value


def _report(report: BulkReport) -> BulkReportResponse:
    return BulkReportResponse.model_validate(report, from_attributes=True)


@router.get("/form-schema", summary="Fields and rules of the creation form")
async def form_schema(_: Principal = _principal) -> dict[str, object]:
    """Fields (grouped, typed, with choices) and what each calc method requires or shows.

    The creation form renders from this, so a new field or method needs no UI release.
    """

    return fields.form_schema()


@router.get(
    "/bulk/template",
    summary="Bulk load template",
    response_class=Response,
    responses={200: {"content": {_XLSX: {}}}},
)
@inject
async def bulk_template(
    supplier_code: str | None = Query(
        default=None,
        max_length=20,
        description="Follow the layout of this supplier's profile (its own headers, fixed "
        "columns left out)",
    ),
    full: bool = Query(
        default=False,
        description="Every field instead of the short general template",
    ),
    _: Principal = _principal,
    service: SupportBulkService = _service,
) -> Response:
    """The bulk file as an ``.xlsx``: columns to fill, instructions and the valid values."""

    profile = await service.template_profile(supplier_code)
    return Response(
        content=template_workbook(profile, full),
        media_type=_XLSX,
        headers={"Content-Disposition": 'attachment; filename="carga-apoyos.xlsx"'},
    )


@router.post(
    "/bulk",
    response_model=BulkReportResponse,
    summary="Bulk load of supports from a file",
    description=(
        "multipart/form-data with `file` (.xlsx or .csv). Every row goes through the checks of "
        "a manual create and the load is all or nothing: one error blocks it. A simulation "
        "unless `apply=true`. Each row creates the support with its key (supplier, NIT, SKU, "
        "agreement, start date), updates it (only the columns the file brings) or leaves it "
        "as it is; stored supports of the same suppliers and classes the file no longer "
        "brings are reported, and closed the day before with `expire_missing=true`. "
        "`supplier_code` selects the supplier's profile (defaults, sheet, column names). "
        "`defaults` and `column_map` (JSON objects, as form fields) override it for this load: "
        '`{"kind": "tactico_cliente"}`, `{"supplier_value": ["% APOYO"]}`.'
    ),
)
@inject
async def bulk_load(
    file: UploadFile = File(...),  # noqa: B008
    apply: bool = Query(default=False),
    supplier_code: str | None = Query(default=None, max_length=20),
    sheet: str | None = Query(default=None, max_length=100),
    expire_missing: bool = Query(default=False),
    defaults: str | None = Form(default=None),
    column_map: str | None = Form(default=None),
    principal: Principal = _principal,
    service: SupportBulkService = _service,
) -> BulkReportResponse:
    """Simulate the load of a file and, with ``apply`` and no errors, write every row."""

    # One byte over the limit is enough for the service to reject the file.
    content = await file.read(service.max_upload_bytes + 1)
    report = await service.load(
        principal,
        file.filename or "archivo",
        content,
        apply=apply,
        supplier_code=supplier_code,
        sheet=sheet,
        defaults=_json_object(defaults, "defaults"),
        column_map=_json_object(column_map, "column_map"),
        expire_missing=expire_missing,
    )
    return _report(report)


__all__ = ["router"]
