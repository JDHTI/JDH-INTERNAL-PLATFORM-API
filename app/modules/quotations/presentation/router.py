"""Endpoints of the quoter (``/quotations``)."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Literal
from urllib.parse import quote
from uuid import UUID

from dependency_injector.wiring import Provide, inject
from fastapi import APIRouter, Depends, File, Form, Query, Response, UploadFile, status
from pydantic import BaseModel, Field, model_validator

from app.app_container import ApplicationContainer
from app.modules.areas.presentation.dependencies import require_permission
from app.modules.identity.domain.principal import Principal
from app.modules.identity.presentation.principal import get_current_principal
from app.modules.quotations.application.service import (
    MAX_PAGE_SIZE,
    MAX_PASTED_SKUS,
    LineChange,
    QuotationService,
    Skipped,
)
from app.modules.quotations.domain import pricing, rules
from app.modules.quotations.domain.entities import (
    AppliedSupport,
    Quotation,
    QuotationDetail,
    QuotationLine,
)
from app.modules.quotations.domain.vocabulary import (
    BASIS_LABELS,
    COMPLIANCE_LABELS,
    COST_SOURCE_LABELS,
    LIST_ACTION_LABELS,
    ORIGIN_LABELS,
    PROBLEM_LABELS,
    REGIONAL_LABELS,
    STATUS_LABELS,
    WARNING_LABELS,
)

router = APIRouter()
_READ_GATE = Depends(require_permission("cotizador"))
_service = Depends(Provide[ApplicationContainer.v1_container.quotation_service])
_principal = Depends(get_current_principal)

Status = Literal["borrador", "pendiente_validacion", "creada", "actualizada"]
ListAction = Literal["actualizacion", "creacion"]
Compliance = Literal["cumple", "bajo_minimo", "sin_objetivo", "sin_costo"]


class SellerResponse(BaseModel):
    code: str
    name: str | None
    email: str | None


class PermissionsResponse(BaseModel):
    """What the caller may do, and the salesperson linked to their e-mail."""

    can_manage: bool
    # May validate confirmed quotations (approvers' area and admins).
    can_validate: bool
    # Customers the caller works on: all, team (supervisor), mine (salesperson) or none.
    # The server applies it to customers, quotations and their creation.
    sales_scope: Literal["all", "team", "mine", "none"] | None = None
    seller_linked: bool
    seller: SellerResponse | None


class RuleResponse(BaseModel):
    code: str
    title: str
    description: str
    effect: Literal["libera", "retiene", "alerta", "informa"]
    parameters: dict[str, str]


class RuleSectionResponse(BaseModel):
    code: str
    title: str
    rules: list[RuleResponse]


class CustomerResponse(BaseModel):
    nit: str
    name: str | None
    channel_code: str | None
    channel: str | None
    salesperson_code: str | None
    salesperson_name: str | None
    branch_count: int
    active_branch_count: int
    is_active: bool


class CustomerPageResponse(BaseModel):
    items: list[CustomerResponse]
    total: int
    page: int
    size: int
    seller_linked: bool


class CreateRequest(BaseModel):
    customer_nit: str = Field(min_length=1, max_length=30)
    window_months: Literal[6, 12] = 12


class LineChangeRequest(BaseModel):
    """A new price (``null`` clears it), or ``margin_pct`` to price the SKU at a margin."""

    sku: str = Field(min_length=1, max_length=60)
    new_price: Decimal | None = None
    margin_pct: Decimal | None = Field(
        default=None, description="Fraction (0.30 = 30 %); prices the SKU at that margin"
    )

    @model_validator(mode="after")
    def _one_of(self) -> LineChangeRequest:
        if self.new_price is not None and self.margin_pct is not None:
            raise ValueError("Envía new_price o margin_pct, no los dos")
        return self


class LinesRequest(BaseModel):
    version: int = Field(ge=1)
    lines: list[LineChangeRequest] = Field(min_length=1, max_length=5000)


class AddLinesRequest(BaseModel):
    """SKUs to add or revalidate: a list, pasted text, or both."""

    version: int = Field(ge=1)
    skus: list[str] = Field(default_factory=list, max_length=MAX_PASTED_SKUS)
    text: str | None = Field(
        default=None,
        max_length=20_000,
        description="Pasted list: an Excel column or SKUs separated by commas, ; or spaces",
    )


class ImportResultResponse(BaseModel):
    """What the pasted list did, SKU by SKU."""

    added: list[str]
    revalidated: list[str]
    not_found: list[str]
    inactive: list[str]
    repeated: int


class SkippedResponse(BaseModel):
    """A SKU a change could not price."""

    sku: str
    reason: str
    row_number: int | None = None


class TemplateResultResponse(BaseModel):
    """What an uploaded workbook did."""

    updated: list[str]
    cleared: list[str]
    added: list[str]
    unchanged: int
    not_found: list[str]
    inactive: list[str]
    skipped: list[SkippedResponse]


class VersionRequest(BaseModel):
    version: int = Field(ge=1)


class ConfirmRequest(BaseModel):
    """Close the draft and send it to validation."""

    version: int = Field(ge=1)
    list_action: ListAction | None = None
    price_list_id: str | None = Field(
        default=None, max_length=3, description="Code of the new list (creation only)"
    )
    new_list_name: str | None = Field(default=None, max_length=40)
    activation_date: date | None = None


class ReturnRequest(BaseModel):
    version: int = Field(ge=1)
    note: str | None = Field(default=None, max_length=1000)


class WarningResponse(BaseModel):
    code: str
    label: str


class SupportResponse(BaseModel):
    support_id: UUID
    kind: str
    calc_method: str
    program: str | None
    amount: Decimal | None
    share_of_cost: Decimal | None
    required_margin_pct: Decimal | None
    detail: str
    conditions: str | None
    needs_review: bool
    valid_from: date | None
    valid_to: date | None


class CandidateResponse(BaseModel):
    support_id: UUID
    calc_method: str
    program: str | None
    reason: str


class LineResponse(BaseModel):
    """One SKU: what was last sold, at what cost and margin, and the advisor's price."""

    id: UUID
    position: int
    sku: str
    description: str | None
    unit: str | None
    pack_factor: Decimal | None
    supplier_code: str | None
    supplier_name: str | None
    last_price: Decimal | None
    last_gross_price: Decimal | None
    last_discount_pct: Decimal | None
    last_sale_date: date | None
    last_document: str | None
    last_sale_unit: str | None
    last_quantity: Decimal | None
    sales_in_window: int
    list_price: Decimal | None
    vat_pct: Decimal | None
    value_tax_key: str | None
    value_tax_name: str | None
    value_tax_amount: Decimal | None
    cost: Decimal | None
    quote_cost: Decimal | None
    discarded_quote_cost: Decimal | None
    inventory_cost: Decimal | None
    cost_source: Literal["cotizacion", "inventario"] | None
    cost_source_label: str | None
    target_margin_pct: Decimal | None
    margin_basis: str | None
    margin_basis_label: str | None
    min_margin_pct: Decimal | None
    current_margin_pct: Decimal | None
    current_margin_with_support_pct: Decimal | None
    min_price: Decimal | None
    optimal_price: Decimal | None
    below_target: bool
    is_active: bool
    support: SupportResponse | None
    support_candidates: list[CandidateResponse]
    new_price: Decimal | None
    new_margin_pct: Decimal | None
    new_margin_with_support_pct: Decimal | None
    variation_pct: Decimal | None
    compliance: Compliance | None
    compliance_label: str | None
    price_with_taxes: Decimal | None
    problems: list[str]
    problem_labels: list[str]
    origin: Literal["historico", "agregado"]
    origin_label: str


class SummaryResponse(BaseModel):
    """A quotation in a list."""

    id: UUID
    customer_nit: str
    customer_name: str | None
    channel: str | None
    price_list_id: str
    window_months: int
    status: Status
    status_label: str
    list_action: ListAction
    list_action_label: str
    export_price_list_id: str | None
    line_count: int
    changed_count: int
    below_rule_count: int
    below_target_count: int
    created_by: str
    created_at: datetime
    updated_at: datetime
    confirmed_at: datetime | None
    validated_at: datetime | None
    version: int


class QuotationResponse(SummaryResponse):
    """A quotation with its header and lines."""

    channel_code: str | None
    price_list_name: str | None
    price_list_customers: int | None
    billing_center_code: str | None
    regional: Literal["nacional", "costa"]
    regional_label: str
    price_family: str
    price_family_label: str
    stock_source: str | None
    salesperson_code: str | None
    salesperson_name: str | None
    built_at: datetime
    cost_cut_date: date | None
    activation_date: date | None
    new_list_name: str | None
    max_points_below_target: Decimal
    warnings: list[WarningResponse]
    confirmed_by: str | None
    validated_by: str | None
    review_note: str | None
    updated_by: str
    lines: list[LineResponse]
    # Only on POST /{id}/lines: what the pasted list did.
    import_result: ImportResultResponse | None = None
    # Only on PUT /{id}/lines: SKUs a margin could not price.
    skipped: list[SkippedResponse] | None = None
    # Only on POST /{id}/template: what the workbook did.
    template_result: TemplateResultResponse | None = None


class QuotationPageResponse(BaseModel):
    items: list[SummaryResponse]
    total: int
    page: int
    size: int


def _support(item: AppliedSupport | None) -> SupportResponse | None:
    if item is None:
        return None
    return SupportResponse(
        support_id=item.support_id,
        kind=item.kind,
        calc_method=item.calc_method,
        program=item.program,
        amount=item.amount,
        share_of_cost=item.share_of_cost,
        required_margin_pct=item.required_margin_pct,
        detail=item.detail,
        conditions=item.conditions,
        needs_review=item.needs_review,
        valid_from=item.valid_from,
        valid_to=item.valid_to,
    )


def _line(line: QuotationLine) -> LineResponse:
    return LineResponse(
        id=line.id,
        position=line.position,
        sku=line.sku,
        description=line.description,
        unit=line.unit,
        pack_factor=line.pack_factor,
        supplier_code=line.supplier_code,
        supplier_name=line.supplier_name,
        last_price=line.last_price,
        last_gross_price=line.last_gross_price,
        last_discount_pct=line.last_discount_pct,
        last_sale_date=line.last_sale_date,
        last_document=line.last_document,
        last_sale_unit=line.last_sale_unit,
        last_quantity=line.last_quantity,
        sales_in_window=line.sales_in_window,
        list_price=line.list_price,
        vat_pct=line.vat_pct,
        value_tax_key=line.value_tax_key,
        value_tax_name=line.value_tax_name,
        value_tax_amount=line.value_tax_amount,
        cost=line.cost,
        quote_cost=line.quote_cost,
        discarded_quote_cost=line.discarded_quote_cost,
        inventory_cost=line.inventory_cost,
        cost_source=line.cost_source,  # type: ignore[arg-type]
        cost_source_label=COST_SOURCE_LABELS.get(line.cost_source or ""),
        target_margin_pct=line.target_margin_pct,
        margin_basis=line.margin_basis,
        margin_basis_label=BASIS_LABELS.get(line.margin_basis or ""),
        min_margin_pct=line.min_margin_pct,
        current_margin_pct=line.current_margin_pct,
        current_margin_with_support_pct=line.current_margin_with_support_pct,
        min_price=line.min_price,
        optimal_price=line.optimal_price,
        below_target=line.below_target,
        is_active=line.is_active,
        support=_support(line.support),
        support_candidates=[
            CandidateResponse(
                support_id=item.support_id,
                calc_method=item.calc_method,
                program=item.program,
                reason=item.reason,
            )
            for item in line.support_candidates
        ],
        new_price=line.new_price,
        new_margin_pct=line.new_margin_pct,
        new_margin_with_support_pct=line.new_margin_with_support_pct,
        variation_pct=line.variation_pct,
        compliance=line.compliance,  # type: ignore[arg-type]
        compliance_label=COMPLIANCE_LABELS.get(line.compliance or ""),
        price_with_taxes=rules.price_with_taxes(line),
        problems=list(line.problems),
        problem_labels=[PROBLEM_LABELS.get(code, code) for code in line.problems],
        origin=line.origin,  # type: ignore[arg-type]
        origin_label=ORIGIN_LABELS.get(line.origin, line.origin),
    )


def _summary_values(quotation: Quotation) -> dict[str, object]:
    return {
        "id": quotation.id,
        "customer_nit": quotation.customer_nit,
        "customer_name": quotation.customer_name,
        "channel": quotation.channel,
        "price_list_id": quotation.price_list_id,
        "window_months": quotation.window_months,
        "status": quotation.status,
        "status_label": STATUS_LABELS[quotation.status],
        "list_action": quotation.list_action,
        "list_action_label": LIST_ACTION_LABELS[quotation.list_action],
        "export_price_list_id": quotation.export_price_list_id,
        "line_count": quotation.line_count,
        "changed_count": quotation.changed_count,
        "below_rule_count": quotation.below_rule_count,
        "below_target_count": quotation.below_target_count,
        "created_by": quotation.created_by,
        "created_at": quotation.created_at,
        "updated_at": quotation.updated_at,
        "confirmed_at": quotation.confirmed_at,
        "validated_at": quotation.validated_at,
        "version": quotation.version,
    }


def _detail(detail: QuotationDetail) -> QuotationResponse:
    quotation = detail.quotation
    return QuotationResponse(
        **_summary_values(quotation),  # type: ignore[arg-type]
        channel_code=quotation.channel_code,
        price_list_name=quotation.price_list_name,
        price_list_customers=quotation.price_list_customers,
        billing_center_code=quotation.billing_center_code,
        regional=quotation.regional,  # type: ignore[arg-type]
        regional_label=REGIONAL_LABELS.get(quotation.regional, quotation.regional),
        price_family=quotation.price_family,
        price_family_label=pricing.FAMILY_LABELS.get(
            quotation.price_family, quotation.price_family
        ),
        stock_source=quotation.stock_source,
        salesperson_code=quotation.salesperson_code,
        salesperson_name=quotation.salesperson_name,
        built_at=quotation.built_at,
        cost_cut_date=quotation.cost_cut_date,
        activation_date=quotation.activation_date,
        new_list_name=quotation.new_list_name,
        max_points_below_target=quotation.max_points_below_target,
        warnings=[
            WarningResponse(code=code, label=WARNING_LABELS.get(code, code))
            for code in quotation.warnings
        ],
        confirmed_by=quotation.confirmed_by,
        validated_by=quotation.validated_by,
        review_note=quotation.review_note,
        updated_by=quotation.updated_by,
        lines=[_line(line) for line in detail.lines],
    )


@router.get("/permissions", response_model=PermissionsResponse, summary="What the caller may do")
@inject
async def permissions(
    principal: Principal = _principal,
    service: QuotationService = _service,
) -> PermissionsResponse:
    """Whether the caller may quote, and the warehouse salesperson whose e-mail is theirs."""

    result = await service.permissions(principal)
    seller = result.seller
    return PermissionsResponse(
        can_manage=result.can_manage,
        can_validate=result.can_validate,
        sales_scope=result.sales_scope,  # type: ignore[arg-type]
        seller_linked=seller is not None,
        seller=SellerResponse(code=seller.code, name=seller.name, email=seller.email)
        if seller
        else None,
    )


@router.get(
    "/rules",
    dependencies=[_READ_GATE],
    response_model=list[RuleSectionResponse],
    summary="The quoter's rules",
)
@inject
async def quotation_rules(
    _: Principal = _principal,
    service: QuotationService = _service,
) -> list[RuleSectionResponse]:
    """Every rule with the values in force. Open to every user."""

    return [
        RuleSectionResponse(
            code=section.code,
            title=section.title,
            rules=[
                RuleResponse(
                    code=rule.code,
                    title=rule.title,
                    description=rule.description,
                    effect=rule.effect,  # type: ignore[arg-type]
                    parameters=dict(rule.parameters),
                )
                for rule in section.rules
            ],
        )
        for section in service.rule_catalog()
    ]


@router.get(
    "/customers",
    dependencies=[_READ_GATE],
    response_model=CustomerPageResponse,
    summary="Customers to quote",
)
@inject
async def customers(
    scope: Literal["mine", "all"] = Query(default="mine"),
    q: str | None = Query(default=None, max_length=100),
    page: int = Query(default=1, ge=1),
    size: int = Query(default=20, ge=1, le=MAX_PAGE_SIZE),
    principal: Principal = _principal,
    service: QuotationService = _service,
) -> CustomerPageResponse:
    """The caller's portfolio (``mine``: customers of the salespeople whose e-mail is theirs)
    or every customer, merged by NIT and active first."""

    result, linked = await service.customers(principal, scope, q, page, size)
    return CustomerPageResponse(
        items=[
            CustomerResponse(
                nit=item.nit,
                name=item.name,
                channel_code=item.channel_code,
                channel=item.channel,
                salesperson_code=item.salesperson_code,
                salesperson_name=item.salesperson_name,
                branch_count=item.branch_count,
                active_branch_count=item.active_branch_count,
                is_active=item.is_active,
            )
            for item in result.items
        ],
        total=result.total,
        page=result.page,
        size=result.size,
        seller_linked=linked,
    )


@router.post(
    "",
    dependencies=[_READ_GATE],
    response_model=QuotationResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Build a quotation of a customer",
)
@inject
async def create_quotation(
    body: CreateRequest,
    principal: Principal = _principal,
    service: QuotationService = _service,
) -> QuotationResponse:
    """Every SKU sold to the customer in the window, at its last price, with current cost,
    target margin and supports."""

    return _detail(await service.create(principal, body.customer_nit, body.window_months))


@router.get(
    "", dependencies=[_READ_GATE], response_model=QuotationPageResponse, summary="List quotations"
)
@inject
async def list_quotations(
    mine: bool = Query(default=True),
    status_filter: Status | None = Query(default=None, alias="status"),  # noqa: B008
    customer_nit: str | None = Query(default=None, max_length=30),
    q: str | None = Query(default=None, max_length=100),
    page: int = Query(default=1, ge=1),
    size: int = Query(default=20, ge=1, le=MAX_PAGE_SIZE),
    principal: Principal = _principal,
    service: QuotationService = _service,
) -> QuotationPageResponse:
    """Quotations, most recently changed first."""

    result = await service.list(
        principal,
        mine=mine,
        status=status_filter,
        customer_nit=customer_nit,
        q=q,
        page=page,
        size=size,
    )
    return QuotationPageResponse(
        items=[SummaryResponse(**_summary_values(item)) for item in result.items],  # type: ignore[arg-type]
        total=result.total,
        page=result.page,
        size=result.size,
    )


@router.get(
    "/{quotation_id}",
    dependencies=[_READ_GATE],
    response_model=QuotationResponse,
    summary="Get a quotation",
)
@inject
async def get_quotation(
    quotation_id: UUID,
    principal: Principal = _principal,
    service: QuotationService = _service,
) -> QuotationResponse:
    return _detail(await service.get(principal, quotation_id))


@router.put(
    "/{quotation_id}/lines",
    dependencies=[_READ_GATE],
    response_model=QuotationResponse,
    summary="Set new prices",
)
@inject
async def update_lines(
    quotation_id: UUID,
    body: LinesRequest,
    principal: Principal = _principal,
    service: QuotationService = _service,
) -> QuotationResponse:
    """Set (or clear with ``null``) the advisor's prices, or price SKUs at a margin with
    ``margin_pct`` (a selection at one standard margin). A price below the rule is saved and
    marked; SKUs a margin cannot price come back in ``skipped``. 409 when the quotation
    changed since it was read or awaits validation."""

    changes = [LineChange(item.sku, item.new_price, item.margin_pct) for item in body.lines]
    detail, skipped = await service.update_lines(principal, quotation_id, body.version, changes)
    response = _detail(detail)
    response.skipped = [_skipped(item) for item in skipped]
    return response


def _skipped(item: Skipped) -> SkippedResponse:
    return SkippedResponse(sku=item.sku, reason=item.reason, row_number=item.row_number)


@router.post(
    "/{quotation_id}/lines",
    dependencies=[_READ_GATE],
    response_model=QuotationResponse,
    summary="Add or revalidate SKUs",
)
@inject
async def add_lines(
    quotation_id: UUID,
    body: AddLinesRequest,
    principal: Principal = _principal,
    service: QuotationService = _service,
) -> QuotationResponse:
    """Paste a list of SKUs (an Excel column or separated by commas): the ones already in the
    quotation are revalidated keeping their new price, the rest are added at the end
    (``origin: agregado``). Unknown and inactive SKUs are reported in ``import_result`` and
    skipped; 422 only when none could be used."""

    values = [*body.skus, *([body.text] if body.text else [])]
    detail, result = await service.add_lines(principal, quotation_id, body.version, values)
    response = _detail(detail)
    response.import_result = ImportResultResponse(
        added=list(result.added),
        revalidated=list(result.revalidated),
        not_found=list(result.not_found),
        inactive=list(result.inactive),
        repeated=result.repeated,
    )
    return response


@router.delete(
    "/{quotation_id}/lines/{sku}",
    dependencies=[_READ_GATE],
    response_model=QuotationResponse,
    summary="Remove an added SKU",
)
@inject
async def remove_line(
    quotation_id: UUID,
    sku: str,
    version: int = Query(ge=1),
    principal: Principal = _principal,
    service: QuotationService = _service,
) -> QuotationResponse:
    """SKUs the advisor added or inactive in SIESA; an active sales-window SKU stays (leave
    its new price empty)."""

    return _detail(await service.remove_line(principal, quotation_id, version, sku))


@router.post(
    "/{quotation_id}/refresh",
    dependencies=[_READ_GATE],
    response_model=QuotationResponse,
    summary="Rebuild from the DWH",
)
@inject
async def refresh_quotation(
    quotation_id: UUID,
    body: VersionRequest,
    principal: Principal = _principal,
    service: QuotationService = _service,
) -> QuotationResponse:
    """Last sales, cost and supports as they are now; new prices are kept by SKU."""

    return _detail(await service.refresh(principal, quotation_id, body.version))


@router.post(
    "/{quotation_id}/confirm",
    dependencies=[_READ_GATE],
    response_model=QuotationResponse,
    summary="Send to validation",
)
@inject
async def confirm_quotation(
    quotation_id: UUID,
    body: ConfirmRequest,
    principal: Principal = _principal,
    service: QuotationService = _service,
) -> QuotationResponse:
    """The advisor closes the draft: every new price of an active SKU must meet the rule.
    ``actualizacion`` writes to the customer's list; ``creacion`` creates ``price_list_id``
    (list 1 customers always go by creation). The quotation is ``pendiente_validacion``."""

    return _detail(
        await service.confirm(
            principal,
            quotation_id,
            body.version,
            list_action=body.list_action,
            price_list_id=body.price_list_id,
            new_list_name=body.new_list_name,
            activation_date=body.activation_date,
        )
    )


@router.post(
    "/{quotation_id}/return",
    dependencies=[_READ_GATE],
    response_model=QuotationResponse,
    summary="Back to draft",
)
@inject
async def return_quotation(
    quotation_id: UUID,
    body: ReturnRequest,
    principal: Principal = _principal,
    service: QuotationService = _service,
) -> QuotationResponse:
    """A pending quotation goes back to draft: the approver rejects it (``note`` required) or
    an advisor withdraws it."""

    return _detail(await service.return_to_draft(principal, quotation_id, body.version, body.note))


@router.post(
    "/{quotation_id}/validate",
    dependencies=[_READ_GATE],
    response_model=QuotationResponse,
    summary="Validate",
)
@inject
async def validate_quotation(
    quotation_id: UUID,
    body: VersionRequest,
    principal: Principal = _principal,
    service: QuotationService = _service,
) -> QuotationResponse:
    """Approvers only: the quotation becomes ``creada`` or ``actualizada`` and its files can
    be downloaded."""

    return _detail(await service.validate(principal, quotation_id, body.version))


@router.get(
    "/{quotation_id}/template.xlsx",
    dependencies=[_READ_GATE],
    response_class=Response,
    responses={
        200: {"content": {"application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": {}}}
    },
    summary="Download the quotation workbook",
)
@inject
async def download_template(
    quotation_id: UUID,
    principal: Principal = _principal,
    service: QuotationService = _service,
) -> Response:
    """Every SKU with its data; ``Precio nuevo`` and ``Margen nuevo`` are editable."""

    result = await service.template_file(principal, quotation_id)
    return _file(result.content, result.filename, result.media_type)


@router.post(
    "/{quotation_id}/template",
    dependencies=[_READ_GATE],
    response_model=QuotationResponse,
    summary="Upload the edited quotation workbook",
    description=(
        "multipart/form-data with `file` (the workbook from `template.xlsx`) and `version`. "
        "Per row the cell that changed wins: a new price, else a new margin; both blank clear "
        "the price. Unknown SKUs are added if they exist and are active."
    ),
)
@inject
async def upload_template(
    quotation_id: UUID,
    file: UploadFile = File(...),  # noqa: B008
    version: int = Form(ge=1),
    principal: Principal = _principal,
    service: QuotationService = _service,
) -> QuotationResponse:
    content = await file.read(service.max_upload_bytes + 1)
    detail, result = await service.upload_template(
        principal, quotation_id, version, file.filename, content
    )
    response = _detail(detail)
    response.template_result = TemplateResultResponse(
        updated=list(result.updated),
        cleared=list(result.cleared),
        added=list(result.added),
        unchanged=result.unchanged,
        not_found=list(result.not_found),
        inactive=list(result.inactive),
        skipped=[_skipped(item) for item in result.skipped],
    )
    return response


def _file(content: bytes, filename: str, media_type: str) -> Response:
    return Response(
        content=content,
        media_type=media_type,
        headers={
            "Content-Disposition": f"attachment; filename*=UTF-8''{quote(filename)}",
            "Cache-Control": "no-store",
        },
    )


@router.get(
    "/{quotation_id}/export.xlsx",
    dependencies=[_READ_GATE],
    response_class=Response,
    responses={
        200: {"content": {"application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": {}}}
    },
    summary="Download the update workbook",
)
@inject
async def export_workbook(
    quotation_id: UUID,
    principal: Principal = _principal,
    service: QuotationService = _service,
) -> Response:
    """``ACTUALIZACION LISTAS PRECIOS`` with sheets ``Precios`` and ``Impuestos en valor``."""

    result = await service.export(principal, quotation_id, "xlsx")
    return _file(result.content, result.filename, result.media_type)


@router.get(
    "/{quotation_id}/export.txt",
    dependencies=[_READ_GATE],
    response_class=Response,
    responses={200: {"content": {"text/plain": {}}}},
    summary="Download the SIESA import file",
)
@inject
async def export_flat_file(
    quotation_id: UUID,
    principal: Principal = _principal,
    service: QuotationService = _service,
) -> Response:
    """Fixed-width Impodatos file: records 0000, 0126, 0127 and 9999."""

    result = await service.export(principal, quotation_id, "txt")
    return _file(result.content, result.filename, result.media_type)


@router.delete(
    "/{quotation_id}",
    dependencies=[_READ_GATE],
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a quotation",
)
@inject
async def delete_quotation(
    quotation_id: UUID,
    principal: Principal = _principal,
    service: QuotationService = _service,
) -> Response:
    """Its creator or an admin."""

    await service.delete(principal, quotation_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
