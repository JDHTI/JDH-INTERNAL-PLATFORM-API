"""Endpoints of the list updates' item review, staging, loads, item detail and liquor tax
(``/price-list-updates``).

A list update itself is read and edited through ``/quotations``; this router holds what only
list updates have. The services check each call against the module of its kind
(``actualizacion-listas`` or ``listas-principales``).
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Literal
from uuid import UUID

from dependency_injector.wiring import Provide, inject
from fastapi import APIRouter, Depends, Query, Response, status
from pydantic import BaseModel, Field

from app.app_container import ApplicationContainer
from app.modules.areas.presentation.dependencies import require_permission
from app.modules.identity.domain.principal import Principal
from app.modules.identity.presentation.principal import get_current_principal
from app.modules.quotations.application.liquor_taxes import ChangeInput, LiquorTaxService
from app.modules.quotations.application.list_review import ListReviewService
from app.modules.quotations.domain import liquor
from app.modules.quotations.domain.entities import PriceListLoad
from app.modules.quotations.domain.liquor import LiquorSummary, LiquorTaxUpdate, TaxValue
from app.modules.quotations.presentation.router import (
    ListKind,
    QuotationResponse,
    ReviewStatus,
    SkippedResponse,
    _detail,
    _file,
)

router = APIRouter()
_LIST_GATE = Depends(require_permission("actualizacion-listas", "listas-principales"))
_MAIN_GATE = Depends(require_permission("listas-principales", "impuesto-licor"))
_review = Depends(Provide[ApplicationContainer.v1_container.list_review_service])
_liquor_service = Depends(Provide[ApplicationContainer.v1_container.liquor_tax_service])
_principal = Depends(get_current_principal)
_XLSX_RESPONSE: dict[int | str, dict[str, object]] = {
    200: {
        "content": {
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": {},
            "text/plain": {},
        }
    }
}
FileFormat = Literal["xlsx", "txt"]
# ``lista`` (Actualización de listas) or ``principal`` (Listas principales).
_LIST_KIND = Query(default="lista")
_ACTIVATION = Query(default=None)


# ---------------------------------------------------------------- review


class ReviewRequest(BaseModel):
    version: int = Field(ge=1)
    skus: list[str] = Field(min_length=1, max_length=10_000)
    status: ReviewStatus


class NoteRequest(BaseModel):
    version: int = Field(ge=1)
    note: str | None = Field(default=None, max_length=2000)


@router.put(
    "/{quotation_id}/review",
    dependencies=[_LIST_GATE],
    response_model=QuotationResponse,
    summary="Mark items of a list update",
)
@inject
async def review(
    quotation_id: UUID,
    body: ReviewRequest,
    principal: Principal = _principal,
    service: ListReviewService = _review,
) -> QuotationResponse:
    """``por_validar`` or ``sin_marcar`` (whoever edits it) and ``validada`` or taking an item
    out of it (whoever validates the kind). Items that cannot be marked come in ``skipped``."""

    detail, skipped = await service.mark(
        principal, quotation_id, body.version, body.skus, body.status
    )
    response = _detail(detail)
    response.skipped = [SkippedResponse(sku=item.sku, reason=item.reason) for item in skipped]
    return response


@router.put(
    "/{quotation_id}/lines/{sku}/note",
    dependencies=[_LIST_GATE],
    response_model=QuotationResponse,
    summary="Write the observation of an item",
)
@inject
async def note(
    quotation_id: UUID,
    sku: str,
    body: NoteRequest,
    principal: Principal = _principal,
    service: ListReviewService = _review,
) -> QuotationResponse:
    """Up to 500 characters; empty clears it. Any state."""

    return _detail(await service.set_note(principal, quotation_id, body.version, sku, body.note))


# ---------------------------------------------------------------- item detail


class StockRowResponse(BaseModel):
    warehouse: str
    warehouse_name: str | None
    center_code: str | None
    on_hand: Decimal
    available: Decimal | None


class PriceVersionResponse(BaseModel):
    valid_from: date
    valid_to: date | None
    price: Decimal
    value_tax_amount: Decimal | None


class MonthlySaleResponse(BaseModel):
    month: date
    price: Decimal
    quantity: Decimal


class InsightResponse(BaseModel):
    """Stock by warehouse, list price versions and the monthly sale price of an item."""

    sku: str
    # The warehouses the stock is read from.
    warehouses: list[str]
    stock: list[StockRowResponse]
    stock_at: datetime | None
    history: list[PriceVersionResponse]
    # The first version the warehouse keeps; the main lists only have them from 2026.
    history_since: date | None
    sales: list[MonthlySaleResponse]
    # Whose sales: ``cliente`` (the update's customer), ``lista`` (its branches) or
    # ``compania`` (a list of thousands of branches: the whole company).
    sales_scope: Literal["cliente", "lista", "compania"]
    sales_read: bool


@router.get(
    "/{quotation_id}/lines/{sku}/insight",
    dependencies=[_LIST_GATE],
    response_model=InsightResponse,
    summary="Stock, price history and monthly sales of an item",
)
@inject
async def insight(
    quotation_id: UUID,
    sku: str,
    principal: Principal = _principal,
    service: ListReviewService = _review,
) -> InsightResponse:
    """Stock of the list's sellable warehouses (the morning's photo), the list price's
    versions of the last 24 months and the monthly net price to the list's customers in the
    last 12."""

    result = await service.insight(principal, quotation_id, sku)
    return InsightResponse(
        sku=result.sku,
        warehouses=list(result.warehouses),
        stock=[
            StockRowResponse(
                warehouse=row.warehouse,
                warehouse_name=row.warehouse_name,
                center_code=row.center_code,
                on_hand=row.on_hand,
                available=row.available,
            )
            for row in result.stock
        ],
        stock_at=max((row.snapshot_at for row in result.stock if row.snapshot_at), default=None),
        history=[
            PriceVersionResponse(
                valid_from=item.valid_from,
                valid_to=item.valid_to,
                price=item.price,
                value_tax_amount=item.value_tax_amount,
            )
            for item in result.history
        ],
        history_since=result.history[0].valid_from if result.history else None,
        sales=[
            MonthlySaleResponse(month=item.month, price=item.price, quantity=item.quantity)
            for item in result.sales
        ],
        sales_scope=result.sales_scope,  # type: ignore[arg-type]
        sales_read=result.sales_read,
    )


class StockTotalResponse(BaseModel):
    on_hand: Decimal
    available: Decimal | None
    warehouses: int


class StockTotalsResponse(BaseModel):
    warehouses: list[str]
    items: dict[str, StockTotalResponse]


@router.get(
    "/{quotation_id}/stock",
    dependencies=[_LIST_GATE],
    response_model=StockTotalsResponse,
    summary="Stock of every item of a list update",
)
@inject
async def stock(
    quotation_id: UUID,
    principal: Principal = _principal,
    service: ListReviewService = _review,
) -> StockTotalsResponse:
    """Stock in the list's sellable warehouses, summed per SKU; a SKU without stock is not
    in ``items``."""

    warehouses, totals = await service.stock_totals(principal, quotation_id)
    return StockTotalsResponse(
        warehouses=list(warehouses),
        items={
            sku: StockTotalResponse(
                on_hand=total.on_hand, available=total.available, warehouses=total.warehouses
            )
            for sku, total in totals.items()
        },
    )


# ---------------------------------------------------------------- staging


class StagedItemResponse(BaseModel):
    line_id: UUID
    quotation_id: UUID
    quotation_name: str | None
    sku: str
    description: str | None
    unit: str | None
    # The list's price in force when the update was built.
    current_price: Decimal | None
    new_price: Decimal
    variation_pct: Decimal | None
    value_tax_key: str | None
    value_tax_amount: Decimal | None
    note: str | None
    validated_by: str | None
    validated_at: datetime | None


class StagedListResponse(BaseModel):
    price_list_id: str
    price_list_name: str | None
    item_count: int
    items: list[StagedItemResponse]


class LoadRequest(BaseModel):
    kind: ListKind
    line_ids: list[UUID] = Field(min_length=1, max_length=10_000)
    activation_date: date | None = None


class LoadResponse(BaseModel):
    id: UUID
    kind: ListKind
    price_list_id: str
    price_list_name: str | None
    activation_date: date
    line_count: int
    created_by: str
    created_at: datetime
    # Only on GET /loads/{id}: what it wrote.
    rows: list[dict[str, object]] | None = None


def _load(item: PriceListLoad, with_rows: bool = False) -> LoadResponse:
    return LoadResponse(
        id=item.id,
        kind=item.kind,  # type: ignore[arg-type]
        price_list_id=item.price_list_id,
        price_list_name=item.price_list_name,
        activation_date=item.activation_date,
        line_count=item.line_count,
        created_by=item.created_by,
        created_at=item.created_at,
        rows=[dict(row) for row in item.rows] if with_rows else None,
    )


@router.get(
    "/staging",
    dependencies=[_LIST_GATE],
    response_model=list[StagedListResponse],
    summary="Validated items waiting to be loaded, by list",
)
@inject
async def staging(
    kind: ListKind = _LIST_KIND,
    principal: Principal = _principal,
    service: ListReviewService = _review,
) -> list[StagedListResponse]:
    """Every list of the kind with validated items not loaded in SIESA yet."""

    return [
        StagedListResponse(
            price_list_id=group.price_list_id,
            price_list_name=group.price_list_name,
            item_count=len(group.items),
            items=[
                StagedItemResponse(
                    line_id=item.line.id,
                    quotation_id=item.quotation_id,
                    quotation_name=item.quotation_name,
                    sku=item.line.sku,
                    description=item.line.description,
                    unit=item.line.unit,
                    current_price=item.line.last_price,
                    new_price=item.line.new_price,  # type: ignore[arg-type]
                    variation_pct=item.line.variation_pct,
                    value_tax_key=item.line.value_tax_key,
                    value_tax_amount=item.line.value_tax_amount,
                    note=item.line.note,
                    validated_by=item.line.reviewed_by,
                    validated_at=item.line.reviewed_at,
                )
                for item in group.items
            ],
        )
        for group in await service.staging(principal, kind)
    ]


@router.get(
    "/staging/{price_list_id}/file.{fmt}",
    dependencies=[_LIST_GATE],
    response_class=Response,
    responses=_XLSX_RESPONSE,
    summary="SIESA files of a list's staging",
)
@inject
async def staging_file(
    price_list_id: str,
    fmt: FileFormat,
    kind: ListKind = _LIST_KIND,
    activation_date: date | None = _ACTIVATION,
    principal: Principal = _principal,
    service: ListReviewService = _review,
) -> Response:
    """The workbook (``Precios`` and ``Impuestos en valor``) or the import file (``0126`` and
    ``0127``) of every staged item of the list. Who validates the kind only."""

    result = await service.staging_file(principal, kind, price_list_id, fmt, activation_date)
    return _file(result.content, result.filename, result.media_type)


@router.post(
    "/staging/{price_list_id}/loads",
    dependencies=[_LIST_GATE],
    response_model=LoadResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Mark a list's staged items as loaded in SIESA",
)
@inject
async def create_load(
    price_list_id: str,
    body: LoadRequest,
    principal: Principal = _principal,
    service: ListReviewService = _review,
) -> LoadResponse:
    """``line_ids`` are the items of the files downloaded; one that left the staging refuses
    the load (409)."""

    return _load(
        await service.load(principal, body.kind, price_list_id, body.line_ids, body.activation_date)
    )


@router.get(
    "/loads",
    dependencies=[_LIST_GATE],
    response_model=list[LoadResponse],
    summary="Latest loads in SIESA",
)
@inject
async def loads(
    kind: ListKind = _LIST_KIND,
    limit: int = Query(default=30, ge=1, le=100),
    principal: Principal = _principal,
    service: ListReviewService = _review,
) -> list[LoadResponse]:
    return [_load(item) for item in await service.loads(principal, kind, limit)]


# ---------------------------------------------------------------- liquor tax


class TaxValueResponse(BaseModel):
    amount: Decimal | None
    lists: list[str]


class LiquorResponse(BaseModel):
    sku: str
    description: str | None
    tax_key: str
    tax_key_name: str | None
    tax_group: str | None
    national: Decimal | None
    coast: Decimal | None
    national_values: list[TaxValueResponse]
    coast_values: list[TaxValueResponse]
    master_national: Decimal | None
    master_coast: Decimal | None
    lists_with_price: int
    flags: list[str]
    flag_labels: list[str]


class LiquorSuspectResponse(BaseModel):
    sku: str
    description: str | None
    tax_group: str | None
    tax_group_name: str | None
    lists_with_value: int


class LiquorOverviewResponse(BaseModel):
    items: list[LiquorResponse]
    suspects: list[LiquorSuspectResponse]
    coast_lists: list[str]
    # The caller changes the liquor tax and generates its files (``impuesto-licor: manage``).
    can_manage: bool = False


class LiquorChangeRequest(BaseModel):
    sku: str = Field(min_length=1, max_length=50)
    national: Decimal = Field(ge=0)
    coast: Decimal = Field(ge=0)


class LiquorUpdateRequest(BaseModel):
    changes: list[LiquorChangeRequest] = Field(min_length=1, max_length=liquor.MAX_CHANGES)
    note: str | None = Field(default=None, max_length=2000)
    # When SIESA activates the files (default tomorrow); never in the past.
    activation_date: date | None = None


class LiquorCancelRequest(BaseModel):
    version: int = Field(ge=1)
    note: str | None = Field(default=None, max_length=2000)


class LiquorVersionRequest(BaseModel):
    version: int = Field(ge=1)


class LiquorChangeResponse(BaseModel):
    sku: str
    description: str | None
    tax_key: str
    tax_group: str | None
    national: Decimal
    coast: Decimal
    previous_national: Decimal | None
    previous_coast: Decimal | None
    lists: int


class LiquorUpdateResponse(BaseModel):
    id: UUID
    status: Literal["pendiente_validacion", "pendiente_carga", "cargada", "devuelta"]
    status_label: str
    changes: list[LiquorChangeResponse]
    # Prices it writes (list × SKU), fixed when it was validated.
    row_count: int
    list_count: int
    note: str | None
    review_note: str | None
    activation_date: date | None
    created_by: str
    created_at: datetime
    validated_by: str | None
    validated_at: datetime | None
    uploaded_by: str | None
    uploaded_at: datetime | None
    version: int


def _values(values: tuple[TaxValue, ...]) -> list[TaxValueResponse]:
    return [TaxValueResponse(amount=item.amount, lists=list(item.lists)) for item in values]


def _liquor(item: LiquorSummary) -> LiquorResponse:
    return LiquorResponse(
        sku=item.product.sku,
        description=item.product.description,
        tax_key=item.product.tax_key,
        tax_key_name=item.product.tax_key_name,
        tax_group=item.product.tax_group,
        national=item.national,
        coast=item.coast,
        national_values=_values(item.national_values),
        coast_values=_values(item.coast_values),
        master_national=item.master_national,
        master_coast=item.master_coast,
        lists_with_price=item.lists_with_price,
        flags=list(item.flags),
        flag_labels=[liquor.FLAG_LABELS.get(flag, flag) for flag in item.flags],
    )


def _liquor_update(item: LiquorTaxUpdate) -> LiquorUpdateResponse:
    return LiquorUpdateResponse(
        id=item.id,
        status=item.status,  # type: ignore[arg-type]
        status_label=liquor.STATUS_LABELS.get(item.status, item.status),
        changes=[
            LiquorChangeResponse(
                sku=change.sku,
                description=change.description,
                tax_key=change.tax_key,
                tax_group=change.tax_group,
                national=change.national,
                coast=change.coast,
                previous_national=change.previous_national,
                previous_coast=change.previous_coast,
                lists=change.lists,
            )
            for change in item.changes
        ],
        row_count=len(item.export_rows),
        list_count=len({row["price_list_id"] for row in item.export_rows}),
        note=item.note,
        review_note=item.review_note,
        activation_date=item.activation_date,
        created_by=item.created_by,
        created_at=item.created_at,
        validated_by=item.validated_by,
        validated_at=item.validated_at,
        uploaded_by=item.uploaded_by,
        uploaded_at=item.uploaded_at,
        version=item.version,
    )


@router.get(
    "/liquor-taxes",
    dependencies=[_MAIN_GATE],
    response_model=LiquorOverviewResponse,
    summary="Liquor tax of every active liquor across the lists",
)
@inject
async def liquor_taxes(
    principal: Principal = _principal,
    service: LiquorTaxService = _liquor_service,
) -> LiquorOverviewResponse:
    """Each active product with a liquor key: the value of each regional, its distinct values
    with their lists, the margin master's values and what does not add up; and the liquors of
    a liquor tax group without the key."""

    result = await service.overview(principal)
    return LiquorOverviewResponse(
        items=[_liquor(item) for item in result.summaries],
        suspects=[
            LiquorSuspectResponse(
                sku=item.sku,
                description=item.description,
                tax_group=item.tax_group,
                tax_group_name=item.tax_group_name,
                lists_with_value=item.lists_with_value,
            )
            for item in result.suspects
        ],
        coast_lists=list(result.coast_lists),
        can_manage=result.can_manage,
    )


@router.get(
    "/liquor-taxes/updates",
    dependencies=[_MAIN_GATE],
    response_model=list[LiquorUpdateResponse],
    summary="Liquor tax changes, newest first",
)
@inject
async def liquor_updates(
    principal: Principal = _principal,
    service: LiquorTaxService = _liquor_service,
) -> list[LiquorUpdateResponse]:
    return [_liquor_update(item) for item in await service.list(principal)]


@router.post(
    "/liquor-taxes/updates",
    dependencies=[_MAIN_GATE],
    response_model=LiquorUpdateResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Change the liquor tax and generate its SIESA files",
)
@inject
async def create_liquor_update(
    body: LiquorUpdateRequest,
    principal: Principal = _principal,
    service: LiquorTaxService = _liquor_service,
) -> LiquorUpdateResponse:
    """New Nacional and Costa values per SKU, with no validation: the prices of every active
    list are fixed now and the files download at once. Only ``impuesto-licor: manage``."""

    return _liquor_update(
        await service.create(
            principal,
            [ChangeInput(item.sku, item.national, item.coast) for item in body.changes],
            body.note,
            body.activation_date,
        )
    )


@router.post(
    "/liquor-taxes/updates/{update_id}/cancel",
    dependencies=[_MAIN_GATE],
    response_model=LiquorUpdateResponse,
    summary="Cancel a liquor tax change not loaded in SIESA",
)
@inject
async def cancel_liquor_update(
    update_id: UUID,
    body: LiquorCancelRequest,
    principal: Principal = _principal,
    service: LiquorTaxService = _liquor_service,
) -> LiquorUpdateResponse:
    return _liquor_update(await service.cancel(principal, update_id, body.version, body.note))


@router.post(
    "/liquor-taxes/updates/{update_id}/uploaded",
    dependencies=[_MAIN_GATE],
    response_model=LiquorUpdateResponse,
    summary="Mark a liquor tax change as loaded in SIESA",
)
@inject
async def liquor_update_uploaded(
    update_id: UUID,
    body: LiquorVersionRequest,
    principal: Principal = _principal,
    service: LiquorTaxService = _liquor_service,
) -> LiquorUpdateResponse:
    """The margin master takes the new values."""

    return _liquor_update(await service.mark_uploaded(principal, update_id, body.version))


@router.get(
    "/liquor-taxes/updates/{update_id}/file.{fmt}",
    dependencies=[_MAIN_GATE],
    response_class=Response,
    responses=_XLSX_RESPONSE,
    summary="SIESA files of a liquor tax change",
)
@inject
async def liquor_update_file(
    update_id: UUID,
    fmt: FileFormat,
    principal: Principal = _principal,
    service: LiquorTaxService = _liquor_service,
) -> Response:
    """Prices (``0126``, unchanged) and their new value tax (``0127``) on every list."""

    result = await service.file(principal, update_id, fmt)
    return _file(result.content, result.filename, result.media_type)


__all__ = ["router"]
