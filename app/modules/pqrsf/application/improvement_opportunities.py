"""Improvement opportunity (OM, P.06.F.04): generate it from a case, edit it, send it.

- The template is a Word file with ``{{ … }}`` marks kept in the bucket; Calidad uploads a
  new version without a deploy and the previous ones stay in the history.
- The OM takes its consecutive (``1181BOG``) the first time it is generated; generating it
  again keeps the number, as on paper.
- Each OM is kept as Word (editable: a hand-edited version can be uploaded) and as PDF,
  which is what goes to the supplier. When the converter is down the OM keeps its Word and
  the PDF is retried.
"""

from __future__ import annotations

import asyncio
import hashlib
import io
import logging
import zipfile
from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime
from typing import Protocol
from uuid import UUID, uuid4

from app.core.errors import (
    ConflictError,
    DependencyUnavailableError,
    ForbiddenError,
    InvalidInputError,
    NotFoundError,
)
from app.modules.identity.domain.principal import Principal
from app.modules.pqrsf.application.dto import EventView
from app.modules.pqrsf.domain.entities import clean_multiline, clean_optional
from app.modules.pqrsf.domain.improvement import (
    DEFAULT_REQUEST_NOTE,
    MAX_PHOTOS,
    OM_DRAFT,
    OM_SENT,
    TEMPLATE_FIELDS,
    TEMPLATE_KIND_OM,
    TEMPLATE_MAX_BYTES,
    DocumentTemplate,
    ImprovementOpportunity,
    OmFilters,
    OmHistory,
    clean_template_kind,
    om_code,
    om_context,
    om_document_key,
    suggested_actions,
    supplier_products,
    template_key,
)
from app.modules.pqrsf.domain.ports import (
    BinaryStorage,
    ImprovementRepository,
    ManagerPolicy,
    PdfConverter,
    PqrsfRepository,
    SignerDirectory,
    WordRenderer,
)
from app.modules.pqrsf.domain.timeline import EVENT_OM_GENERATED, EVENT_OM_SENT
from app.modules.pqrsf.domain.tracking import TrackingCase

logger = logging.getLogger(__name__)

DOCX_TYPE = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
PDF_TYPE = "application/pdf"
# A hand-edited OM keeps its photos: it can weigh more than the template.
EDITED_MAX_BYTES = 20 * 1024 * 1024
_IMAGE_TYPES = ("image/",)


class OmTracking(Protocol):
    """What the OM needs from case tracking."""

    async def require_view(self, case_id: UUID, principal: Principal) -> TrackingCase:
        """The case, when the caller may see it."""
        ...

    async def require_work(self, case_id: UUID, principal: Principal) -> TrackingCase:
        """The case, when the caller may work it."""
        ...

    async def record_om(
        self, case: TrackingCase, principal: Principal, event_type: str, data: dict
    ) -> EventView:
        """Write an OM step in the timeline."""
        ...


@dataclass(frozen=True)
class OmCommand:
    """What the team writes on the OM; the rest comes from the case."""

    supplier_code: str | None = None
    supplier_name: str | None = None
    immediate_actions: str | None = None
    request_note: str | None = None
    evidence_ids: tuple[UUID, ...] = ()
    document_number: str | None = None
    document_date: date | None = None


@dataclass(frozen=True)
class OmFile:
    """One document of an OM, ready to download or attach."""

    filename: str
    content_type: str
    content: bytes


def _sample_case_context() -> dict:
    """Values to try a new template with before it replaces the active one."""

    return {name: f"[{name}]" for name in TEMPLATE_FIELDS - {"products", "photos"}} | {
        "products": [
            {"sku": "1", "description": "Producto", "lot": "L1", "expiry": "", "quantity": "1"}
        ]
    }


def _is_docx(content: bytes) -> bool:
    try:
        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            return "word/document.xml" in archive.namelist()
    except zipfile.BadZipFile:
        return False


class ImprovementOpportunityService:
    """Generate, keep and send the OMs of the cases, and manage their template."""

    def __init__(
        self,
        tracking: OmTracking,
        requests: PqrsfRepository,
        repository: ImprovementRepository,
        storage: BinaryStorage,
        renderer: WordRenderer,
        converter: PdfConverter,
        signers: SignerDirectory,
        managers: ManagerPolicy,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        """Create the service.

        Args:
            tracking (OmTracking): Access rules and the case timeline.
            requests (PqrsfRepository): The public request and its evidence.
            repository (ImprovementRepository): Templates, consecutive and OMs.
            storage (BinaryStorage): The private bucket.
            renderer (WordRenderer): Fills the Word template.
            converter (PdfConverter): Word to PDF (Gotenberg).
            signers (SignerDirectory): Name, position and phone of who signs.
            managers (ManagerPolicy): Who manages PQRSF (template and consecutive).
            clock (Callable[[], datetime]): Current instant (replaced in tests).
        """

        self.tracking = tracking
        self.requests = requests
        self.repository = repository
        self.storage = storage
        self.renderer = renderer
        self.converter = converter
        self.signers = signers
        self.managers = managers
        self.clock = clock

    # --- Template and consecutive (managers) ---

    async def _require_manager(self, principal: Principal) -> None:
        if not await self.managers.is_manager(principal):
            raise ForbiddenError("Solo quien gestiona PQRSF puede cambiar la plantilla de la OM")

    async def list_templates(self, kind: str, principal: Principal) -> list[DocumentTemplate]:
        """Every version of a template, newest first (the active one is marked)."""

        await self._require_manager(principal)
        return await self.repository.list_templates(clean_template_kind(kind))

    async def upload_template(
        self,
        kind: str,
        filename: str,
        content: bytes,
        version_label: str,
        principal: Principal,
    ) -> DocumentTemplate:
        """Make a new template version the active one (managers)."""

        await self._require_manager(principal)
        return await self.install_template(
            kind, filename, content, version_label, principal.user_id
        )

    async def install_template(
        self,
        kind: str,
        filename: str,
        content: bytes,
        version_label: str,
        user_id: UUID | None,
    ) -> DocumentTemplate:
        """Check a template, store it in the bucket and make it the active version.

        Also used by the deploy command that loads the first version.

        Raises:
            InvalidInputError: Not a Word file, too big, marks missing or unknown, or it
                does not fill with sample values.
        """

        kind = clean_template_kind(kind)
        label = clean_optional(version_label, "versión", 60)
        if not label:
            raise InvalidInputError("Indica la versión de la plantilla (por ejemplo, «V2»)")
        if len(content) > TEMPLATE_MAX_BYTES:
            raise InvalidInputError("La plantilla no puede superar 5 MB")
        if not filename.lower().endswith(".docx") or not _is_docx(content):
            raise InvalidInputError("La plantilla debe ser un documento de Word (.docx)")
        marks = await asyncio.to_thread(self.renderer.marks, content)
        missing, unknown = TEMPLATE_FIELDS - marks, marks - TEMPLATE_FIELDS
        if missing or unknown:
            parts = []
            if missing:
                parts.append("faltan " + ", ".join(sorted(missing)))
            if unknown:
                parts.append("no se conocen " + ", ".join(sorted(unknown)))
            raise InvalidInputError(f"Revisa las marcas de la plantilla: {'; '.join(parts)}")
        await asyncio.to_thread(self.renderer.render, content, _sample_case_context(), ())
        template = DocumentTemplate(
            id=uuid4(),
            kind=kind,
            version_label=label,
            filename=filename[-255:],
            storage_key="",
            size_bytes=len(content),
            checksum_sha256=hashlib.sha256(content).hexdigest(),
            is_active=True,
            uploaded_by=user_id,
        )
        template.storage_key = template_key(kind, template.id)
        await self.storage.put_object(template.storage_key, content, DOCX_TYPE)
        try:
            return await self.repository.add_template(template)
        except Exception:
            await self.storage.delete_object(template.storage_key)
            raise

    async def template_file(self, template_id: UUID, principal: Principal) -> OmFile:
        """The Word of a template version (managers)."""

        await self._require_manager(principal)
        template = await self.repository.get_template(template_id)
        if template is None:
            raise NotFoundError("Plantilla no encontrada")
        return OmFile(
            template.filename, DOCX_TYPE, await self.storage.get_object(template.storage_key)
        )

    async def next_number(self, principal: Principal) -> int:
        """The number the next OM will take (managers)."""

        await self._require_manager(principal)
        return await self.repository.next_om_number()

    async def set_next_number(self, value: int, principal: Principal) -> int:
        """Where the OM consecutive goes on: Calidad's last number plus one (managers)."""

        await self._require_manager(principal)
        if value < 1 or value > 9_999_999:
            raise InvalidInputError("El consecutivo de la OM debe estar entre 1 y 9.999.999")
        await self.repository.set_next_om_number(value, principal.user_id)
        return value

    # --- History (follow-up across cases) ---

    async def history(
        self, filters: OmFilters, page: int, size: int, principal: Principal
    ) -> OmHistory:
        """Every OM, newest first, to follow up which suppliers have not answered yet.

        For whoever reads PQRSF (Calidad). Each row says when its supplier first wrote
        back after it was sent; the totals count drafts, sent and waiting, and answered.
        """

        if not await self.managers.can_read(principal):
            raise ForbiddenError("Solo quien consulta PQRSF ve el historial de oportunidades")
        if page < 1 or not 1 <= size <= 100:
            raise InvalidInputError("Página desde 1 y tamaño entre 1 y 100")
        if filters.status not in (None, OM_DRAFT, OM_SENT):
            raise InvalidInputError("Estado de OM no válido")
        rows, total, counts = await self.repository.history(filters, (page - 1) * size, size)
        return OmHistory(rows, total, counts["draft"], counts["awaiting"], counts["replied"])

    # --- OMs of a case ---

    async def list_for_case(
        self, case_id: UUID, principal: Principal
    ) -> list[ImprovementOpportunity]:
        """The OMs of a case, for anyone who can see it."""

        case = await self.tracking.require_view(case_id, principal)
        return await self.repository.list_for_case(case.id)

    async def generate(
        self, case_id: UUID, command: OmCommand, principal: Principal
    ) -> ImprovementOpportunity:
        """Fill the OM of a case for one supplier, or fill it again.

        Only who works the case, while it is open. The consecutive is taken the first
        time; generating again keeps it and drops a hand-edited Word. A sent OM is not
        generated again.

        Raises:
            ConflictError: Closed case, OM already sent, or no active template.
            InvalidInputError: The site has no OM code, or evidence not of this case.
        """

        case = await self.tracking.require_work(case_id, principal)
        if case.is_closed:
            raise ConflictError("El caso está finalizado: no se puede generar la OM")
        template = await self.repository.active_template(TEMPLATE_KIND_OM)
        if template is None:
            raise ConflictError("Falta la plantilla de la OM: un gestor debe subirla")
        site_code = case.site.om_code if case.site else None
        if not site_code:
            raise InvalidInputError(
                "La sede del caso no tiene código de OM: configúralo en la administración de sedes"
            )
        supplier_code = clean_optional(command.supplier_code, "código del proveedor", 40)
        products = supplier_products(case, supplier_code)
        supplier_name = (
            clean_optional(command.supplier_name, "proveedor", 200)
            or next((p.supplier_name for p in products if p.supplier_name), None)
            or case.supplier_name
        )
        existing = next(
            (
                om
                for om in await self.repository.list_for_case(case.id)
                if (om.supplier_code or None) == supplier_code
            ),
            None,
        )
        if existing is not None and existing.status == OM_SENT:
            raise ConflictError(f"La OM {existing.code} ya se envió: no se vuelve a generar")
        photos = await self._photos(case, command.evidence_ids)
        if existing is None:
            number = await self.repository.take_om_number()
            om = ImprovementOpportunity(
                id=uuid4(),
                case_id=case.id,
                number=number,
                code=om_code(number, site_code),
                supplier_code=supplier_code,
                supplier_name=supplier_name,
                created_by=principal.user_id,
            )
        else:
            om = replace(existing, supplier_name=supplier_name)
        om.immediate_actions = (
            clean_multiline(command.immediate_actions, "acciones inmediatas", 2_000)
            if command.immediate_actions is not None
            else om.immediate_actions or suggested_actions(case) or None
        )
        om.request_note = (
            clean_multiline(command.request_note, "nota", 1_000)
            if command.request_note is not None
            else om.request_note or DEFAULT_REQUEST_NOTE
        )
        om.document_number = (
            clean_optional(command.document_number, "documento", 200)
            if command.document_number is not None
            else om.document_number
        )
        om.document_date = command.document_date or om.document_date
        om.evidence_ids = [attachment_id for attachment_id, _ in photos]
        om.signer = await self.signers.signer(principal.user_id)
        om.template_id = template.id
        now = self.clock()
        context = om_context(case, om, products, now)
        source = await self.storage.get_object(template.storage_key)
        document = await asyncio.to_thread(
            self.renderer.render, source, context, [content for _, content in photos]
        )
        om.edited = False
        om.generated_at = now
        await self._store_documents(om, document)
        stored = await self.repository.save(om)
        await self.tracking.record_om(
            case,
            principal,
            EVENT_OM_GENERATED,
            {
                "om_id": str(stored.id),
                "code": stored.code,
                "supplier_name": stored.supplier_name,
                "again": existing is not None,
                "pdf": stored.pdf_key is not None,
            },
        )
        return stored

    async def upload_edited(
        self, case_id: UUID, om_id: UUID, filename: str, content: bytes, principal: Principal
    ) -> ImprovementOpportunity:
        """Replace the Word of a draft OM with one edited by hand; the PDF follows it."""

        case, om = await self._load(case_id, om_id, principal, work=True)
        if om.status == OM_SENT:
            raise ConflictError(f"La OM {om.code} ya se envió: no se reemplaza")
        if len(content) > EDITED_MAX_BYTES:
            raise InvalidInputError("El Word de la OM no puede superar 20 MB")
        if not filename.lower().endswith(".docx") or not _is_docx(content):
            raise InvalidInputError("Sube la OM como documento de Word (.docx)")
        om.edited = True
        await self._store_documents(om, content)
        stored = await self.repository.save(om)
        await self.tracking.record_om(
            case,
            principal,
            EVENT_OM_GENERATED,
            {
                "om_id": str(stored.id),
                "code": stored.code,
                "supplier_name": stored.supplier_name,
                "edited": True,
                "pdf": stored.pdf_key is not None,
            },
        )
        return stored

    async def retry_pdf(
        self, case_id: UUID, om_id: UUID, principal: Principal
    ) -> ImprovementOpportunity:
        """Convert the Word of an OM to PDF again (after the converter was down)."""

        _, om = await self._load(case_id, om_id, principal, work=True)
        if not om.docx_key:
            raise ConflictError("La OM no tiene Word: genérala primero")
        document = await self.storage.get_object(om.docx_key)
        pdf = await self.converter.to_pdf(om.filename("docx"), document)
        om.pdf_key = om_document_key(om.case_id, om.id, "pdf")
        await self.storage.put_object(om.pdf_key, pdf, PDF_TYPE)
        return await self.repository.save(om)

    async def document(self, case_id: UUID, om_id: UUID, fmt: str, principal: Principal) -> OmFile:
        """The Word or the PDF of an OM, for anyone who can see the case."""

        _, om = await self._load(case_id, om_id, principal, work=False)
        if fmt == "pdf":
            if not om.pdf_key:
                raise ConflictError("La OM aún no tiene PDF: vuelve a intentar la conversión")
            return OmFile(om.filename("pdf"), PDF_TYPE, await self.storage.get_object(om.pdf_key))
        if fmt != "docx":
            raise InvalidInputError("El formato debe ser docx o pdf")
        if not om.docx_key:
            raise ConflictError("La OM no tiene Word: genérala primero")
        return OmFile(om.filename("docx"), DOCX_TYPE, await self.storage.get_object(om.docx_key))

    # --- Sending (used by the supplier messages) ---

    async def for_sending(
        self, case: TrackingCase, om_id: UUID
    ) -> tuple[ImprovementOpportunity, OmFile]:
        """The OM of a case and its PDF, to attach to a message to the supplier."""

        om = await self.repository.get(om_id)
        if om is None or om.case_id != case.id:
            raise NotFoundError("OM no encontrada en el caso")
        if not om.pdf_key:
            raise ConflictError(
                f"La OM {om.code} aún no tiene PDF: reintenta la conversión antes de enviarla"
            )
        return om, OmFile(om.filename("pdf"), PDF_TYPE, await self.storage.get_object(om.pdf_key))

    async def mark_sent(
        self,
        case: TrackingCase,
        om: ImprovementOpportunity,
        principal: Principal,
        recipients: Sequence[str],
    ) -> None:
        """Record that the OM left for the supplier: its status, the event and the flag."""

        om.status = OM_SENT
        om.sent_at = self.clock()
        await self.repository.save(om)
        await self.tracking.record_om(
            case,
            principal,
            EVENT_OM_SENT,
            {
                "om_id": str(om.id),
                "code": om.code,
                "supplier_name": om.supplier_name,
                "to": list(recipients),
            },
        )

    # --- Helpers ---

    async def _load(
        self, case_id: UUID, om_id: UUID, principal: Principal, work: bool
    ) -> tuple[TrackingCase, ImprovementOpportunity]:
        case = await (
            self.tracking.require_work(case_id, principal)
            if work
            else self.tracking.require_view(case_id, principal)
        )
        om = await self.repository.get(om_id)
        if om is None or om.case_id != case.id:
            raise NotFoundError("OM no encontrada en el caso")
        return case, om

    async def _photos(
        self, case: TrackingCase, evidence_ids: Sequence[UUID]
    ) -> list[tuple[UUID, bytes]]:
        """The chosen photos of the client's request, in the order given."""

        if not evidence_ids:
            return []
        if len(evidence_ids) > MAX_PHOTOS:
            raise InvalidInputError(f"La OM lleva máximo {MAX_PHOTOS} fotos")
        if case.request_id is None:
            raise InvalidInputError("El caso no tiene evidencias del cliente")
        photos = []
        for attachment_id in dict.fromkeys(evidence_ids):
            attachment = await self.requests.get_attachment(case.request_id, attachment_id)
            if attachment is None:
                raise InvalidInputError("Una de las fotos elegidas no es evidencia de este caso")
            if not attachment.content_type.startswith(_IMAGE_TYPES):
                raise InvalidInputError(f"«{attachment.original_filename}» no es una foto")
            photos.append((attachment_id, await self.storage.get_object(attachment.storage_key)))
        return photos

    async def _store_documents(self, om: ImprovementOpportunity, document: bytes) -> None:
        """Keep the Word and, when the converter answers, the PDF."""

        om.docx_key = om_document_key(om.case_id, om.id, "docx")
        await self.storage.put_object(om.docx_key, document, DOCX_TYPE)
        try:
            pdf = await self.converter.to_pdf(om.filename("docx"), document)
        except DependencyUnavailableError:
            logger.warning("OM %s stays without PDF: the converter is down", om.code)
            om.pdf_key = None
            return
        om.pdf_key = om_document_key(om.case_id, om.id, "pdf")
        await self.storage.put_object(om.pdf_key, pdf, PDF_TYPE)
