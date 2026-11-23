"""Use cases of the internal support requests (see ``domain/internal_requests.py``).

- **Asks**: whoever uses the quoter and does not review (BI reviews; it does not ask itself).
- **Decides**: whoever manages ``apoyos`` in BI, or holds ``manage`` on the permission
  ``aprobacion-apoyos-internos`` in any area (designated reviewers).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import replace
from datetime import UTC, date, datetime
from decimal import Decimal
from uuid import UUID

from app.core.errors import ConflictError, ForbiddenError, InvalidInputError, NotFoundError
from app.modules.commercial_masters.domain.entities import (
    TargetMargin,
    clean_nit,
    nit_check_digit,
)
from app.modules.commercial_masters.domain.margins import MARGIN_LABELS
from app.modules.identity.domain.principal import Principal
from app.modules.supplier_supports.domain.entities import SupportData
from app.modules.supplier_supports.domain.internal_requests import (
    FOR_GOOD,
    JDH_KIND,
    KINDS,
    MAX_ITEMS,
    ONCE,
    PENDING,
    SUPPLIER_KIND,
    Approval,
    InternalRequest,
    InternalRequestRepository,
    NewRequest,
    QuotationAccess,
    QuotedLine,
    RequestItem,
    RequestRef,
)
from app.modules.supplier_supports.domain.ports import (
    Calendar,
    ManagerPolicy,
    MasterLookup,
    SupportRepository,
)
from app.modules.supplier_supports.domain.pricing import GUARANTEED_METHOD
from app.modules.supplier_supports.domain.vocabulary import INTERNAL_STATUSES

QUOTATION_KIND = "cotizacion"
MAX_PAGE_SIZE = 100
_TEXT = {"agreed_with": 300, "reason": 1000, "note": 1000}


def _text(value: str | None, name: str, label: str, *, required: bool = True) -> str | None:
    text = " ".join(value.split()) if value else ""
    if not text:
        if required:
            raise InvalidInputError(f"Escribe {label}")
        return None
    if len(text) > _TEXT[name]:
        raise InvalidInputError(f"{label.capitalize()} supera {_TEXT[name]} caracteres")
    return text


def _margin(value: Decimal, sku: str) -> Decimal:
    if not Decimal(0) <= value < Decimal(1):
        raise InvalidInputError(
            f"El margen de {sku} debe ser una fracción entre 0 y 1 (0.16 = 16 %)"
        )
    return value.quantize(Decimal("0.000001"))


def _pct(value: Decimal) -> str:
    return f"{value * 100:.2f}".replace(".", ",") + " %"


def _approved_supplier_margin(sku: str, value: Decimal, item: RequestItem) -> Decimal:
    """The supplier minimum a reviewer approves: from -100 % and under the one in force when
    it was asked.

    Raises:
        InvalidInputError: Out of that range.
    """

    current = item.target_margin_pct
    if not Decimal(-1) < value < Decimal(1) or (current is not None and value >= current):
        limit = _pct(current) if current is not None else "100 %"
        raise InvalidInputError(
            f"El margen aprobado de {sku} debe estar entre -100 % y el mínimo del proveedor "
            f"({limit})"
        )
    return value.quantize(Decimal("0.000001"))


def _below_supplier_minimum(sku: str, value: Decimal, line: QuotedLine) -> tuple[Decimal, Decimal]:
    """The margin asked in a supplier request, and the supplier's minimum it must be under.

    Raises:
        InvalidInputError: The line is not covered by a % Margen Garantizado with the
            supplier's minimum, or the margin is not between -100 % and that minimum.
    """

    current = line.supplier_min_margin_pct
    if line.support_calc_method != GUARANTEED_METHOD or current is None or not line.support_id:
        raise InvalidInputError(
            f"La referencia {sku} no tiene un % Margen Garantizado con margen mínimo del "
            "proveedor: pide un margen autorizado JDH"
        )
    if not Decimal(-1) < value < Decimal(1):
        raise InvalidInputError(
            f"El margen de {sku} debe ser una fracción entre -1 y 1 (0.07 = 7 %, -0.05 = -5 %)"
        )
    asked = value.quantize(Decimal("0.000001"))
    if asked >= current:
        raise InvalidInputError(
            f"El margen pedido para {sku} ({_pct(asked)}) debe ser menor que el margen mínimo "
            f"del proveedor ({_pct(current)})"
        )
    return asked, current


def _below_target(sku: str, asked: Decimal, target: TargetMargin | None) -> Decimal:
    """The product's optimal margin on the customer's list, which ``asked`` must be under.

    Raises:
        InvalidInputError: The SKU is unknown, has no margin on that list, or the margin asked
            is not lower (BI, 2026-11-18: only a lower margin is requested).
    """

    if target is None:
        raise InvalidInputError(f"Referencia JDH desconocida: {sku}")
    label = MARGIN_LABELS.get(target.margin_field, target.margin_field)
    if target.margin_pct is None:
        raise InvalidInputError(
            f"La referencia {sku} no tiene {label} en el maestro de márgenes: no hay un margen "
            "actual para pedir uno menor"
        )
    if asked >= target.margin_pct:
        raise InvalidInputError(
            f"El margen pedido para {sku} ({_pct(asked)}) debe ser menor que su margen actual "
            f"({_pct(target.margin_pct)}, {label})"
        )
    return target.margin_pct


class InternalRequestLinks:
    """Who reviews, and the latest request of each SKU of a quotation.

    The quoter uses it for its permissions and lines; it does not depend on the quoter.
    """

    def __init__(
        self,
        repository: InternalRequestRepository,
        managers: ManagerPolicy,
        designated: ManagerPolicy,
    ) -> None:
        """Create it.

        Args:
            repository (InternalRequestRepository): The requests.
            managers (ManagerPolicy): Supports managers (``apoyos`` in BI).
            designated (ManagerPolicy): ``manage`` on ``aprobacion-apoyos-internos``.
        """

        self.repository = repository
        self.managers = managers
        self.designated = designated

    async def is_reviewer(self, principal: Principal) -> bool:
        """Whether the caller approves or rejects internal requests."""

        if principal.is_platform_admin:
            return True
        return await self.managers.is_manager(principal) or await self.designated.is_manager(
            principal
        )

    async def latest_for(self, quotation_id: UUID) -> dict[str, RequestRef]:
        """The latest request of each SKU of a quotation."""

        return await self.repository.latest_for_quotation(quotation_id)


class InternalRequestService:
    """Ask, list, approve and reject internal support requests."""

    def __init__(
        self,
        repository: InternalRequestRepository,
        links: InternalRequestLinks,
        quotations: QuotationAccess,
        masters: MasterLookup,
        calendar: Calendar,
        requesters: ManagerPolicy | None = None,
        supports: SupportRepository | None = None,
    ) -> None:
        """Create the service.

        Args:
            repository (InternalRequestRepository): The requests.
            links (InternalRequestLinks): Who reviews.
            quotations (QuotationAccess): The quoter (the quotation, read with the caller's
                permission; whether the caller quotes).
            masters (MasterLookup): Suppliers, products, customers and margins of the master.
            calendar (Calendar): Bogotá calendar (the default order date).
            requesters (ManagerPolicy | None): Who asks JDH supports from the requests panel
                (``solicitudes-apoyos``); they may ask by customer and SKU too.
            supports (SupportRepository | None): The master: a supplier margin request copies
                the guaranteed support it lowers.
        """

        self.repository = repository
        self.links = links
        self.quotations = quotations
        self.masters = masters
        self.calendar = calendar
        self.requesters = requesters
        self.supports = supports

    # ------------------------------------------------------------ access

    async def can_request(self, principal: Principal) -> bool:
        """Uses the quoter. An approver asks too (2026-11-19: the form shows for everyone who
        quotes); they never approve their own request (:meth:`approve`)."""

        return await self.quotations.can_quote(principal)

    async def permissions(self, principal: Principal) -> dict[str, bool]:
        """``can_review_internal``, and ``can_request_internal``: asks from the requests panel and
        sees "my requests". Same rule as ``create_direct``, so Comercial's requesters of
        ``solicitudes-apoyos`` get the button even when they do not use the quoter."""

        return {
            "can_review_internal": await self.links.is_reviewer(principal),
            "can_request_internal": await self.can_request_direct(principal),
        }

    async def _require_reviewer(self, principal: Principal) -> None:
        if not await self.links.is_reviewer(principal):
            raise ForbiddenError("No tienes permiso para decidir solicitudes de apoyo interno")

    # ------------------------------------------------------------ asking

    async def create(
        self,
        principal: Principal,
        quotation_id: UUID,
        *,
        version: int,
        supplier_code: str,
        requested_for: date | None,
        agreed_with: str,
        reason: str,
        items: Sequence[tuple[str, Decimal]],
        kind: str = JDH_KIND,
    ) -> InternalRequest:
        """Ask a supported margin for SKUs of one supplier of a customer quotation.

        ``kind``: ``jdh`` (an authorized margin, under the product's optimal margin) or
        ``proveedor`` (2026-11-23): each line is covered by a % Margen Garantizado with the
        supplier's minimum margin, and the margin asked is a lower minimum for this customer
        (it may be 0 or negative).

        Raises:
            ForbiddenError: The caller does not quote, or reviews.
            NotFoundError: The quotation does not exist (or is not visible).
            ConflictError: The quotation changed (``version``), or a SKU is already pending.
            InvalidInputError: A rule fails (kind, customer, supplier, SKUs, margins, texts).
        """

        if not await self.can_request(principal):
            raise ForbiddenError(
                "Solo quien cotiza puede pedir apoyos internos desde la cotización"
            )
        quotation = await self.quotations.quotation(principal, quotation_id)
        if quotation.kind != QUOTATION_KIND:
            raise InvalidInputError(
                "Los apoyos internos solo se piden desde una cotización de cliente"
            )
        if quotation.version != version:
            raise ConflictError("La cotización cambió desde que la abriste; recárgala")
        nit = clean_nit(quotation.customer_nit)
        if not nit:
            raise InvalidInputError("La cotización no tiene un cliente con NIT de SIESA")
        code = (supplier_code or "").strip()
        supplier_id = (await self.masters.commercial_suppliers_by_code([code])).get(code)
        if supplier_id is None:
            raise InvalidInputError(f"Proveedor comercial desconocido: {code}")
        if kind not in KINDS:
            raise InvalidInputError(f"kind debe ser uno de: {', '.join(KINDS)}")
        if not 1 <= len(items) <= MAX_ITEMS:
            raise InvalidInputError(f"Pide entre 1 y {MAX_ITEMS} referencias")
        skus = [sku.strip() for sku, _ in items]
        if len(set(skus)) != len(skus):
            raise InvalidInputError("Una referencia está repetida en la solicitud")
        request_items: list[RequestItem] = []
        supplier_name: str | None = None
        targets = await self.masters.target_margins(nit, skus) if kind == JDH_KIND else {}
        for sku, (_, margin) in zip(skus, items, strict=True):
            line = quotation.lines.get(sku)
            if line is None or line.is_local:
                raise InvalidInputError(f"La referencia {sku} no está en la cotización")
            if (line.supplier_code or "").strip() != code:
                raise InvalidInputError(
                    f"La referencia {sku} es de otro proveedor: todas deben ser de {code}"
                )
            supplier_name = supplier_name or line.supplier_name
            if kind == SUPPLIER_KIND:
                asked, current = _below_supplier_minimum(sku, margin, line)
                base = line.support_id
            else:
                asked = _margin(margin, sku)
                current, base = _below_target(sku, asked, targets.get(sku)), None
            request_items.append(
                RequestItem(
                    sku=sku,
                    requested_margin_pct=asked,
                    description=line.description,
                    unit=line.unit,
                    cost=line.cost,
                    quoted_price=line.quoted_price,
                    margin_pct=line.margin_pct,
                    min_margin_pct=line.min_margin_pct,
                    target_margin_pct=current,
                    base_support_id=base,
                )
            )
        new = NewRequest(
            quotation_id=quotation.id,
            quotation_label=quotation.label,
            customer_nit=nit,
            customer_name=quotation.customer_name,
            commercial_supplier_id=supplier_id,
            supplier_code=code,
            supplier_name=supplier_name,
            requested_for=requested_for or self.calendar.today(),
            agreed_with=_text(agreed_with, "agreed_with", "con quién se acordó") or "",
            reason=_text(reason, "reason", "el motivo") or "",
            items=tuple(request_items),
            kind=kind,
        )
        return await self.repository.create(
            new, principal.user_id, principal.email, datetime.now(UTC)
        )

    async def can_request_direct(self, principal: Principal) -> bool:
        """Asks from the requests panel: quotes or asks JDH supports (an approver too; they
        never approve their own request)."""

        if await self.quotations.can_quote(principal):
            return True
        return self.requesters is not None and await self.requesters.is_manager(principal)

    async def _customer(self, raw_nit: str) -> tuple[str, str | None]:
        """The customer's NIT as the master writes it (with or without its check digit)."""

        nit = clean_nit(raw_nit)
        if not nit:
            raise InvalidInputError("Escribe el NIT del cliente")
        candidates = [nit]
        if len(nit) > 5 and nit_check_digit(nit[:-1]) == nit[-1]:
            candidates.append(nit[:-1])
        found = await self.masters.customers(candidates)
        for candidate in candidates:
            if candidate in found:
                return candidate, found[candidate].name or None
        raise InvalidInputError(f"NIT de cliente desconocido: {raw_nit}")

    async def targets(
        self, principal: Principal, customer_nit: str, skus: Sequence[str]
    ) -> tuple[str, str | None, list[TargetMargin]]:
        """The current margin of each SKU for the customer (the product's optimal margin on
        the customer's list), to ask a lower one. Unknown SKUs are left out.

        Raises:
            ForbiddenError: The caller neither asks nor reviews.
            InvalidInputError: Unknown customer, or no SKU.
        """

        if not (
            await self.can_request_direct(principal) or await self.links.is_reviewer(principal)
        ):
            raise ForbiddenError("No tienes permiso para pedir márgenes autorizados")
        nit, name = await self._customer(customer_nit)
        wanted = list(dict.fromkeys(sku.strip() for sku in skus if sku and sku.strip()))
        if not 1 <= len(wanted) <= MAX_ITEMS:
            raise InvalidInputError(f"Consulta entre 1 y {MAX_ITEMS} referencias")
        found = await self.masters.target_margins(nit, wanted)
        return nit, name, [found[sku] for sku in wanted if sku in found]

    async def create_direct(
        self,
        principal: Principal,
        *,
        customer_nit: str,
        requested_for: date | None,
        agreed_with: str,
        reason: str,
        items: Sequence[tuple[str, Decimal]],
    ) -> InternalRequest:
        """Ask an authorized margin from the requests panel, by customer and SKU (BI,
        2026-11-18): no quotation; every SKU of one supplier; each margin lower than the
        product's current one for that customer. Approving creates the same margin exception
        as a request from a quotation.

        Raises:
            ForbiddenError: The caller neither quotes nor asks JDH supports, or reviews.
            ConflictError: A SKU is already pending for that customer.
            InvalidInputError: A rule fails (customer, SKUs, supplier, margins, texts).
        """

        if not await self.can_request_direct(principal):
            raise ForbiddenError("No tienes permiso para pedir márgenes autorizados")
        nit, customer_name = await self._customer(customer_nit)
        if not 1 <= len(items) <= MAX_ITEMS:
            raise InvalidInputError(f"Pide entre 1 y {MAX_ITEMS} referencias")
        skus = [sku.strip() for sku, _ in items]
        if len(set(skus)) != len(skus):
            raise InvalidInputError("Una referencia está repetida en la solicitud")
        targets = await self.masters.target_margins(nit, skus)
        codes = {(targets[sku].commercial_supplier_code or "") for sku in skus if sku in targets}
        request_items: list[RequestItem] = []
        for sku, (_, margin) in zip(skus, items, strict=True):
            asked = _margin(margin, sku)
            target = targets.get(sku)
            current = _below_target(sku, asked, target)
            request_items.append(
                RequestItem(
                    sku=sku,
                    requested_margin_pct=asked,
                    description=target.description if target else None,
                    unit=target.unit if target else None,
                    target_margin_pct=current,
                )
            )
        if len(codes) != 1 or not next(iter(codes)):
            raise InvalidInputError(
                "Las referencias de una solicitud deben ser de un mismo proveedor comercial"
            )
        code = next(iter(codes)).strip()
        supplier_id = (await self.masters.commercial_suppliers_by_code([code])).get(code)
        if supplier_id is None:
            raise InvalidInputError(f"Proveedor comercial desconocido: {code}")
        names = await self.masters.commercial_supplier_names([supplier_id])
        new = NewRequest(
            quotation_id=None,
            quotation_label=None,
            customer_nit=nit,
            customer_name=customer_name,
            commercial_supplier_id=supplier_id,
            supplier_code=code,
            supplier_name=names.get(supplier_id, (code, None))[1],
            requested_for=requested_for or self.calendar.today(),
            agreed_with=_text(agreed_with, "agreed_with", "con quién se acordó") or "",
            reason=_text(reason, "reason", "el motivo") or "",
            items=tuple(request_items),
        )
        return await self.repository.create(
            new, principal.user_id, principal.email, datetime.now(UTC)
        )

    # ------------------------------------------------------------ reading

    async def search(
        self,
        principal: Principal,
        *,
        mine: bool,
        status: str | None = None,
        q: str | None = None,
        page: int = 1,
        size: int = 50,
    ) -> tuple[list[InternalRequest], int]:
        """``mine``: the caller's own requests; otherwise every request (reviewers)."""

        if not mine:
            await self._require_reviewer(principal)
        if status is not None and status not in INTERNAL_STATUSES:
            raise InvalidInputError(f"status debe ser uno de: {', '.join(INTERNAL_STATUSES)}")
        if page < 1 or not 1 <= size <= MAX_PAGE_SIZE:
            raise InvalidInputError(f"Página desde 1 y tamaño entre 1 y {MAX_PAGE_SIZE}")
        return await self.repository.search(
            status=status,
            q=" ".join(q.split()) if q else None,
            owner_id=principal.user_id if mine else None,
            page=page,
            size=size,
        )

    async def get(self, principal: Principal, request_id: UUID) -> InternalRequest:
        """One request: its author or a reviewer."""

        request = await self.repository.get(request_id)
        if request is None:
            raise NotFoundError("Solicitud no encontrada")
        if request.requested_by_id != principal.user_id and not await self.links.is_reviewer(
            principal
        ):
            raise NotFoundError("Solicitud no encontrada")
        return request

    # ------------------------------------------------------------ deciding

    async def approve(
        self,
        principal: Principal,
        request_id: UUID,
        *,
        version: int,
        decision: str,
        valid_from: date | None,
        valid_to: date | None,
        note: str | None,
        items: Sequence[tuple[str, Decimal]],
    ) -> InternalRequest:
        """Approve for good or once; SKUs left out are not approved (at least one).

        Each approved SKU becomes a support ``excepcion_margen`` / ``margen_minimo`` with the
        approved margin, for the customer and supplier of the request, published to the DWH.
        """

        await self._require_reviewer(principal)
        request = await self.repository.get(request_id)
        if request is None:
            raise NotFoundError("Solicitud no encontrada")
        if request.requested_by_id == principal.user_id:
            # An approver may ask too (2026-11-19), but never decides their own request.
            raise ForbiddenError("No puedes aprobar tu propia solicitud: la decide otro aprobador")
        if request.status != PENDING:
            raise ConflictError("La solicitud ya fue resuelta")
        if decision not in (FOR_GOOD, ONCE):
            raise InvalidInputError("decision debe ser persistente o unica_vez")
        if not items:
            raise InvalidInputError("Aprueba al menos una referencia (o rechaza la solicitud)")
        asked = {item.sku: item for item in request.items}
        margins: dict[str, Decimal] = {}
        for sku, margin in items:
            key = sku.strip()
            if key not in asked:
                raise InvalidInputError(f"La referencia {key} no está en la solicitud")
            if key in margins:
                raise InvalidInputError(f"La referencia {key} está repetida")
            if request.kind == SUPPLIER_KIND:
                margins[key] = _approved_supplier_margin(key, margin, asked[key])
            else:
                margins[key] = _margin(margin, key)
        text = _text(note, "note", "la nota", required=decision == ONCE)
        if decision == ONCE:
            start, end = request.requested_for, request.requested_for
        else:
            start = valid_from or request.requested_for
            end = valid_to
            if end is not None and end < start:
                raise InvalidInputError("La vigencia final no puede ser anterior a la inicial")
        if request.kind == SUPPLIER_KIND:
            supplier_supports = await self._supplier_copies(
                request, asked, margins, decision, start, end, text, principal
            )
            return await self.repository.approve(
                request_id,
                version,
                Approval(decision, start, end, text, margins, supplier_supports),
                principal.email,
                datetime.now(UTC),
            )
        products = await self.masters.products_by_sku(sorted(margins))
        supports: dict[str, SupportData] = {}
        for sku, margin in margins.items():
            product = products.get(sku)
            once = f"Única vez: pedido del {request.requested_for.isoformat()}"
            supports[sku] = SupportData(
                kind="excepcion_margen",
                calc_method="margen_minimo",
                commercial_supplier_id=request.commercial_supplier_id,
                funding="jdh",
                program=f"Solicitud de apoyo interno #{request.number}",
                customer_nit=request.customer_nit,
                customer_name=request.customer_name,
                product_id=product.id if product else None,
                product_sku=sku,
                brand=product.brand if product else None,
                min_margin_pct=margin,
                supported_margin_pct=margin,
                valid_from=start,
                valid_to=end,
                authorized_by=principal.email[:200],
                conditions=once if decision == ONCE else None,
                notes=text,
                source=f"solicitud interna #{request.number}",
            )
        return await self.repository.approve(
            request_id,
            version,
            Approval(decision, start, end, text, margins, supports),
            principal.email,
            datetime.now(UTC),
        )

    async def _supplier_copies(
        self,
        request: InternalRequest,
        asked: dict[str, RequestItem],
        margins: dict[str, Decimal],
        decision: str,
        start: date,
        end: date | None,
        note: str | None,
        principal: Principal,
    ) -> dict[str, SupportData]:
        """The supports a supplier margin request creates: per SKU, the guaranteed support it
        lowers, for the request's customer, with the approved supplier minimum and the
        decided validity. The base support keeps its minimum for everyone else.

        Raises:
            ConflictError: The base support is gone, voided or no longer a guarantee.
        """

        if self.supports is None:
            raise ConflictError("No se puede leer el maestro de apoyos para aprobar")
        once = f"Única vez: pedido del {request.requested_for.isoformat()}"
        copies: dict[str, SupportData] = {}
        for sku, margin in margins.items():
            base_id = asked[sku].base_support_id
            base = await self.supports.get(base_id) if base_id else None
            if (
                base is None
                or base.data.status == "anulado"
                or base.data.calc_method != GUARANTEED_METHOD
            ):
                raise ConflictError(
                    f"El % Margen Garantizado de {sku} ya no existe o cambió: rechaza la "
                    "solicitud y pídela de nuevo"
                )
            copies[sku] = replace(
                base.data,
                supplier_min_margin_pct=margin,
                customer_nit=request.customer_nit,
                customer_name=request.customer_name,
                customer_branch_id=None,
                customer_group=None,
                group_wide=False,
                product_sku=sku,
                program=f"Solicitud de margen proveedor #{request.number}",
                valid_from=start,
                valid_to=end,
                authorized_by=principal.email[:200],
                conditions=once if decision == ONCE else None,
                notes=note,
                source=f"solicitud margen proveedor #{request.number}",
                source_key=None,
                external_ref=None,
                status="activo",
                is_enabled=True,
                issues=(),
            )
        return copies

    async def reject(
        self, principal: Principal, request_id: UUID, *, version: int, reason: str
    ) -> InternalRequest:
        """Reject with a reason."""

        await self._require_reviewer(principal)
        text = _text(reason, "reason", "el motivo del rechazo") or ""
        return await self.repository.reject(
            request_id, version, text, principal.email, datetime.now(UTC)
        )


__all__ = ["InternalRequestLinks", "InternalRequestService"]
