"""Endpoints of the quoter (``/quotations``)."""

from __future__ import annotations

from datetime import date, datetime, timedelta
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
from app.modules.quotations.domain import offer, pricing, rules
from app.modules.quotations.domain.entities import (
    AppliedSupport,
    Quotation,
    QuotationDetail,
    QuotationLine,
    QuotationRef,
    RequestedProduct,
    SellerContact,
)
from app.modules.quotations.domain.vocabulary import (
    BASIS_LABELS,
    COMPLIANCE_LABELS,
    COST_SOURCE_LABELS,
    DEFAULT_VALIDITY_DAYS,
    LIST_ACTION_LABELS,
    MAX_VALIDITY_DAYS,
    ORIGIN_LABELS,
    PROBLEM_LABELS,
    REGIONAL_LABELS,
    REQUESTED_STATUS_LABELS,
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
OfferScope = Literal["changed", "all"]
RequestedStatus = Literal["solicitado", "creado", "descartado"]


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
    validity_days: int = Field(
        default=DEFAULT_VALIDITY_DAYS,
        ge=1,
        le=MAX_VALIDITY_DAYS,
        description="Days the offer holds",
    )


class LineChangeRequest(BaseModel):
    """A new price (``null`` clears it), or ``margin_pct`` to price the SKU at a margin, and
    ``quantity`` (``null`` clears it). A change with only ``quantity`` leaves the price."""

    sku: str = Field(min_length=1, max_length=60)
    new_price: Decimal | None = None
    margin_pct: Decimal | None = Field(
        default=None, description="Fraction (0.30 = 30 %); prices the SKU at that margin"
    )
    quantity: Decimal | str | None = Field(
        default=None, description="Inventory units, above zero, up to two decimals"
    )

    @model_validator(mode="after")
    def _one_of(self) -> LineChangeRequest:
        if self.new_price is not None and self.margin_pct is not None:
            raise ValueError("Envía new_price o margin_pct, no los dos")
        if self.margin_pct is not None and "quantity" in self.model_fields_set:
            raise ValueError("Envía margin_pct o quantity, no los dos")
        return self

    def change(self) -> LineChange:
        """The service's change: a price change unless only the quantity was sent."""

        sent = self.model_fields_set
        quantity = "quantity" in sent
        price = not quantity or bool(sent & {"new_price", "margin_pct"})
        return LineChange(
            self.sku,
            self.new_price,
            self.margin_pct,
            quantity=self.quantity,
            set_price=price,
            set_quantity=quantity,
        )


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


class LocalLineRequest(BaseModel):
    """A product SIESA does not have, quoted only here.

    The fields are checked by the service so a bad value comes back as one Spanish
    ``detail``: description 1 to 120 characters, supplier up to 120, price in whole pesos
    above zero before VAT, VAT 19, 5 or 0 (points).
    """

    version: int = Field(ge=1)
    description: str | None = Field(default=None, max_length=2000)
    supplier_name: str | None = Field(default=None, max_length=2000)
    price: Decimal | str | None = Field(default=None, description="Whole pesos, before VAT")
    vat_pct: Decimal | str | None = Field(default=None, description="Points: 19, 5 or 0")
    requested_product_id: UUID | None = Field(
        default=None,
        description="A requested product still `solicitado`: its description, supplier and "
        "VAT are used, and its estimated price when `price` is omitted",
    )


class OfferRequest(BaseModel):
    """Offer settings; only the keys sent change (``null`` leaves one as it is)."""

    version: int = Field(ge=1)
    validity_days: int | None = Field(default=None, ge=1, le=MAX_VALIDITY_DAYS)
    conditions: list[str] | None = Field(default=None, max_length=200)
    include_seller: bool | None = None
    quote_quantities: bool | None = None
    offer_scope: OfferScope | None = None


class RequestedProductUpdate(BaseModel):
    """BI's decision on a requested product."""

    status: RequestedStatus
    siesa_sku: str | None = Field(default=None, max_length=60)
    note: str | None = Field(default=None, max_length=4000)


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


class RequestedProductRefResponse(BaseModel):
    id: UUID
    status: RequestedStatus
    siesa_sku: str | None


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
    # ``local``: a product only for the quotation (SKU ``LOC-<n>``), not in SIESA.
    origin: Literal["historico", "agregado", "local"]
    origin_label: str
    # Product category in SIESA; ``None`` when unknown and for a local product.
    category: str | None
    # Inventory units the customer would buy.
    quantity: Decimal | None
    # New price, else last price (a local product: its new price).
    offer_price: Decimal | None
    # Whether the customer offer carries the line (``offer_scope``; never an inactive one).
    in_offer: bool
    # A local product's entry in the requested products registry.
    requested_product: RequestedProductRefResponse | None


class SellerContactResponse(BaseModel):
    code: str
    name: str | None
    email: str | None
    phone: str | None


class VatGroupResponse(BaseModel):
    vat_pct: Decimal
    base: Decimal
    vat: Decimal


class OfferSummaryResponse(BaseModel):
    """Totals of the offer lines with a quantity, in whole pesos."""

    item_count: int
    subtotal: Decimal
    vat_groups: list[VatGroupResponse]
    liquor_tax: Decimal
    total: Decimal


class RequestedQuotationResponse(BaseModel):
    id: UUID
    customer_name: str | None
    customer_nit: str
    status: Status
    status_label: str
    created_by: str


class RequestedProductResponse(BaseModel):
    """A product an advisor quoted that SIESA does not have."""

    id: UUID
    description: str
    supplier_name: str | None
    estimated_price: Decimal | None
    vat_pct: Decimal | None
    status: RequestedStatus
    status_label: str
    siesa_sku: str | None
    note: str | None
    created_by: str
    created_at: datetime
    updated_by: str
    updated_at: datetime
    # Only the quotations the caller can open.
    quotations: list[RequestedQuotationResponse]


class RequestedProductPageResponse(BaseModel):
    items: list[RequestedProductResponse]
    total: int
    page: int
    size: int


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
    validity_days: int
    valid_from: date
    valid_until: date


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
    conditions: list[str]
    include_seller: bool
    quote_quantities: bool
    offer_scope: OfferScope
    seller_contact: SellerContactResponse | None
    # ``None`` unless ``quote_quantities``.
    offer_summary: OfferSummaryResponse | None
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


def _line(line: QuotationLine, scope: str) -> LineResponse:
    requested = line.requested_product
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
        category=line.category,
        quantity=line.quantity,
        offer_price=offer.offer_price(line),
        in_offer=offer.in_offer(line, scope),
        requested_product=RequestedProductRefResponse(
            id=requested.id,
            status=requested.status,  # type: ignore[arg-type]
            siesa_sku=requested.siesa_sku,
        )
        if requested
        else None,
    )


def _summary(detail: QuotationDetail) -> OfferSummaryResponse | None:
    result = offer.offer_summary(detail)
    if result is None:
        return None
    return OfferSummaryResponse(
        item_count=result.item_count,
        subtotal=result.subtotal,
        vat_groups=[
            VatGroupResponse(vat_pct=group.vat_pct, base=group.base, vat=group.vat)
            for group in result.vat_groups
        ],
        liquor_tax=result.liquor_tax,
        total=result.total,
    )


def _seller(contact: SellerContact | None) -> SellerContactResponse | None:
    if contact is None:
        return None
    return SellerContactResponse(
        code=contact.code, name=contact.name, email=contact.email, phone=contact.phone
    )


def _summary_values(quotation: Quotation) -> dict[str, object]:
    valid_from = quotation.valid_from or quotation.created_at.date()
    return {
        "validity_days": quotation.validity_days,
        "valid_from": valid_from,
        "valid_until": valid_from + timedelta(days=quotation.validity_days),
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
        conditions=list(quotation.conditions),
        include_seller=quotation.include_seller,
        quote_quantities=quotation.quote_quantities,
        offer_scope=quotation.offer_scope,  # type: ignore[arg-type]
        seller_contact=_seller(quotation.seller_contact),
        offer_summary=_summary(detail),
        lines=[_line(line, quotation.offer_scope) for line in detail.lines],
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

    return _detail(
        await service.create(principal, body.customer_nit, body.window_months, body.validity_days)
    )


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


def _requested(item: RequestedProduct, refs: list[QuotationRef]) -> RequestedProductResponse:
    return RequestedProductResponse(
        id=item.id,
        description=item.description,
        supplier_name=item.supplier_name,
        estimated_price=item.estimated_price,
        vat_pct=item.vat_pct,
        status=item.status,  # type: ignore[arg-type]
        status_label=REQUESTED_STATUS_LABELS.get(item.status, item.status),
        siesa_sku=item.siesa_sku,
        note=item.note,
        created_by=item.created_by,
        created_at=item.created_at,
        updated_by=item.updated_by,
        updated_at=item.updated_at,
        quotations=[
            RequestedQuotationResponse(
                id=ref.id,
                customer_name=ref.customer_name,
                customer_nit=ref.customer_nit,
                status=ref.status,  # type: ignore[arg-type]
                status_label=STATUS_LABELS.get(ref.status, ref.status),
                created_by=ref.created_by,
            )
            for ref in refs
        ],
    )


# Declared before ``/{quotation_id}`` so the path is not taken for an id.
@router.get(
    "/requested-products",
    dependencies=[_READ_GATE],
    response_model=RequestedProductPageResponse,
    summary="Products advisors asked for that SIESA does not have",
)
@inject
async def list_requested_products(
    status_filter: RequestedStatus | None = Query(default=None, alias="status"),  # noqa: B008
    q: str | None = Query(default=None, max_length=100),
    page: int = Query(default=1, ge=1),
    size: int = Query(default=20, ge=1, le=MAX_PAGE_SIZE),
    principal: Principal = _principal,
    service: QuotationService = _service,
) -> RequestedProductPageResponse:
    """``solicitado`` first, then the newest. ``q`` matches the description or the supplier;
    each item lists only the quotations the caller can open. Quoters and validators."""

    result, refs = await service.requested_products(
        principal, status=status_filter, q=q, page=page, size=size
    )
    return RequestedProductPageResponse(
        items=[_requested(item, refs.get(item.id, [])) for item in result.items],
        total=result.total,
        page=result.page,
        size=result.size,
    )


@router.patch(
    "/requested-products/{product_id}",
    dependencies=[_READ_GATE],
    response_model=RequestedProductResponse,
    summary="Mark a requested product created, discarded or requested",
)
@inject
async def update_requested_product(
    product_id: UUID,
    body: RequestedProductUpdate,
    principal: Principal = _principal,
    service: QuotationService = _service,
) -> RequestedProductResponse:
    """Validators only. ``creado`` needs a SIESA reference that exists and is active; back to
    ``solicitado`` clears it. ``note`` changes only when sent."""

    product, refs = await service.update_requested_product(
        principal,
        product_id,
        status=body.status,
        siesa_sku=body.siesa_sku,
        note=body.note,
        set_note="note" in body.model_fields_set,
    )
    return _requested(product, refs)


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

    changes = [item.change() for item in body.lines]
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


@router.post(
    "/{quotation_id}/local-lines",
    dependencies=[_READ_GATE],
    response_model=QuotationResponse,
    summary="Quote a product SIESA does not have",
)
@inject
async def add_local_line(
    quotation_id: UUID,
    body: LocalLineRequest,
    principal: Principal = _principal,
    service: QuotationService = _service,
) -> QuotationResponse:
    """A product only for the quotation (``origin: local``, SKU ``LOC-<n>``): description,
    optional supplier, price before VAT and VAT. It has no cost, the rule does not judge it
    and it never reaches the workbook or the SIESA files. 422 on an invalid field; 409 when
    the quotation changed since it was read or awaits validation."""

    return _detail(
        await service.add_local_line(
            principal,
            quotation_id,
            body.version,
            description=body.description,
            supplier_name=body.supplier_name,
            price=body.price,
            vat_pct=body.vat_pct,
            requested_product_id=body.requested_product_id,
        )
    )


@router.post(
    "/{quotation_id}/lines/{sku}/promote",
    dependencies=[_READ_GATE],
    response_model=QuotationResponse,
    summary="Swap a local product for its new SIESA reference",
)
@inject
async def promote_line(
    quotation_id: UUID,
    sku: str,
    body: VersionRequest,
    principal: Principal = _principal,
    service: QuotationService = _service,
) -> QuotationResponse:
    """Only when BI marked its requested product ``creado``: the SIESA reference takes the
    local line's place (``agregado``) with its new price and quantity. 422 otherwise."""

    return _detail(await service.promote_line(principal, quotation_id, body.version, sku))


@router.patch(
    "/{quotation_id}/offer",
    dependencies=[_READ_GATE],
    response_model=QuotationResponse,
    summary="Offer settings",
)
@inject
async def update_offer(
    quotation_id: UUID,
    body: OfferRequest,
    principal: Principal = _principal,
    service: QuotationService = _service,
) -> QuotationResponse:
    """Validity, conditions, seller, quantities and which lines the offer carries. Only the
    keys sent change. Any status; never changes it. 409 on a stale version."""

    return _detail(
        await service.update_offer(
            principal,
            quotation_id,
            body.version,
            validity_days=body.validity_days,
            conditions=body.conditions,
            include_seller=body.include_seller,
            quote_quantities=body.quote_quantities,
            offer_scope=body.offer_scope,
        )
    )


@router.get(
    "/{quotation_id}/offer.xlsx",
    dependencies=[_READ_GATE],
    response_class=Response,
    responses={
        200: {"content": {"application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": {}}}
    },
    summary="Download the customer offer",
)
@inject
async def download_offer(
    quotation_id: UUID,
    principal: Principal = _principal,
    service: QuotationService = _service,
) -> Response:
    """The offer for the customer, in any status: items, totals and conditions. Never cost,
    margins, supports or rule data."""

    result = await service.offer_file(principal, quotation_id)
    return _file(result.content, result.filename, result.media_type)


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
    """SKUs the advisor added, local products, or SKUs inactive in SIESA; an active
    sales-window SKU stays (leave its new price empty)."""

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
