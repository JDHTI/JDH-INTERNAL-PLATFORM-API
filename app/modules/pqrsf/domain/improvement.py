"""Improvement opportunity (OM, format P.06.F.04): what Calidad sends a supplier about a case.

The OM is a Word document filled from the case with its own consecutive (``1181BOG``: a
number plus the site's three letters). The template lives in the bucket, versioned, so
Calidad can replace the format without a deploy.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date, datetime
from uuid import UUID
from zoneinfo import ZoneInfo

from app.core.errors import InvalidInputError
from app.modules.pqrsf.domain.tracking import CaseProduct, TrackingCase

TEMPLATE_KIND_OM = "om"
TEMPLATE_KINDS = frozenset({TEMPLATE_KIND_OM})

OM_DRAFT = "draft"
OM_SENT = "sent"

DEFAULT_REQUEST_NOTE = (
    "Es conveniente enviar una respuesta formal con plan de acción para dar cierre, enviar "
    "copia al cliente final"
)
# «Descripción medio recibo»: how the request reached JDH.
CHANNELS = {
    "public_form": "Formulario web PQRSF",
    "email": "Correo electrónico",
    "internal": "Registro interno",
    "import": "Matriz P.13.F.01",
}
# Every mark the template must use; checked when a new template version is uploaded.
TEMPLATE_FIELDS = frozenset(
    {
        "code",
        "city",
        "address",
        "report_date",
        "channel",
        "pickup_order",
        "document_number",
        "document_date",
        "classification",
        "novelty",
        "dependency",
        "requester",
        "sales_executive",
        "immediate_actions",
        "customer",
        "supplier",
        "cause",
        "description",
        "products",
        "request_note",
        "photos",
        "signer_name",
        "signer_position",
        "signer_email",
        "signer_phone",
    }
)
TEMPLATE_MAX_BYTES = 5 * 1024 * 1024
# The photos printed on the OM, at most; the rest stay as attachments of the message.
MAX_PHOTOS = 6
_LOCAL = ZoneInfo("America/Bogota")


@dataclass
class DocumentTemplate:
    """A Word template version kept in the bucket; one active per kind."""

    id: UUID
    kind: str
    version_label: str
    filename: str
    storage_key: str
    size_bytes: int
    checksum_sha256: str
    is_active: bool
    uploaded_by: UUID | None
    created_at: datetime | None = None


@dataclass(frozen=True)
class Signer:
    """Who signs the OM: whoever generates it, as the directory knows them."""

    user_id: UUID | None
    name: str | None
    position: str | None
    email: str | None
    phone: str | None


@dataclass
class ImprovementOpportunity:
    """An OM of a case for one supplier, with its Word and PDF in the bucket."""

    id: UUID
    case_id: UUID
    number: int
    code: str
    supplier_code: str | None
    supplier_name: str | None
    immediate_actions: str | None = None
    request_note: str | None = DEFAULT_REQUEST_NOTE
    document_number: str | None = None
    document_date: date | None = None
    evidence_ids: list[UUID] = field(default_factory=list)
    signer: Signer | None = None
    template_id: UUID | None = None
    docx_key: str | None = None
    pdf_key: str | None = None
    edited: bool = False
    status: str = OM_DRAFT
    generated_at: datetime | None = None
    sent_at: datetime | None = None
    created_by: UUID | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None

    @property
    def is_sent(self) -> bool:
        """Whether the OM already went to the supplier."""

        return self.status == OM_SENT

    def filename(self, extension: str) -> str:
        """``OM 1181BOG.pdf``."""

        return f"OM {self.code}.{extension}"


def om_code(number: int, site_code: str) -> str:
    """The OM consecutive: the number and the site's three letters (``1181BOG``)."""

    return f"{number}{site_code}"


def template_key(kind: str, template_id: UUID) -> str:
    """Private object key of a template version."""

    return f"pqrsf/templates/{kind}/{template_id}.docx"


def om_document_key(case_id: UUID, om_id: UUID, extension: str) -> str:
    """Private object key of an OM document, under its case."""

    return f"pqrsf/cases/{case_id}/om/{om_id}.{extension}"


def clean_template_kind(kind: str) -> str:
    """Validate the kind of a template."""

    if kind not in TEMPLATE_KINDS:
        raise InvalidInputError("Tipo de plantilla no soportado")
    return kind


def supplier_products(case: TrackingCase, supplier_code: str | None) -> list[CaseProduct]:
    """The case products of one supplier (all of them when the case has no codes)."""

    if not supplier_code:
        return [p for p in case.products if not p.supplier_code] or list(case.products)
    return [p for p in case.products if p.supplier_code == supplier_code]


def _day(value: date | None) -> str:
    return value.strftime("%d/%m/%Y") if value else ""


def _join(values: Sequence[str | None]) -> str:
    seen: list[str] = []
    for value in values:
        if value and value not in seen:
            seen.append(value)
    return ", ".join(seen)


def suggested_actions(case: TrackingCase) -> str:
    """What JDH already did, as the OM's «Acciones inmediatas» starts."""

    if case.pickup_order_requested == "yes":
        number = f" {case.pickup_order_number}" if case.pickup_order_number else ""
        return f"Se aprobó la orden de recogida{number} del producto afectado."
    return ""


def om_context(
    case: TrackingCase,
    om: ImprovementOpportunity,
    products: Sequence[CaseProduct],
    now: datetime,
) -> dict:
    """The values of the OM marks (photos aside: the renderer adds them).

    Args:
        case (TrackingCase): The case.
        om (ImprovementOpportunity): The OM, with what the team wrote.
        products (Sequence[CaseProduct]): The products of its supplier.
        now (datetime): When it is generated.

    Returns:
        dict: Mark name to text; ``products`` is a list of rows.
    """

    site = case.site
    signer = om.signer or Signer(None, None, None, None, None)
    return {
        "code": om.code,
        "city": (site.city if site else None) or "",
        "address": (site.address if site else None) or "",
        "report_date": now.astimezone(_LOCAL).strftime("%d/%m/%Y"),
        "channel": CHANNELS.get(case.origin, case.origin),
        "pickup_order": case.pickup_order_number or "",
        "document_number": om.document_number or _join([p.invoice_number for p in products]),
        "document_date": _day(om.document_date or case.delivery_date),
        "classification": case.request_type.prefix,
        "novelty": case.subject_ref.name if case.subject_ref else (case.subject or ""),
        "dependency": case.dependency.name if case.dependency else "",
        "requester": case.requester_name or "",
        "sales_executive": case.sales_executive_name or "",
        "immediate_actions": om.immediate_actions or "",
        "customer": case.customer_name or "",
        "supplier": om.supplier_name or case.supplier_name or "",
        "cause": case.cause_ref.name if case.cause_ref else "",
        "description": case.description or "",
        "products": [
            {
                "sku": p.sku or "",
                "description": p.description or "",
                "lot": p.lot or "",
                "expiry": _day(p.expiry_date),
                "quantity": str(p.affected_quantity) if p.affected_quantity else "",
            }
            for p in products
        ],
        "request_note": om.request_note or "",
        "signer_name": signer.name or "",
        "signer_position": signer.position or "",
        "signer_email": signer.email or "",
        "signer_phone": signer.phone or "",
    }
