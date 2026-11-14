"""Use cases of the internal support requests (see ``domain/internal_requests.py``).

- **Asks**: whoever uses the quoter and does not review (BI reviews; it does not ask itself).
- **Decides**: whoever manages ``apoyos`` in BI, or holds ``manage`` on the permission
  ``aprobacion-apoyos-internos`` in any area (designated reviewers).
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, date, datetime
from decimal import Decimal
from uuid import UUID

from app.core.errors import ConflictError, ForbiddenError, InvalidInputError, NotFoundError
from app.modules.commercial_masters.domain.entities import clean_nit
from app.modules.identity.domain.principal import Principal
from app.modules.supplier_supports.domain.entities import SupportData
from app.modules.supplier_supports.domain.internal_requests import (
    FOR_GOOD,
    MAX_ITEMS,
    ONCE,
    PENDING,
    Approval,
    InternalRequest,
    InternalRequestRepository,
    NewRequest,
    QuotationAccess,
    RequestItem,
    RequestRef,
)
from app.modules.supplier_supports.domain.ports import Calendar, ManagerPolicy, MasterLookup
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
    ) -> None:
        """Create the service.

        Args:
            repository (InternalRequestRepository): The requests.
            links (InternalRequestLinks): Who reviews.
            quotations (QuotationAccess): The quoter (the quotation, read with the caller's
                permission; whether the caller quotes).
            masters (MasterLookup): Suppliers and products of the master.
            calendar (Calendar): Bogotá calendar (the default order date).
        """

        self.repository = repository
        self.links = links
        self.quotations = quotations
        self.masters = masters
        self.calendar = calendar

    # ------------------------------------------------------------ access

    async def can_request(self, principal: Principal) -> bool:
        """Uses the quoter and does not review."""

        return await self.quotations.can_quote(principal) and not await self.links.is_reviewer(
            principal
        )

    async def permissions(self, principal: Principal) -> dict[str, bool]:
        """``can_review_internal`` and ``can_request_internal`` (for "my requests")."""

        reviewer = await self.links.is_reviewer(principal)
        return {
            "can_review_internal": reviewer,
            "can_request_internal": not reviewer and await self.quotations.can_quote(principal),
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
    ) -> InternalRequest:
        """Ask a supported margin for SKUs of one supplier of a customer quotation.

        Raises:
            ForbiddenError: The caller does not quote, or reviews.
            NotFoundError: The quotation does not exist (or is not visible).
            ConflictError: The quotation changed (``version``), or a SKU is already pending.
            InvalidInputError: A rule fails (kind, customer, supplier, SKUs, margins, texts).
        """

        if not await self.can_request(principal):
            raise ForbiddenError(
                "Solo quien cotiza (y no revisa) puede pedir apoyos internos desde la cotización"
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
        if not 1 <= len(items) <= MAX_ITEMS:
            raise InvalidInputError(f"Pide entre 1 y {MAX_ITEMS} referencias")
        skus = [sku.strip() for sku, _ in items]
        if len(set(skus)) != len(skus):
            raise InvalidInputError("Una referencia está repetida en la solicitud")
        request_items: list[RequestItem] = []
        supplier_name: str | None = None
        for sku, (_, margin) in zip(skus, items, strict=True):
            line = quotation.lines.get(sku)
            if line is None or line.is_local:
                raise InvalidInputError(f"La referencia {sku} no está en la cotización")
            if (line.supplier_code or "").strip() != code:
                raise InvalidInputError(
                    f"La referencia {sku} es de otro proveedor: todas deben ser de {code}"
                )
            supplier_name = supplier_name or line.supplier_name
            request_items.append(
                RequestItem(
                    sku=sku,
                    requested_margin_pct=_margin(margin, sku),
                    description=line.description,
                    unit=line.unit,
                    cost=line.cost,
                    quoted_price=line.quoted_price,
                    margin_pct=line.margin_pct,
                    min_margin_pct=line.min_margin_pct,
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
            margins[key] = _margin(margin, key)
        text = _text(note, "note", "la nota", required=decision == ONCE)
        if decision == ONCE:
            start, end = request.requested_for, request.requested_for
        else:
            start = valid_from or request.requested_for
            end = valid_to
            if end is not None and end < start:
                raise InvalidInputError("La vigencia final no puede ser anterior a la inicial")
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
