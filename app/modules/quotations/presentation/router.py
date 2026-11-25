"""Endpoints of the quoter (``/quotations``)."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Annotated, Any, Literal
from urllib.parse import quote
from uuid import UUID

from dependency_injector.wiring import Provide, inject
from fastapi import APIRouter, Depends, File, Form, Query, Response, UploadFile, status
from pydantic import BaseModel, Field, model_validator

from app.app_container import ApplicationContainer
from app.core.errors import ForbiddenError, InvalidInputError
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
from app.modules.quotations.domain import offer, pricing, rules, vocabulary
from app.modules.quotations.domain.entities import (
    AppliedSupport,
    Quotation,
    QuotationDetail,
    QuotationLine,
    SellerContact,
    TemporaryCustomer,
)
from app.modules.quotations.domain.manual import MANUAL
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
    REVIEW_LABELS,
    STATUS_LABELS,
    WARNING_LABELS,
)

router = APIRouter()
# The quoter and the list updates share these endpoints; the service checks each
# quotation against the module of its kind.
_READ_GATE = Depends(require_permission("cotizador", "actualizacion-listas", "listas-principales"))
_service = Depends(Provide[ApplicationContainer.v1_container.quotation_service])
_principal = Depends(get_current_principal)
# ``cotizacion``, ``lista`` or ``principal``: which module a listing, search or permission
# check is for.
_KIND = Query(default="cotizacion")
_LIST_KIND = Query(default="lista")

Status = Literal["borrador", "pendiente_validacion", "pendiente_carga", "cargada"]
Kind = Literal["cotizacion", "lista", "principal"]
ListKind = Literal["lista", "principal"]
ReviewStatus = Literal["sin_marcar", "por_validar", "validada"]
ListAction = Literal["actualizacion", "creacion"]
Compliance = Literal["cumple", "bajo_minimo", "sin_objetivo", "sin_costo"]
OfferScope = Literal["changed", "all"]


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
    # May ask an internal support from a customer quotation (quotes and does not review).
    can_request_support: bool = False
    # BI sees the calculation rules (GET /rules); Comercial the operating manual (GET /manual).
    can_see_rules: bool = False
    # Sees others' quotations with ``mine=false`` («Todas»): validators, moderators, and the
    # call center's leader those of its members (2026-11-23).
    can_see_team: bool = False
    # What ``mine=false`` lists (2026-11-23): ``all`` («Todas»: validators, moderators,
    # Comercial's directors) or ``assigned`` («Mi equipo»: a coordinator's sellers, the call
    # center's members); null when the caller sees only their own.
    team_scope: Literal["all", "assigned"] | None = None
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
    business_group: str | None = None


class CustomerPageResponse(BaseModel):
    items: list[CustomerResponse]
    total: int
    page: int
    size: int
    seller_linked: bool


class TemporaryCustomerRequest(BaseModel):
    """A customer not in SIESA yet (texts checked by the service: 1 to 120 characters)."""

    name: str | None = Field(default=None, max_length=1000)
    legal_name: str | None = Field(default=None, max_length=1000)
    trade_name: str | None = Field(default=None, max_length=1000)
    nit: str | None = Field(default=None, max_length=30)


class GroupResponse(BaseModel):
    """A business group to quote: its active customers (NITs) and branches."""

    name: str
    customers: int
    branches: int


class GroupPageResponse(BaseModel):
    items: list[GroupResponse]
    total: int
    page: int
    size: int
    seller_linked: bool


class CreateRequest(BaseModel):
    """A SIESA customer by ``customer_nit``, a whole ``business_group``, or a
    ``temporary_customer`` with the ``billing_center_code`` its prices come from (one of
    the three)."""

    customer_nit: str | None = Field(default=None, max_length=30)
    business_group: str | None = Field(
        default=None,
        max_length=200,
        description="Quote a whole business group (its name, from /quotations/business-groups)",
    )
    temporary_customer: TemporaryCustomerRequest | None = None
    billing_center_code: str | None = Field(
        default=None,
        max_length=10,
        description="Required for a temporary customer; for a SIESA customer it replaces "
        "the center of its branches",
    )
    window_months: Literal[6, 12] = 12
    validity_days: int = Field(
        default=DEFAULT_VALIDITY_DAYS,
        ge=1,
        le=MAX_VALIDITY_DAYS,
        description="Days the offer holds",
    )
    with_history: bool = Field(
        default=True,
        description="False starts a SIESA customer's quotation empty, without its sales",
    )
    customer_branch_id: str | None = Field(
        default=None,
        max_length=20,
        description=(
            "With customer_nit: quote that branch alone (/quotations/customers/{nit}/branches); "
            "its list, channel, center, sales and NIT-sucursal supports"
        ),
    )


class CustomerBranchResponse(BaseModel):
    """A branch of a customer, to quote it alone."""

    branch_id: str
    branch_name: str | None
    establishment_name: str | None
    city: str | None
    channel: str | None
    salesperson_name: str | None
    is_active: bool


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


class OfferRequest(BaseModel):
    """Offer settings; only the keys sent change (``null`` leaves one as it is)."""

    version: int = Field(ge=1)
    validity_days: int | None = Field(default=None, ge=1, le=MAX_VALIDITY_DAYS)
    conditions: list[str] | None = Field(default=None, max_length=200)
    include_seller: bool | None = None
    quote_quantities: bool | None = None
    offer_scope: OfferScope | None = None
    include_variation: bool | None = None


WarehouseClass = Literal["BP", "BSP", "PROMO"]


class WarehouseGroupRequest(BaseModel):
    """A whole group of warehouses inside a chosen center."""

    center_code: str = Field(min_length=1, max_length=10)
    classification: WarehouseClass


class BillingCenterRequest(BaseModel):
    """The operation centers to cost from: ``national`` alone, or one or more centers.

    Inside the centers the warehouses are chosen by whole groups (2026-11-25):
    ``cost_warehouse_groups``, each a center and its main (``BP``), spot-request (``BSP``)
    or promotional (``PROMO``) warehouses (``GET /billing-centers``). A center with main or
    spot-request ones chosen costs and shows stock from those only; one without any keeps
    its own. Promotional ones are added on top, for every line (the highest cost with stock
    wins). ``cost_warehouses`` (one by one, earlier clients) must make up whole groups.
    ``billing_center_code`` is the single center of earlier clients.
    """

    version: int = Field(ge=1)
    national: bool = False
    cost_centers: list[Annotated[str, Field(min_length=1, max_length=10)]] = Field(
        default_factory=list, max_length=30
    )
    cost_warehouse_groups: list[WarehouseGroupRequest] = Field(default_factory=list, max_length=90)
    cost_warehouses: list[Annotated[str, Field(min_length=1, max_length=10)]] = Field(
        default_factory=list, max_length=100
    )
    billing_center_code: str | None = Field(default=None, min_length=1, max_length=10)

    @property
    def codes(self) -> list[str]:
        """The chosen centers; none is Nacional."""

        single = [self.billing_center_code] if self.billing_center_code else []
        return [*self.cost_centers, *single]

    @property
    def groups(self) -> list[tuple[str, str]]:
        return [(item.center_code, item.classification) for item in self.cost_warehouse_groups]

    @model_validator(mode="after")
    def _one_choice(self) -> BillingCenterRequest:
        if self.national and (self.codes or self.cost_warehouses or self.cost_warehouse_groups):
            raise ValueError("Nacional va sola: no se combina con centros de operación ni bodegas")
        if not self.national and not self.codes:
            raise ValueError("Elige Nacional o al menos un centro de operación")
        return self


class ValidateRequest(BaseModel):
    """Validate: for ``creacion`` the code of the new list is required."""

    version: int = Field(ge=1)
    price_list_id: str | None = Field(
        default=None, max_length=3, description="Code of the new list (creation only)"
    )
    new_list_name: str | None = Field(default=None, max_length=40)


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


class VersionRequest(BaseModel):
    version: int = Field(ge=1)


class ConfirmRequest(BaseModel):
    """Close the draft and send it to validation (the validator gives a new list's code)."""

    version: int = Field(ge=1)
    list_action: ListAction | None = None
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
    # JDH's authorized minimum margin: when set, the line's minimum price uses it.
    authorized_margin_pct: Decimal | None = None
    # % Margen Garantizado: the margin the supplier guarantees. Without the supplier's
    # minimum its ``amount`` is what it pays at the line's price (new, else last) and
    # ``list_amount`` the one at the list price.
    guaranteed_margin_pct: Decimal | None = None
    # The supplier's minimum margin (2026-11-23): the amount is then fixed, the one at the
    # supported price cost / (1 − margin), which is the line's minimum price.
    supplier_min_margin_pct: Decimal | None = None
    list_amount: Decimal | None = None


class CandidateResponse(BaseModel):
    support_id: UUID
    calc_method: str
    program: str | None
    reason: str


class SupportOptionResponse(BaseModel):
    """A list update: one support of the list's customers on the SKU, with its prices."""

    support_id: UUID
    kind: str
    calc_method: str
    program: str | None
    detail: str
    amount: Decimal | None
    required_margin_pct: Decimal | None
    min_price: Decimal | None
    optimal_price: Decimal | None
    applied: bool
    # Why it cannot be applied (in review, one warehouse, rebate, no value...).
    reason: str | None
    # Set when the support is for one customer of the list.
    customer_nit: str | None
    valid_to: date | None
    # Set when the support is for a business group (no NIT): it covers that group, not the
    # whole list. Both empty: a general support.
    customer_group: str | None = None


class SupportRequestRefResponse(BaseModel):
    """The latest internal support request of the SKU in this quotation."""

    id: UUID
    status: Literal["pendiente", "aprobada", "rechazada"]
    decision: Literal["persistente", "unica_vez"] | None
    approved_margin_pct: Decimal | None


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
    cost_source: Literal["cotizacion", "inventario", "alza"] | None
    cost_source_label: str | None
    # The operation center whose warehouses set ``cost``; ``None`` for Nacional or a list
    # with its own warehouses.
    cost_center: str | None
    # New cost of a price increase SIESA does not have yet, the third value compared.
    increase_cost: Decimal | None
    # The date of the SKU's latest supplier price increase, when it has one (2026-11-23).
    increase_on: date | None = None
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
    # ``local``: a product only for the quotation (SKU ``LOC-<n>``), not in SIESA; ``lista``:
    # a SKU with a price in force on the list a list update starts from.
    origin: Literal["historico", "agregado", "local", "lista"]
    origin_label: str
    # A list update: ``last_price`` is the list's price in force, and these are the net price
    # and unit cost of goods sold of the SKU's last sale (``last_sale_date``).
    last_sale_price: Decimal | None = None
    last_sale_cost: Decimal | None = None
    # A list update: every support of the list's customers on the SKU, lowest minimum first.
    support_options: list[SupportOptionResponse] = Field(default_factory=list)
    # Product category in SIESA; ``None`` when unknown and for a local product.
    category: str | None
    # Inventory units the customer would buy.
    quantity: Decimal | None
    # New price, else last price (a local product: its new price).
    offer_price: Decimal | None
    # Whether the customer offer carries the line (``offer_scope``; never an inactive one).
    in_offer: bool
    # A list update: the item's review, who set it and when, the person's observation, and
    # the load that took it to SIESA (``loaded`` true; it left the staging).
    review_status: ReviewStatus = "sin_marcar"
    review_label: str = "Sin marcar"
    reviewed_by: str | None = None
    reviewed_at: datetime | None = None
    note: str | None = None
    load_id: UUID | None = None
    loaded: bool = False
    # A customer quotation: the latest internal support request of the SKU, if any.
    support_request: SupportRequestRefResponse | None = None
    # Units on hand and available in the warehouses that cost the line.
    stock_on_hand: Decimal | None = None
    stock_available: Decimal | None = None


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


class TemporaryCustomerResponse(BaseModel):
    name: str
    legal_name: str
    trade_name: str | None
    nit: str | None


class CenterWarehouseResponse(BaseModel):
    """A warehouse that can be chosen inside its operation center."""

    code: str
    name: str | None
    # ``BP`` main warehouses, ``BSP`` spot requests (SIESA's groups for the platform),
    # ``PROMO`` promotional ones (added on top of the center's, read for every line).
    classification: WarehouseClass
    classification_label: str


class WarehouseGroupResponse(BaseModel):
    """A group of warehouses chosen inside a center; its breakdown is in
    ``cost_warehouses``."""

    center_code: str
    classification: WarehouseClass
    classification_label: str


class BillingCenterResponse(BaseModel):
    """An operation center a quotation can be billed and priced from, with the main and
    spot-request warehouses that can be chosen inside it (empty: none to choose).

    ``regional`` is the liquor tax regional it bills with; ``regional_name`` the center's
    regional in the master (``ANTIOQUIA``, ``ORIENTE``…; 2026-11-25)."""

    code: str
    name: str | None
    regional: Literal["nacional", "costa"]
    regional_label: str
    regional_code: str | None = None
    regional_name: str | None = None
    warehouses: list[CenterWarehouseResponse] = Field(default_factory=list)


class SummaryResponse(BaseModel):
    """A quotation in a list."""

    id: UUID
    customer_nit: str
    customer_name: str | None
    channel: str | None
    # ``None`` for a temporary customer.
    price_list_id: str | None
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
    uploaded_at: datetime | None
    uploaded_by: str | None
    version: int
    validity_days: int
    valid_from: date
    valid_until: date
    is_temporary_customer: bool
    temporary_customer: TemporaryCustomerResponse | None
    # ``cotizacion`` or ``lista`` (the update of an existing price list).
    kind: Kind = "cotizacion"
    # A list update: ``cliente`` (from a customer's list) or ``lista``.
    source: Literal["cliente", "lista"] | None = None
    price_list_name: str | None = None
    # Quoted to a business group: its name (also ``customer_name``) and its NITs.
    business_group: str | None = None
    group_customers: int | None = None
    # Quoted to one branch of the customer (2026-11-24); ``null`` is the NIT as a whole.
    customer_branch_id: str | None = None
    # When SIESA activates the prices (set at /confirm). In «Por cargar», one already past
    # means the files come out with today's date (SIESA does not load a past one).
    activation_date: date | None = None
    # A customer draft never sent to validation is deleted for good on this day (30 days
    # after it was created, 2026-11-23); ``null`` for any other.
    deleted_on: date | None = None


class QuotationResponse(SummaryResponse):
    """A quotation with its header and lines."""

    # Whether the caller may change it, whatever its status: false for those who only see it
    # (a coordinator's team, a director, the call center's leader; 2026-11-23).
    can_edit: bool = True
    channel_code: str | None
    price_list_customers: int | None
    billing_center_code: str | None
    billing_center_name: str | None
    # Operation centers the lines are costed from; empty is Nacional (every warehouse).
    cost_centers: list[str]
    # The warehouses chosen inside the centers (2026-11-20); a center with none keeps its
    # own warehouses. The breakdown of ``cost_warehouse_groups``.
    cost_warehouses: list[str]
    # The warehouse groups chosen inside the centers (2026-11-25), what the screen shows.
    # Empty with warehouses chosen one by one before that date.
    cost_warehouse_groups: list[WarehouseGroupResponse]
    # A list update's references taken out with its template (2026-11-25); a refresh
    # does not bring them back.
    excluded_skus: list[str]
    # False when the list has its own warehouses (list 1 and the skill's) and no warehouse
    # was chosen: the centers then change only the liquor tax regional, not the cost.
    centers_set_cost: bool
    # False when it started empty: a refresh prices the added SKUs and reads no sales.
    with_history: bool
    # Where ``cost_centers`` comes from: ``auto`` (the customer's branches) or ``manual``
    # (chosen; a refresh keeps it).
    billing_center_source: Literal["auto", "manual"]
    regional: Literal["nacional", "costa"]
    regional_label: str
    price_family: str
    price_family_label: str
    stock_source: str | None
    salesperson_code: str | None
    salesperson_name: str | None
    built_at: datetime
    cost_cut_date: date | None
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
    include_variation: bool
    seller_contact: SellerContactResponse | None
    # ``None`` unless ``quote_quantities``.
    offer_summary: OfferSummaryResponse | None
    lines: list[LineResponse]
    # Only on POST /{id}/lines: what the pasted list did.
    import_result: ImportResultResponse | None = None
    # Only on PUT /{id}/lines: SKUs a margin could not price.
    skipped: list[SkippedResponse] | None = None


class QuotationPageResponse(BaseModel):
    items: list[SummaryResponse]
    total: int
    page: int
    size: int


def _support(item: AppliedSupport | None, line: QuotationLine) -> SupportResponse | None:
    if item is None:
        return None
    guaranteed = item.open_guarantee
    return SupportResponse(
        support_id=item.support_id,
        kind=item.kind,
        calc_method=item.calc_method,
        program=item.program,
        # A guaranteed margin with no supplier minimum pays what is missing at the line's
        # price: that is its amount.
        amount=line.support_amount if guaranteed else item.amount,
        guaranteed_margin_pct=item.guaranteed_margin_pct,
        supplier_min_margin_pct=item.supplier_min_margin_pct,
        list_amount=item.amount,
        share_of_cost=item.share_of_cost,
        required_margin_pct=item.required_margin_pct,
        detail=item.detail,
        conditions=item.conditions,
        needs_review=item.needs_review,
        valid_from=item.valid_from,
        valid_to=item.valid_to,
        authorized_margin_pct=item.authorized_margin_pct,
    )


def _request_ref(item: Any) -> SupportRequestRefResponse | None:
    if item is None:
        return None
    return SupportRequestRefResponse(
        id=item.id,
        status=item.status,
        decision=item.decision,
        approved_margin_pct=item.approved_margin_pct,
    )


def _line(
    line: QuotationLine, scope: str, request: Any = None, *, quantities: bool = False
) -> LineResponse:
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
        cost_center=line.cost_center,
        stock_on_hand=line.stock_on_hand,
        stock_available=line.stock_available,
        increase_cost=line.increase_cost,
        increase_on=line.increase_on,
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
        support=_support(line.support, line),
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
        # Looked up without a category is stored as "" so it is not looked up again.
        category=line.category or None,
        quantity=line.quantity,
        offer_price=offer.offer_price(line),
        in_offer=offer.in_offer(line, scope, quantities=quantities),
        last_sale_price=line.last_sale_price,
        last_sale_cost=line.last_sale_cost,
        support_options=[
            SupportOptionResponse(
                support_id=item.support_id,
                kind=item.kind,
                calc_method=item.calc_method,
                program=item.program,
                detail=item.detail,
                amount=item.amount,
                required_margin_pct=item.required_margin_pct,
                min_price=item.min_price,
                optimal_price=item.optimal_price,
                applied=item.applied,
                reason=item.reason,
                customer_nit=item.customer_nit,
                valid_to=item.valid_to,
                customer_group=item.customer_group,
            )
            for item in line.support_options
        ],
        review_status=line.review_status,  # type: ignore[arg-type]
        review_label=REVIEW_LABELS.get(line.review_status, line.review_status),
        reviewed_by=line.reviewed_by,
        reviewed_at=line.reviewed_at,
        note=line.note,
        load_id=line.load_id,
        loaded=line.load_id is not None,
        support_request=_request_ref(request),
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
        "uploaded_at": quotation.uploaded_at,
        "uploaded_by": quotation.uploaded_by,
        "version": quotation.version,
        "is_temporary_customer": quotation.is_temporary_customer,
        "temporary_customer": TemporaryCustomerResponse(**quotation.temporary_customer.as_dict())
        if quotation.temporary_customer
        else None,
        "business_group": quotation.business_group,
        "group_customers": quotation.group_customers,
        "customer_branch_id": quotation.customer_branch_id,
        "kind": quotation.kind,
        "source": quotation.source,
        "price_list_name": quotation.price_list_name,
        "activation_date": quotation.activation_date,
        "deleted_on": _deleted_on(quotation),
    }


def _deleted_on(quotation: Quotation) -> date | None:
    """When a customer draft never sent to validation is deleted (job
    ``quotations-stale-drafts``, 2026-11-23); ``None`` for any other."""

    if (
        quotation.kind != vocabulary.QUOTATION
        or quotation.status != vocabulary.DRAFT
        or quotation.confirmed_at is not None
    ):
        return None
    return quotation.created_at.date() + timedelta(days=vocabulary.STALE_DRAFT_DAYS)


def _warehouse_groups(quotation: Quotation) -> list[WarehouseGroupResponse]:
    """The chosen groups, by center in the quotation's order."""

    return [
        WarehouseGroupResponse(
            center_code=center,
            classification=kind,  # type: ignore[arg-type]
            classification_label=pricing.WAREHOUSE_CLASSES.get(kind, kind),
        )
        for center in quotation.cost_centers
        for kind in quotation.warehouse_groups.get(center, ())
    ]


def _detail(
    detail: QuotationDetail,
    requests: Mapping[str, Any] | None = None,
    can_edit: bool = True,
) -> QuotationResponse:
    quotation = detail.quotation
    return QuotationResponse(
        **_summary_values(quotation),  # type: ignore[arg-type]
        can_edit=can_edit,
        channel_code=quotation.channel_code,
        price_list_customers=quotation.price_list_customers,
        billing_center_code=quotation.billing_center_code,
        billing_center_name=quotation.billing_center_name,
        cost_centers=list(quotation.cost_centers),
        cost_warehouses=[
            code
            for center in quotation.cost_centers
            for code in (
                *quotation.cost_warehouses.get(center, ()),
                *quotation.promo_warehouses.get(center, ()),
            )
        ],
        cost_warehouse_groups=_warehouse_groups(quotation),
        excluded_skus=list(quotation.excluded_skus),
        centers_set_cost=bool(quotation.cost_warehouses or quotation.promo_warehouses)
        or pricing.centers_set_cost(quotation.price_list_id),
        with_history=quotation.with_history,
        billing_center_source=quotation.billing_center_source,  # type: ignore[arg-type]
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
        include_variation=quotation.include_variation,
        seller_contact=_seller(quotation.seller_contact),
        offer_summary=_summary(detail),
        lines=[
            _line(
                line,
                quotation.offer_scope,
                (requests or {}).get(line.sku),
                quantities=quotation.quote_quantities,
            )
            for line in detail.lines
        ],
    )


async def _respond(
    principal: Principal, service: QuotationService, detail: QuotationDetail
) -> QuotationResponse:
    """The quotation with the internal support request of each line, and whether the caller
    may change it."""

    return _detail(
        detail,
        await service.support_requests_for(detail),
        can_edit=await service.can_edit(principal, detail.quotation),
    )


@router.get("/permissions", response_model=PermissionsResponse, summary="What the caller may do")
@inject
async def permissions(
    kind: Kind = _KIND,
    principal: Principal = _principal,
    service: QuotationService = _service,
) -> PermissionsResponse:
    """Whether the caller may quote (or update lists, ``kind=lista``), and the warehouse
    salesperson whose e-mail is theirs."""

    result = await service.permissions(principal, kind)
    seller = result.seller
    return PermissionsResponse(
        can_manage=result.can_manage,
        can_validate=result.can_validate,
        sales_scope=result.sales_scope,  # type: ignore[arg-type]
        can_request_support=result.can_request_support,
        can_see_rules=result.can_see_rules,
        can_see_team=result.can_see_team,
        team_scope=result.team_scope,  # type: ignore[arg-type]
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
    principal: Principal = _principal,
    service: QuotationService = _service,
) -> list[RuleSectionResponse]:
    """Every calculation rule with the values in force. BI only (``can_see_rules``);
    Comercial reads ``GET /manual``."""

    if not await service.can_see_rules(principal):
        raise ForbiddenError("Las reglas de cálculo del cotizador son de BI: consulta el manual")
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


class ManualStepResponse(BaseModel):
    title: str
    text: str


class ManualSectionResponse(BaseModel):
    code: str
    title: str
    steps: list[ManualStepResponse]


@router.get(
    "/manual",
    dependencies=[_READ_GATE],
    response_model=list[ManualSectionResponse],
    summary="The quoter's operating manual",
)
async def quotation_manual(_: Principal = _principal) -> list[ManualSectionResponse]:
    """How to use the quoter, for Comercial: steps, columns, supports and validation, without
    the calculation rules (BI's, ``GET /rules``)."""

    return [
        ManualSectionResponse(
            code=section.code,
            title=section.title,
            steps=[ManualStepResponse(title=s.title, text=s.text) for s in section.steps],
        )
        for section in MANUAL
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
    kind: Kind = _KIND,
    principal: Principal = _principal,
    service: QuotationService = _service,
) -> CustomerPageResponse:
    """The caller's portfolio (``mine``: customers of the salespeople whose e-mail is theirs)
    or every customer, merged by NIT and active first. For a list update (``kind=lista``)
    every customer."""

    result, linked = await service.customers(principal, scope, q, page, size, kind)
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
                business_group=item.business_group,
            )
            for item in result.items
        ],
        total=result.total,
        page=result.page,
        size=result.size,
        seller_linked=linked,
    )


@router.get(
    "/customers/{customer_nit}/branches",
    dependencies=[_READ_GATE],
    response_model=list[CustomerBranchResponse],
    summary="Branches of a customer, to quote one",
)
@inject
async def customer_branches(
    customer_nit: str,
    principal: Principal = _principal,
    service: QuotationService = _service,
) -> list[CustomerBranchResponse]:
    """Las sucursales del cliente, activas primero. La UI ofrece «Cotización por sucursal»
    solo cuando el cliente tiene más de una."""

    return [
        CustomerBranchResponse(
            branch_id=item.branch_id,
            branch_name=item.branch_name,
            establishment_name=item.establishment_name,
            city=item.city,
            channel=item.channel,
            salesperson_name=item.salesperson_name,
            is_active=item.is_active,
        )
        for item in await service.customer_branches(principal, customer_nit)
    ]


@router.get(
    "/business-groups",
    dependencies=[_READ_GATE],
    response_model=GroupPageResponse,
    summary="Business groups to quote",
)
@inject
async def business_groups(
    scope: Literal["mine", "all"] = Query(default="mine"),
    q: str | None = Query(default=None, max_length=100),
    page: int = Query(default=1, ge=1),
    size: int = Query(default=20, ge=1, le=MAX_PAGE_SIZE),
    principal: Principal = _principal,
    service: QuotationService = _service,
) -> GroupPageResponse:
    """The business groups of the caller's portfolio (a group with at least one customer of
    their salespeople), by name; BI and Comercial's leaders choose ``mine`` or ``all``. Each
    with the whole group's active customers and branches."""

    result, linked = await service.business_groups(principal, scope, q, page, size)
    return GroupPageResponse(
        items=[
            GroupResponse(name=item.name, customers=item.customers, branches=item.branches)
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
    summary="Build a quotation of a customer or a business group",
)
@inject
async def create_quotation(
    body: CreateRequest,
    principal: Principal = _principal,
    service: QuotationService = _service,
) -> QuotationResponse:
    """Every SKU sold to the customer in the window, at its last price, with current cost,
    target margin and supports. A temporary customer starts empty, priced from the chosen
    center; the sales scope does not apply to it."""

    temporary = body.temporary_customer
    return await _respond(
        principal,
        service,
        await service.create(
            principal,
            body.customer_nit,
            body.window_months,
            body.validity_days,
            temporary_customer=TemporaryCustomer(
                name=temporary.name or "",
                legal_name=temporary.legal_name or "",
                trade_name=temporary.trade_name,
                nit=temporary.nit,
            )
            if temporary
            else None,
            billing_center_code=body.billing_center_code,
            with_history=body.with_history,
            business_group=body.business_group,
            customer_branch_id=body.customer_branch_id,
        ),
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
    kind: Kind = _KIND,
    principal: Principal = _principal,
    service: QuotationService = _service,
) -> QuotationPageResponse:
    """Quotations (or list updates, ``kind=lista``), most recently changed first."""

    result = await service.list(
        principal,
        mine=mine,
        status=status_filter,
        customer_nit=customer_nit,
        q=q,
        page=page,
        size=size,
        kind=kind,
    )
    return QuotationPageResponse(
        items=[SummaryResponse(**_summary_values(item)) for item in result.items],  # type: ignore[arg-type]
        total=result.total,
        page=result.page,
        size=result.size,
    )


class PriceListResponse(BaseModel):
    """A SIESA price list to update."""

    code: str
    name: str | None
    is_active: bool
    # Active branches assigned to it (SIESA counts branches, not NITs).
    active_branches: int | None
    # It belongs to the other module: a main list searched from ``lista``, or any other list
    # from ``principal``. It cannot be chosen here.
    restricted: bool
    # One of the main lists (module ``listas-principales``).
    main: bool = False


class PriceListPageResponse(BaseModel):
    """The lists found, shaped like the other searches (one page of up to 50)."""

    items: list[PriceListResponse]
    total: int
    page: int
    size: int


class ListUpdateRequest(BaseModel):
    """The list to update: a customer's (``customer_nit``) or one by ``price_list_id``."""

    customer_nit: str | None = Field(default=None, max_length=30)
    price_list_id: str | None = Field(default=None, max_length=3)
    # ``lista`` (Actualización de listas) or ``principal`` (Listas principales).
    kind: ListKind = "lista"


# Declared before ``/{quotation_id}`` so the paths are not taken for an id.
@router.get(
    "/price-lists",
    dependencies=[_READ_GATE],
    response_model=PriceListPageResponse,
    summary="SIESA price lists to update, by code or name",
)
@inject
async def price_lists(
    q: str | None = Query(default=None, max_length=80),
    kind: ListKind = _LIST_KIND,
    principal: Principal = _principal,
    service: QuotationService = _service,
) -> PriceListPageResponse:
    """Lists whose code is ``q`` or whose name contains it (accents and case ignored),
    active and most used first; at most 50, in one page. Names repeat: each comes with its
    code. With ``kind=principal`` and no ``q``, every main list. ``restricted`` marks the
    lists of the other module."""

    items = [
        PriceListResponse(
            code=item.code,
            name=item.name,
            is_active=item.is_active,
            active_branches=item.active_branches,
            restricted=not service.list_allowed(kind, item.code),
            main=service.is_main_list(item.code),
        )
        for item in await service.price_lists(principal, q, kind)
    ]
    return PriceListPageResponse(items=items, total=len(items), page=1, size=max(len(items), 1))


class WarehouseStockResponse(BaseModel):
    warehouse: str
    name: str | None
    on_hand: Decimal
    available: Decimal | None


class CenterStockResponse(BaseModel):
    """One operation center's warehouses with stock of the reference."""

    code: str | None
    on_hand: Decimal
    available: Decimal | None
    warehouses: list[WarehouseStockResponse]


class SkuStockResponse(BaseModel):
    """The consolidated stock of a reference (the line's) and its detail by center."""

    sku: str
    on_hand: Decimal
    available: Decimal | None
    centers: list[CenterStockResponse]


class StockBreakdownResponse(BaseModel):
    # Nacional (no center chosen): every main warehouse.
    national: bool
    label: str
    # The warehouses read by code, and the centers read by every warehouse SIESA puts there.
    warehouses: list[str]
    centers: list[str]
    stock_at: datetime | None
    items: list[SkuStockResponse]


def _stock_items(result: Any) -> list[SkuStockResponse]:
    by_sku: dict[str, dict[str | None, list[Any]]] = {}
    for row in result.rows:
        by_sku.setdefault(row.sku, {}).setdefault(row.center_code, []).append(row)
    items = []
    for sku in result.skus:
        total = result.totals.get(sku)
        centers = []
        for code, rows in sorted(by_sku.get(sku, {}).items(), key=lambda item: item[0] or "~"):
            available = (
                None
                if any(row.available is None for row in rows)
                else sum((row.available for row in rows), Decimal(0))
            )
            centers.append(
                CenterStockResponse(
                    code=code,
                    on_hand=sum((row.on_hand for row in rows), Decimal(0)),
                    available=available,
                    warehouses=[
                        WarehouseStockResponse(
                            warehouse=row.warehouse,
                            name=row.warehouse_name,
                            on_hand=row.on_hand,
                            available=row.available,
                        )
                        for row in rows
                    ],
                )
            )
        items.append(
            SkuStockResponse(
                sku=sku,
                on_hand=total.on_hand if total else Decimal(0),
                available=total.available if total else Decimal(0),
                centers=centers,
            )
        )
    return items


class LastSalesStatusResponse(BaseModel):
    """Whether a list update has its last sales, without reading its lines."""

    read: bool = Field(description="False while it carries ultima_venta_no_leida")
    retrying: bool = Field(description="The API is reading them again in the background")
    version: int


@router.get(
    "/{quotation_id}/last-sales",
    dependencies=[_READ_GATE],
    response_model=LastSalesStatusResponse,
    summary="Whether a list update has its last sales",
)
@inject
async def last_sales_status(
    quotation_id: UUID,
    principal: Principal = _principal,
    service: QuotationService = _service,
) -> LastSalesStatusResponse:
    """Light to poll (only the header): ``read`` turns true when the background retry wrote
    the last sales, without a new ``version``; then read the detail once. ``read: false``
    with ``retrying: false`` means the retry gave up (or the API restarted): only a refresh
    tries again."""

    header = (await service.get(principal, quotation_id, [])).quotation
    return LastSalesStatusResponse(
        read=vocabulary.SALES_NOT_READ not in header.warnings,
        retrying=service.retrying_sales(quotation_id),
        version=header.version,
    )


@router.get(
    "/{quotation_id}/stock-breakdown",
    dependencies=[_READ_GATE],
    response_model=StockBreakdownResponse,
    summary="Stock of the references in the warehouses of the chosen operation centers",
)
@inject
async def stock_breakdown(
    quotation_id: UUID,
    sku: list[str] | None = Query(default=None, max_length=500),  # noqa: B008
    principal: Principal = _principal,
    service: QuotationService = _service,
) -> StockBreakdownResponse:
    """The stock of a quotation's or list update's references (``sku``, repeatable; none:
    every SIESA line, up to 500) in the sellable warehouses of its operation centers, summed
    per reference as each line shows it, and detailed by center and warehouse. No center
    chosen is *Nacional*: every main warehouse. From SIESA's stock photo of the morning."""

    result = await service.stock_breakdown(principal, quotation_id, sku)
    return StockBreakdownResponse(
        national=result.scope.national,
        label=result.scope.label,
        warehouses=list(result.scope.warehouses),
        centers=list(result.scope.centers),
        stock_at=max((row.snapshot_at for row in result.rows if row.snapshot_at), default=None),
        items=_stock_items(result),
    )


class FreeCodesResponse(BaseModel):
    """Free codes for a new price list, in order."""

    items: list[str]
    # Of the 46 656 codes of 3 characters (0-9, A-Z).
    free: int
    used: int
    # Pass it as ``after`` for the next page; ``null`` on the last one.
    next_after: str | None


# Declared before ``/{quotation_id}`` so the path is not taken for an id.
@router.get(
    "/list-codes/available",
    dependencies=[_READ_GATE],
    response_model=FreeCodesResponse,
    summary="Free codes for a new price list",
)
@inject
async def available_list_codes(
    after: str | None = Query(default=None, max_length=3),
    size: int = Query(default=30, ge=1, le=200),
    principal: Principal = _principal,
    service: QuotationService = _service,
) -> FreeCodesResponse:
    """The 3-character codes (digits and letters, digits first) that SIESA does not have and
    no validated quotation took, in order, after ``after``. The validator of a list creation
    picks one of them; ``free`` and ``used`` count the whole range."""

    result = await service.available_list_codes(principal, after, size)
    return FreeCodesResponse(
        items=list(result.items), free=result.free, used=result.used, next_after=result.next_after
    )


@router.post(
    "/list-updates",
    dependencies=[_READ_GATE],
    response_model=QuotationResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Start the update of an existing price list",
)
@inject
async def create_list_update(
    body: ListUpdateRequest,
    principal: Principal = _principal,
    service: QuotationService = _service,
) -> QuotationResponse:
    """Every SKU with a price in force on the list, compared against that price, with the
    quoter's cost, target, rule and states, the last sale to the customer (or the list's
    customers) and every support of the list's customers. No offer and no quantities."""

    return await _respond(
        principal,
        service,
        await service.create_list_update(
            principal,
            customer_nit=body.customer_nit,
            price_list_id=body.price_list_id,
            kind=body.kind,
        ),
    )


@router.get(
    "/billing-centers",
    dependencies=[_READ_GATE],
    response_model=list[BillingCenterResponse],
    summary="Operation centers a quotation can be priced from",
)
@inject
async def billing_centers(
    principal: Principal = _principal,
    service: QuotationService = _service,
) -> list[BillingCenterResponse]:
    """Active operation centers of the ``centros-operacion`` master, by code, with the
    liquor tax regional each one bills with (``costa`` for 004) and the main (``BP``) and
    spot-request (``BSP``) warehouses that can be chosen inside each one. When the warehouse
    master does not answer the centers come without warehouses."""

    result = []
    for item in await service.billing_centers(principal):
        regional = pricing.regional(item.code)
        result.append(
            BillingCenterResponse(
                code=item.code,
                name=item.name,
                regional=regional,  # type: ignore[arg-type]
                regional_label=REGIONAL_LABELS.get(regional, regional),
                regional_code=item.regional_code,
                regional_name=item.regional_name,
                warehouses=[
                    CenterWarehouseResponse(
                        code=warehouse.code,
                        name=warehouse.name,
                        classification=warehouse.classification,  # type: ignore[arg-type]
                        classification_label=pricing.WAREHOUSE_CLASSES.get(
                            warehouse.classification, warehouse.classification
                        ),
                    )
                    for warehouse in item.warehouses
                    if warehouse.classification in pricing.WAREHOUSE_CLASSES
                ],
            )
        )
    return result


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
    return await _respond(principal, service, await service.get(principal, quotation_id))


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
    response = await _respond(principal, service, detail)
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
    response = await _respond(principal, service, detail)
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

    return await _respond(
        principal,
        service,
        await service.add_local_line(
            principal,
            quotation_id,
            body.version,
            description=body.description,
            supplier_name=body.supplier_name,
            price=body.price,
            vat_pct=body.vat_pct,
        ),
    )


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

    return await _respond(
        principal,
        service,
        await service.update_offer(
            principal,
            quotation_id,
            body.version,
            validity_days=body.validity_days,
            conditions=body.conditions,
            include_seller=body.include_seller,
            quote_quantities=body.quote_quantities,
            offer_scope=body.offer_scope,
            include_variation=body.include_variation,
        ),
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

    return await _respond(
        principal, service, await service.remove_line(principal, quotation_id, version, sku)
    )


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

    return await _respond(
        principal, service, await service.refresh(principal, quotation_id, body.version)
    )


@router.post(
    "/{quotation_id}/confirm",
    dependencies=[_READ_GATE],
    response_model=QuotationResponse,
    summary="Ask to update or create the price list (to validation)",
)
@inject
async def confirm_quotation(
    quotation_id: UUID,
    body: ConfirmRequest,
    principal: Principal = _principal,
    service: QuotationService = _service,
) -> QuotationResponse:
    """Ask to take the quotation to a price list, from ``borrador`` (sent or not): the only
    step that goes to validation. It needs one priced active line (the rule does not block;
    the validator sees what breaks it). ``actualizacion`` writes to the customer's list;
    ``creacion`` creates a list whose code the validator gives (list 1 and temporary
    customers always go by creation). The quotation is ``pendiente_validacion``."""

    return await _respond(
        principal,
        service,
        await service.confirm(
            principal,
            quotation_id,
            body.version,
            list_action=body.list_action,
            new_list_name=body.new_list_name,
            activation_date=body.activation_date,
        ),
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

    return await _respond(
        principal,
        service,
        await service.return_to_draft(principal, quotation_id, body.version, body.note),
    )


@router.post(
    "/{quotation_id}/validate",
    dependencies=[_READ_GATE],
    response_model=QuotationResponse,
    summary="Validate",
)
@inject
async def validate_quotation(
    quotation_id: UUID,
    body: ValidateRequest,
    principal: Principal = _principal,
    service: QuotationService = _service,
) -> QuotationResponse:
    """Approvers only: the files are released and the quotation is ``pendiente_carga``. For
    ``creacion`` the code of the new list is required (1 to 3 letters or digits, free in
    SIESA); ``new_list_name`` may change its description."""

    return await _respond(
        principal,
        service,
        await service.validate(
            principal,
            quotation_id,
            body.version,
            price_list_id=body.price_list_id,
            new_list_name=body.new_list_name,
        ),
    )


@router.post(
    "/{quotation_id}/uploaded",
    dependencies=[_READ_GATE],
    response_model=QuotationResponse,
    summary="Mark loaded in SIESA",
)
@inject
async def mark_uploaded(
    quotation_id: UUID,
    body: VersionRequest,
    principal: Principal = _principal,
    service: QuotationService = _service,
) -> QuotationResponse:
    """Approvers only, from ``pendiente_carga``: the quotation is ``cargada`` and records who
    and when (``uploaded_by``, ``uploaded_at``)."""

    return await _respond(
        principal, service, await service.mark_uploaded(principal, quotation_id, body.version)
    )


class CenterComparisonResponse(BaseModel):
    """The costliest of the compared centers, or ``null`` when they cost the same."""

    highest: str | None


@router.get(
    "/{quotation_id}/cost-centers/compare",
    dependencies=[_READ_GATE],
    response_model=CenterComparisonResponse,
    summary="Which operation center costs the most",
)
@inject
async def compare_cost_centers(
    quotation_id: UUID,
    codes: list[str] = Query(min_length=2, max_length=30),  # noqa: B008
    warehouses: list[str] = Query(default=[], max_length=100),  # noqa: B008
    groups: list[str] = Query(default=[], max_length=90),  # noqa: B008
    principal: Principal = _principal,
    service: QuotationService = _service,
) -> CenterComparisonResponse:
    """For the quotation's SIESA references, the center with the highest sum of unit costs,
    before choosing them, each center costed from its chosen warehouse ``groups``
    (``001:BP``, ``001:PROMO``) if any. Only the code: no amounts. 422 with fewer than two
    centers, an unknown one or a group outside them; ``null`` when they tie or the list
    has its own warehouses and none was chosen."""

    pairs = []
    for item in groups:
        center, _, kind = item.partition(":")
        if not center.strip() or not kind.strip():
            raise InvalidInputError(f"Grupo de bodegas inválido: {item} (usa C.O.:grupo, 001:BP)")
        pairs.append((center, kind))
    return CenterComparisonResponse(
        highest=await service.compare_centers(principal, quotation_id, codes, warehouses, pairs)
    )


@router.patch(
    "/{quotation_id}/billing-center",
    dependencies=[_READ_GATE],
    response_model=QuotationResponse,
    summary="Cost from Nacional or other operation centers",
)
@inject
async def set_billing_center(
    quotation_id: UUID,
    body: BillingCenterRequest,
    principal: Principal = _principal,
    service: QuotationService = _service,
) -> QuotationResponse:
    """Nacional (``national``) takes, SKU by SKU, the highest cost of every warehouse; one
    center its warehouses; several the costliest of them per SKU (``cost_center`` on each
    line). Inside a center, ``cost_warehouse_groups`` narrows it to its main or
    spot-request warehouses, by whole groups: the highest cost with stock among them (frozen
    and refrigerated lines read the cold room when it was chosen), and their stock; the
    promotional group adds on top. Every line is priced again (stock plans, regional, liquor
    tax, target, margins), keeping new prices, quantities and local products; back to
    draft. Same rules as ``/refresh``. 422 on an unknown center, on Nacional with centers or
    warehouses, on a group the center does not have, or on warehouses that leave a group
    half chosen."""

    return await _respond(
        principal,
        service,
        await service.set_billing_center(
            principal,
            quotation_id,
            body.version,
            body.codes,
            body.cost_warehouses,
            body.groups,
        ),
    )


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
    "/{quotation_id}/lines.xlsx",
    dependencies=[_READ_GATE],
    response_class=Response,
    responses={
        200: {"content": {"application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": {}}}
    },
    summary="Download BI's template of lines and costs",
)
@inject
async def lines_workbook(
    quotation_id: UUID,
    principal: Principal = _principal,
    service: QuotationService = _service,
) -> Response:
    """Every line with the table's data and its costs (supplier's quotation, inventory, the
    center that set it), in any status. Validators only (403 otherwise): it carries costs."""

    result = await service.lines_file(principal, quotation_id)
    return _file(result.content, result.filename, result.media_type)


@router.get(
    "/{quotation_id}/list-template.xlsx",
    dependencies=[_READ_GATE],
    response_class=Response,
    responses={
        200: {"content": {"application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": {}}}
    },
    summary="Download the list update template",
)
@inject
async def list_template(
    quotation_id: UUID,
    principal: Principal = _principal,
    service: QuotationService = _service,
) -> Response:
    """A list update's lines with what the advisors see and no costs (sheets ``Lineas`` and
    ``Cotizacion``), with a filter on the header. Whoever reads the update; 422 on another
    kind. Delete the rows of the references to leave out and upload it back."""

    result = await service.list_template_file(principal, quotation_id)
    return _file(result.content, result.filename, result.media_type)


class RemovedReferenceResponse(BaseModel):
    sku: str
    description: str | None


class TemplateUploadResponse(BaseModel):
    """What the uploaded template leaves out of the list update.

    ``removed``: the update's references no longer in the file. ``unknown``: the file's
    references the update does not have (ignored). ``applied`` false is the preview:
    nothing changed and ``quotation`` is ``null``; true, the lines are gone and
    ``quotation`` is the update as it stands."""

    removed: list[RemovedReferenceResponse]
    removed_count: int
    unknown: list[str]
    unknown_count: int
    kept: int
    applied: bool
    quotation: QuotationResponse | None = None


@router.post(
    "/{quotation_id}/list-template",
    dependencies=[_READ_GATE],
    response_model=TemplateUploadResponse,
    summary="Upload the list update template without the references to leave out",
    description=(
        "multipart/form-data with `file` (the template, .xlsx), `version` and `apply` "
        "(false: only say what would be removed; true: remove it)."
    ),
)
@inject
async def upload_list_template(
    quotation_id: UUID,
    file: UploadFile = File(...),  # noqa: B008
    version: int = Form(ge=1),  # noqa: B008
    apply: bool = Form(default=False),  # noqa: B008
    principal: Principal = _principal,
    service: QuotationService = _service,
) -> TemplateUploadResponse:
    """Only the ``Referencia`` column counts: every reference of the update missing from the
    file leaves it (prices and other columns are ignored). Removed references stay out after
    a refresh. Same editing rules as the lines: 409 on a stale version or a closed update.
    422 on another kind, a file of another list, without references, too large or not the
    template, one that leaves nothing, or ``apply`` with nothing to remove."""

    # One byte over the limit is enough for the service to reject the file.
    content = await file.read(service.template_max_bytes + 1)
    detail, removal = await service.upload_list_template(
        principal, quotation_id, version, content, apply
    )
    return TemplateUploadResponse(
        removed=[
            RemovedReferenceResponse(sku=sku, description=description)
            for sku, description in removal.removed
        ],
        removed_count=len(removal.removed),
        unknown=list(removal.unknown),
        unknown_count=len(removal.unknown),
        kept=removal.kept,
        applied=removal.applied,
        quotation=await _respond(principal, service, detail) if removal.applied else None,
    )


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
