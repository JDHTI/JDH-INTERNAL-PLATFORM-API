"""Endpoints of the board of Listas principales (``/price-list-updates/main``): every main list
in one table. Included before ``list_router`` so ``/main/...`` is never taken for an id."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Annotated, Literal
from uuid import UUID

from dependency_injector.wiring import Provide, inject
from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field

from app.app_container import ApplicationContainer
from app.modules.areas.presentation.dependencies import require_permission
from app.modules.identity.domain.principal import Principal
from app.modules.identity.presentation.principal import get_current_principal
from app.modules.quotations.application.main_board import MainBoardService, PriceInput
from app.modules.quotations.application.service import Skipped
from app.modules.quotations.domain.board import MAX_KEYS, MAX_PAGE_SIZE, BoardFilter, BoardKey
from app.modules.quotations.domain.vocabulary import WARNING_LABELS
from app.modules.quotations.presentation.router import (
    LineResponse,
    ReviewStatus,
    SkippedResponse,
    WarningResponse,
    _line,
)

router = APIRouter()
_GATE = Depends(require_permission("listas-principales"))
_service = Depends(Provide[ApplicationContainer.v1_container.main_board_service])
_principal = Depends(get_current_principal)

Chip = Literal[
    "sin_marcar", "por_validar", "validada", "cargada", "alza", "objetivo", "encima", "alerta"
]
Sort = Literal["lista", "sku", "precio_actual", "margen_actual", "precio_nuevo", "variacion"]


class BoardListResponse(BaseModel):
    code: str
    name: str | None
    # ``None`` until the list is built on the board.
    quotation_id: UUID | None
    built_at: datetime | None
    line_count: int
    # Rows of each review state: ``sin_marcar``, ``por_validar``, ``validada``, ``cargada``.
    counts: dict[str, int]
    warnings: list[WarningResponse]


class BoardOverviewResponse(BaseModel):
    lists: list[BoardListResponse]
    suppliers: list[str]
    categories: list[str]


class BoardRowResponse(LineResponse):
    """A SKU on one main list, with the update it lives in and its stock."""

    quotation_id: UUID
    quotation_version: int
    price_list_id: str
    price_list_name: str | None
    stock_on_hand: Decimal | None = None
    stock_available: Decimal | None = None
    stock_warehouses: int = 0


class BoardPageResponse(BaseModel):
    items: list[BoardRowResponse]
    total: int
    page: int
    size: int
    counts: dict[str, int]
    # False when the warehouse did not answer the stock of the page.
    stock_read: bool


class BoardKeyModel(BaseModel):
    quotation_id: UUID
    sku: str = Field(min_length=1, max_length=50)


class PriceChange(BoardKeyModel):
    # ``None`` clears the new price.
    new_price: Decimal | None = Field(default=None, gt=0)


class PricesRequest(BaseModel):
    changes: list[PriceChange] = Field(min_length=1, max_length=MAX_KEYS)


class ApplyRequest(BaseModel):
    keys: list[BoardKeyModel] = Field(min_length=1, max_length=MAX_KEYS)
    mode: Literal["optimo", "minimo", "limpiar"]


class ReviewRequest(BaseModel):
    keys: list[BoardKeyModel] = Field(min_length=1, max_length=MAX_KEYS)
    status: ReviewStatus


class NoteRequest(BoardKeyModel):
    note: str | None = Field(default=None, max_length=2000)


class ActionResponse(BaseModel):
    done: int
    skipped: list[SkippedResponse]


def _filter(
    lists: list[str] | None,
    q: str | None,
    suppliers: list[str] | None,
    categories: list[str] | None,
    chip: str | None,
    sort: str,
    desc: bool,
) -> BoardFilter:
    return BoardFilter(
        lists=tuple(dict.fromkeys(code.strip() for code in lists or () if code.strip()))[:20],
        q=(q or "").strip() or None,
        suppliers=tuple(suppliers or ())[:200],
        categories=tuple(categories or ())[:200],
        chip=chip,
        sort=sort,
        descending=desc,
    )


def _skipped(items: tuple[Skipped, ...]) -> list[SkippedResponse]:
    return [SkippedResponse(sku=item.sku, reason=item.reason) for item in items]


ListsQuery = Annotated[list[str] | None, Query()]
TextsQuery = Annotated[list[str] | None, Query()]


@router.get(
    "/main",
    dependencies=[_GATE],
    response_model=BoardOverviewResponse,
    summary="The main lists on the board",
)
@inject
async def overview(
    principal: Principal = _principal,
    service: MainBoardService = _service,
) -> BoardOverviewResponse:
    """Every main list, with its update's counts once built, and the filter options."""

    result = await service.overview(principal)
    return BoardOverviewResponse(
        lists=[
            BoardListResponse(
                code=item.code,
                name=item.name,
                quotation_id=item.quotation_id,
                built_at=item.built_at,
                line_count=item.line_count,
                counts=item.counts,
                warnings=[
                    WarningResponse(code=code, label=WARNING_LABELS.get(code, code))
                    for code in item.warnings
                ],
            )
            for item in result.lists
        ],
        suppliers=list(result.suppliers),
        categories=list(result.categories),
    )


@router.post(
    "/main/lists/{code}/build",
    dependencies=[_GATE],
    response_model=BoardListResponse,
    summary="Build or refresh a main list on the board",
)
@inject
async def build(
    code: str,
    principal: Principal = _principal,
    service: MainBoardService = _service,
) -> BoardListResponse:
    """Reads the list from the warehouse: its first build, or a refresh that keeps the new
    prices, reviews and notes by SKU."""

    item = await service.build(principal, code)
    return BoardListResponse(
        code=item.code,
        name=item.name,
        quotation_id=item.quotation_id,
        built_at=item.built_at,
        line_count=item.line_count,
        counts=item.counts,
        warnings=[
            WarningResponse(code=code, label=WARNING_LABELS.get(code, code))
            for code in item.warnings
        ],
    )


@router.get(
    "/main/rows",
    dependencies=[_GATE],
    response_model=BoardPageResponse,
    summary="A page of the board",
)
@inject
async def rows(
    lists: ListsQuery = None,
    q: str | None = Query(default=None, max_length=120),
    suppliers: TextsQuery = None,
    categories: TextsQuery = None,
    chip: Chip | None = None,
    sort: Sort = "lista",
    desc: bool = False,
    page: int = Query(default=1, ge=1),
    size: int = Query(default=100, ge=1, le=MAX_PAGE_SIZE),
    principal: Principal = _principal,
    service: MainBoardService = _service,
) -> BoardPageResponse:
    """Rows of every main list (or the chosen ``lists``), filtered and sorted; ``counts`` has
    the rows of each chip under the other filters."""

    result = await service.rows(
        principal,
        _filter(lists, q, suppliers, categories, chip, sort, desc),
        page,
        size,
    )
    stock = result.stock or {}
    items = []
    for row in result.page.rows:
        total = stock.get((row.quotation_id, row.line.sku))
        items.append(
            BoardRowResponse(
                **_line(row.line, "changed").model_dump(),
                quotation_id=row.quotation_id,
                quotation_version=row.quotation_version,
                price_list_id=row.price_list_id,
                price_list_name=row.price_list_name,
                stock_on_hand=total.on_hand if total else None,
                stock_available=total.available if total else None,
                stock_warehouses=total.warehouses if total else 0,
            )
        )
    return BoardPageResponse(
        items=items,
        total=result.page.total,
        page=result.page.page,
        size=result.page.size,
        counts=result.page.counts,
        stock_read=result.stock is not None,
    )


@router.get(
    "/main/keys",
    dependencies=[_GATE],
    response_model=list[BoardKeyModel],
    summary="Every row of a filter of the board",
)
@inject
async def keys(
    lists: ListsQuery = None,
    q: str | None = Query(default=None, max_length=120),
    suppliers: TextsQuery = None,
    categories: TextsQuery = None,
    chip: Chip | None = None,
    principal: Principal = _principal,
    service: MainBoardService = _service,
) -> list[BoardKeyModel]:
    """At most 10,000, for a bulk action on every row of the filter."""

    found = await service.keys(
        principal, _filter(lists, q, suppliers, categories, chip, "lista", False)
    )
    return [BoardKeyModel(quotation_id=item.quotation_id, sku=item.sku) for item in found]


@router.put(
    "/main/prices",
    dependencies=[_GATE],
    response_model=ActionResponse,
    summary="Write new prices on rows of any list",
)
@inject
async def prices(
    body: PricesRequest,
    principal: Principal = _principal,
    service: MainBoardService = _service,
) -> ActionResponse:
    """Whole pesos; ``null`` clears it. A new price sends the item back to unmarked."""

    done, skipped = await service.set_prices(
        principal,
        [PriceInput(item.quotation_id, item.sku, item.new_price) for item in body.changes],
    )
    return ActionResponse(done=done, skipped=_skipped(skipped))


@router.post(
    "/main/apply",
    dependencies=[_GATE],
    response_model=ActionResponse,
    summary="Price rows at their optimal or minimum price, or clear them",
)
@inject
async def apply(
    body: ApplyRequest,
    principal: Principal = _principal,
    service: MainBoardService = _service,
) -> ActionResponse:
    done, skipped = await service.apply(
        principal, [BoardKey(item.quotation_id, item.sku) for item in body.keys], body.mode
    )
    return ActionResponse(done=done, skipped=_skipped(skipped))


@router.put(
    "/main/review",
    dependencies=[_GATE],
    response_model=ActionResponse,
    summary="Mark rows of any list",
)
@inject
async def review(
    body: ReviewRequest,
    principal: Principal = _principal,
    service: MainBoardService = _service,
) -> ActionResponse:
    """``por_validar`` and ``sin_marcar``: whoever edits; ``validada``: who validates."""

    done, skipped = await service.mark(
        principal, [BoardKey(item.quotation_id, item.sku) for item in body.keys], body.status
    )
    return ActionResponse(done=done, skipped=_skipped(skipped))


@router.put(
    "/main/note",
    dependencies=[_GATE],
    response_model=BoardRowResponse,
    summary="Write the observation of a row",
)
@inject
async def note(
    body: NoteRequest,
    principal: Principal = _principal,
    service: MainBoardService = _service,
) -> BoardRowResponse:
    row = await service.note(principal, body.quotation_id, body.sku, body.note)
    return BoardRowResponse(
        **_line(row.line, "changed").model_dump(),
        quotation_id=row.quotation_id,
        quotation_version=row.quotation_version,
        price_list_id=row.price_list_id,
        price_list_name=row.price_list_name,
    )


__all__ = ["router"]
