"""Endpoints of the support settlement (``/support-settlements``)."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Literal
from urllib.parse import quote
from uuid import UUID

from dependency_injector.wiring import Provide, inject
from fastapi import APIRouter, Depends, Path, Query, Response
from pydantic import BaseModel

from app.app_container import ApplicationContainer
from app.modules.areas.presentation.dependencies import require_permission
from app.modules.identity.domain.principal import Principal
from app.modules.identity.presentation.principal import get_current_principal
from app.modules.support_settlements.application.service import (
    MAX_PAGE_SIZE,
    SettlementService,
)
from app.modules.support_settlements.domain.entities import (
    COST_BASIS_LABELS,
    ITEM_STATUS_LABELS,
    SALE_FLAG_LABELS,
    STATUS_LABELS,
    Settlement,
)
from app.modules.support_settlements.domain.modules import SETTLEMENTS
from app.modules.support_settlements.domain.rules import margin, recomposed_margin

router = APIRouter()
_GATE = Depends(require_permission(SETTLEMENTS))
_service = Depends(Provide[ApplicationContainer.v1_container.support_settlement_service])
_principal = Depends(get_current_principal)
_PERIOD = Path(pattern=r"^\d{4}-(0[1-9]|1[0-2])$", description="Month, YYYY-MM")
_SUPPLIER = Query(default=None, max_length=40, description="Commercial supplier code")

ItemStatus = Literal[
    "liquidado",
    "en_revision",
    "escalas",
    "minimo_no_cumplido",
    "descontado_en_factura",
    "sin_valor",
]
Breakdown = Literal["regional", "business_unit", "salesperson", "customer"]
CostBasis = Literal["cotizacion", "inventario"]
_STATUS = Query(default=None, description="Only lines with a support in this status")
_BY = Query(description="Dimension of the breakdown")
_BASIS = Query(
    default="cotizacion",
    description="The cost the month is settled with: the supplier's quotation (cotizacion) or "
    "the inventory cost billed (inventario)",
)


class PermissionsResponse(BaseModel):
    can_read: bool
    can_manage: bool
    # Reopen a closed month: the area's leader and platform administrators.
    can_reopen: bool


class SettlementResponse(BaseModel):
    """A stored month. ``amount`` is what the suppliers owe; ``flagged_amount`` what was
    calculated but not claimed (supports in review, with tiers, minimum not met...)."""

    period: str
    cost_basis: CostBasis
    cost_basis_label: str
    status: Literal["borrador", "cerrado"]
    status_label: str
    amount: Decimal
    flagged_amount: Decimal
    # Every sale of the supported references in the month.
    net: Decimal
    cost: Decimal
    margin: Decimal | None
    recomposed_margin: Decimal | None
    sales_read: int
    supported_lines: int
    suppliers: int
    calculated_at: datetime
    calculated_by: str
    closed_at: datetime | None
    closed_by: str | None
    reopened_at: datetime | None
    reopened_by: str | None


class PeriodResponse(BaseModel):
    period: str
    is_finished: bool
    is_current: bool
    # The settlement of the cost basis asked for; ``null`` when not calculated yet.
    settlement: SettlementResponse | None
    # Every stored settlement of the month (one per cost basis).
    settlements: list[SettlementResponse] = []


class TotalsResponse(BaseModel):
    quantity: Decimal | None = None
    net: Decimal
    cost: Decimal
    margin: Decimal | None
    amount: Decimal
    recomposed_margin: Decimal | None


class SupplierRowResponse(TotalsResponse):
    supplier_code: str | None
    supplier_name: str | None
    references: int
    supported_lines: int
    flagged_amount: Decimal


class SummaryResponse(BaseModel):
    settlement: SettlementResponse
    suppliers: list[SupplierRowResponse]


class ReferenceRowResponse(TotalsResponse):
    supplier_code: str | None
    supplier_name: str | None
    sku: str
    description: str | None
    supported_lines: int
    flagged_amount: Decimal


class ReferencesResponse(BaseModel):
    settlement: SettlementResponse
    references: list[ReferenceRowResponse]


class ItemResponse(BaseModel):
    """A support applied to the line, as it was when the month was calculated."""

    support_id: UUID
    support_version: int
    kind: str
    calc_method: str
    status: ItemStatus
    status_label: str
    supplier_value: Decimal | None
    value_unit: str | None
    valid_from: date | None
    valid_to: date | None
    # % types: the negotiated % and the one settled after the discount not given.
    negotiated_pct: Decimal | None
    settled_pct: Decimal | None
    unit_amount: Decimal
    amount: Decimal
    detail: str


class FlagResponse(BaseModel):
    code: str
    label: str


class LineResponse(BaseModel):
    id: UUID
    line_key: str
    sale_date: date
    document: str | None
    co: str | None
    co_name: str | None
    regional: str | None
    business_unit: str | None
    channel: str | None
    city: str | None
    salesperson: str | None
    nit: str | None
    branch_id: str | None
    customer_name: str | None
    business_group: str | None
    supplier_code: str | None
    supplier_name: str | None
    sku: str
    description: str | None
    warehouse: str | None
    is_return: bool
    reason: str | None
    quantity: Decimal
    gross: Decimal
    net: Decimal
    cost: Decimal
    # The supplier's quotation in force (``cotizacion``) or the inventory cost billed
    # (``factura``): always ``factura`` with the ``inventario`` basis.
    base_unit_cost: Decimal | None
    cost_source: Literal["cotizacion", "factura"]
    amount: Decimal
    margin: Decimal | None
    recomposed_margin: Decimal | None
    flags: list[FlagResponse]
    items: list[ItemResponse]


class LinePageResponse(BaseModel):
    items: list[LineResponse]
    total: int
    page: int
    size: int


class BreakdownRowResponse(BaseModel):
    key: str | None
    name: str | None
    lines: int
    net: Decimal
    cost: Decimal
    amount: Decimal
    margin: Decimal | None
    recomposed_margin: Decimal | None


def _settlement(item: Settlement) -> SettlementResponse:
    return SettlementResponse(
        period=item.period,
        cost_basis=item.cost_basis,  # type: ignore[arg-type]
        cost_basis_label=COST_BASIS_LABELS.get(item.cost_basis, item.cost_basis),
        status=item.status,  # type: ignore[arg-type]
        status_label=STATUS_LABELS.get(item.status, item.status),
        amount=item.amount,
        flagged_amount=item.flagged_amount,
        net=item.net,
        cost=item.cost,
        margin=margin(item.net, item.cost),
        recomposed_margin=recomposed_margin(item.net, item.cost, item.amount),
        sales_read=item.sales_read,
        supported_lines=item.supported_lines,
        suppliers=item.suppliers,
        calculated_at=item.calculated_at,
        calculated_by=item.calculated_by,
        closed_at=item.closed_at,
        closed_by=item.closed_by,
        reopened_at=item.reopened_at,
        reopened_by=item.reopened_by,
    )


def _totals(row: Mapping[str, Any]) -> dict[str, Any]:
    net, cost, amount = row["net"] or 0, row["cost"] or 0, row["amount"] or 0
    return {
        "net": net,
        "cost": cost,
        "amount": amount,
        "margin": margin(Decimal(net), Decimal(cost)),
        "recomposed_margin": recomposed_margin(Decimal(net), Decimal(cost), Decimal(amount)),
    }


def _line(row: Mapping[str, Any]) -> LineResponse:
    fields = {name: row[name] for name in LineResponse.model_fields if name in row}
    fields["flags"] = [
        FlagResponse(code=flag, label=SALE_FLAG_LABELS.get(flag, flag))
        for flag in row["flags"] or ()
    ]
    fields["items"] = [
        ItemResponse(**item, status_label=ITEM_STATUS_LABELS.get(item["status"], item["status"]))
        for item in row["items"]
    ]
    fields["margin"] = margin(row["net"], row["cost"])
    fields["recomposed_margin"] = recomposed_margin(row["net"], row["cost"], row["amount"])
    return LineResponse(**fields)


@router.get("/permissions", response_model=PermissionsResponse, summary="What the caller may do")
@inject
async def permissions(
    principal: Principal = _principal, service: SettlementService = _service
) -> PermissionsResponse:
    """Open to everyone: ``false`` when the caller has no access (no 403)."""

    result = await service.permissions(principal)
    return PermissionsResponse(
        can_read=result.can_read, can_manage=result.can_manage, can_reopen=result.can_reopen
    )


@router.get(
    "/periods",
    response_model=list[PeriodResponse],
    dependencies=[_GATE],
    summary="Stored months, plus the current and the previous one",
)
@inject
async def list_periods(
    cost_basis: CostBasis = _BASIS,
    principal: Principal = _principal,
    service: SettlementService = _service,
) -> list[PeriodResponse]:
    result = await service.periods(principal, cost_basis)
    return [
        PeriodResponse(
            period=item.period,
            is_finished=item.is_finished,
            is_current=item.is_current,
            settlement=_settlement(item.settlement) if item.settlement else None,
            settlements=[_settlement(stored) for stored in item.settlements.values()],
        )
        for item in result
    ]


@router.get(
    "/{period}",
    response_model=SummaryResponse,
    dependencies=[_GATE],
    summary="A month: totals and suppliers",
)
@inject
async def get_summary(
    period: str = _PERIOD,
    cost_basis: CostBasis = _BASIS,
    principal: Principal = _principal,
    service: SettlementService = _service,
) -> SummaryResponse:
    settlement, rows = await service.summary(principal, period, cost_basis)
    return SummaryResponse(
        settlement=_settlement(settlement),
        suppliers=[
            SupplierRowResponse(
                supplier_code=row["supplier_code"],
                supplier_name=row["supplier_name"],
                references=row["references"],
                supported_lines=row["supported_lines"] or 0,
                quantity=row["quantity"],
                flagged_amount=row["flagged_amount"] or 0,
                **_totals(row),
            )
            for row in rows
        ],
    )


@router.get(
    "/{period}/references",
    response_model=ReferencesResponse,
    dependencies=[_GATE],
    summary="The supported references of a month (of one supplier)",
)
@inject
async def get_references(
    period: str = _PERIOD,
    supplier_code: str | None = _SUPPLIER,
    cost_basis: CostBasis = _BASIS,
    principal: Principal = _principal,
    service: SettlementService = _service,
) -> ReferencesResponse:
    """Each reference with every sale of the month, what its supports pay and its margin
    recomposed ``(sale − cost + support) / sale``."""

    settlement, rows = await service.references(principal, period, supplier_code, cost_basis)
    return ReferencesResponse(
        settlement=_settlement(settlement),
        references=[
            ReferenceRowResponse(
                supplier_code=row["supplier_code"],
                supplier_name=row["supplier_name"],
                sku=row["sku"],
                description=row["description"],
                supported_lines=row["supported_lines"],
                quantity=row["quantity"],
                flagged_amount=row["flagged_amount"],
                **_totals(row),
            )
            for row in rows
        ],
    )


@router.get(
    "/{period}/lines",
    response_model=LinePageResponse,
    dependencies=[_GATE],
    summary="The sale lines a support covered, with each support applied",
)
@inject
async def get_lines(
    period: str = _PERIOD,
    supplier_code: str | None = _SUPPLIER,
    sku: str | None = Query(default=None, max_length=40),
    nit: str | None = Query(default=None, max_length=40),
    status: ItemStatus | None = _STATUS,
    page: int = Query(default=1, ge=1),
    size: int = Query(default=100, ge=1, le=MAX_PAGE_SIZE),
    cost_basis: CostBasis = _BASIS,
    principal: Principal = _principal,
    service: SettlementService = _service,
) -> LinePageResponse:
    rows, total = await service.lines(
        principal,
        period,
        cost_basis=cost_basis,
        supplier_code=supplier_code,
        sku=sku,
        nit=nit,
        status=status,
        page=page,
        size=size,
    )
    return LinePageResponse(items=[_line(row) for row in rows], total=total, page=page, size=size)


@router.get(
    "/{period}/breakdown",
    response_model=list[BreakdownRowResponse],
    dependencies=[_GATE],
    summary="Where the supplier's money goes back: regional, business unit, salesperson, customer",
)
@inject
async def get_breakdown(
    period: str = _PERIOD,
    by: Breakdown = _BY,
    supplier_code: str | None = _SUPPLIER,
    cost_basis: CostBasis = _BASIS,
    principal: Principal = _principal,
    service: SettlementService = _service,
) -> list[BreakdownRowResponse]:
    rows = await service.breakdown(principal, period, by, supplier_code, cost_basis)
    return [
        BreakdownRowResponse(
            key=None if row["key"] is None else str(row["key"]),
            name=row["name"],
            lines=row["lines"],
            **_totals(row),
        )
        for row in rows
    ]


@router.get(
    "/{period}/export",
    dependencies=[_GATE],
    summary="The month's workbook (one supplier or all)",
    response_class=Response,
)
@inject
async def export(
    period: str = _PERIOD,
    supplier_code: str | None = _SUPPLIER,
    cost_basis: CostBasis = _BASIS,
    principal: Principal = _principal,
    service: SettlementService = _service,
) -> Response:
    """Sheets: Resumen (by reference), Detalle ventas and No liquidados."""

    result = await service.export(principal, period, supplier_code, cost_basis)
    return Response(
        content=result.content,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(result.filename)}"},
    )


@router.post(
    "/{period}/recalculate",
    response_model=SettlementResponse,
    dependencies=[_GATE],
    summary="Calculate the month again (a draft)",
)
@inject
async def recalculate(
    period: str = _PERIOD,
    cost_basis: CostBasis = _BASIS,
    principal: Principal = _principal,
    service: SettlementService = _service,
) -> SettlementResponse:
    """Reads the month's sales from the DWH once (it may take a few minutes) and stores both
    cost bases, the ones not closed; returns ``cost_basis``. 409 when that one is closed."""

    return _settlement(await service.recalculate(principal, period, cost_basis))


@router.post(
    "/{period}/close",
    response_model=SettlementResponse,
    dependencies=[_GATE],
    summary="Calculate the finished month one last time and freeze it",
)
@inject
async def close(
    period: str = _PERIOD,
    cost_basis: CostBasis = _BASIS,
    principal: Principal = _principal,
    service: SettlementService = _service,
) -> SettlementResponse:
    """Closes only ``cost_basis``; the other basis stays as it is."""

    return _settlement(await service.close(principal, period, cost_basis))


@router.post(
    "/{period}/reopen",
    response_model=SettlementResponse,
    dependencies=[_GATE],
    summary="Reopen a closed month (the area's leader)",
)
@inject
async def reopen(
    period: str = _PERIOD,
    cost_basis: CostBasis = _BASIS,
    principal: Principal = _principal,
    service: SettlementService = _service,
) -> SettlementResponse:
    return _settlement(await service.reopen(principal, period, cost_basis))


__all__ = ["router"]
