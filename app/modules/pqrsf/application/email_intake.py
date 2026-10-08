"""Turn the emails of the PQRSF mailbox into cases, keeping each conversation together."""

from __future__ import annotations

import hashlib
import logging
from collections.abc import Callable, Collection
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID, uuid4

from app.core.errors import DependencyUnavailableError, InvalidInputError
from app.modules.mail.domain import InboundMessage, MailGateway
from app.modules.pqrsf.application.attachment_guard import (
    IMAGE_EXTENSIONS,
    AttachmentGuard,
    extension_of,
    safe_filename,
)
from app.modules.pqrsf.domain.email import (
    CREATED,
    FAILED,
    IGNORED,
    RECEIVED,
    THREADED,
    CaseAttachment,
    EmailCaseInput,
    EmailMessage,
    ParsedRequest,
    acknowledgment,
    case_attachment_key,
    clean_subject,
    find_case_code,
    fold,
    is_automatic,
    match_name,
    parse_request,
    strip_quoted_reply,
)
from app.modules.pqrsf.domain.entities import RequestType
from app.modules.pqrsf.domain.ports import (
    BinaryStorage,
    EmailCases,
    EmailRepository,
    ImageProcessor,
    MalwareScanner,
    PqrsfRepository,
)
from app.modules.sites.application.service import SiteService
from app.modules.sites.domain.entities import Site

logger = logging.getLogger(__name__)

DEFAULT_REQUEST_TYPE = "peticion"
_EXCERPT = 500


@dataclass
class SyncReport:
    """What one pass over the mailbox did."""

    created: int = 0
    threaded: int = 0
    ignored: int = 0
    failed: int = 0


@dataclass(frozen=True)
class _Stored:
    """Attachments saved for a message and those that were turned away."""

    saved: list[CaseAttachment]
    rejected: list[dict]


def _trim(value: str | None, size: int) -> str | None:
    cleaned = " ".join((value or "").split())
    return cleaned[:size] or None


class EmailIntakeService:
    """Read the PQRSF inbox and open or extend a case for every message.

    Each message is claimed (a row keyed by its Internet message id) before any
    work, so a repeated delivery never opens a second case, and the steps after the
    case exists (files, acknowledgment) are flagged so a retry resumes where it stopped.
    """

    def __init__(
        self,
        mail: MailGateway,
        emails: EmailRepository,
        cases: EmailCases,
        requests: PqrsfRepository,
        sites: SiteService,
        storage: BinaryStorage,
        image_processor: ImageProcessor,
        malware_scanner: MalwareScanner,
        mailbox: str,
        ignored_senders: Collection[str] = (),
        since: datetime | None = None,
        max_attempts: int = 5,
        max_attachments: int = 10,
        max_attachment_bytes: int = 10 * 1024 * 1024,
        max_total_attachment_bytes: int = 25 * 1024 * 1024,
        allowed_extensions: Collection[str] = (),
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        """Create the service.

        Args:
            mail (MailGateway): Mailbox access.
            emails (EmailRepository): Stored messages, attachments and cursor.
            cases (EmailCases): Opens cases and writes their timeline.
            requests (PqrsfRepository): Request type catalog.
            sites (SiteService): Site catalog.
            storage (BinaryStorage): Private bucket for attachments.
            image_processor (ImageProcessor): Image sanitation.
            malware_scanner (MalwareScanner): Antivirus.
            mailbox (str): The PQRSF mailbox to read and answer from.
            ignored_senders (Collection[str]): Addresses whose mail never opens a case.
            since (datetime | None): On the very first sync, only mail received from here.
            max_attempts (int): Tries per message before it stays ``failed``.
            max_attachments (int): Files kept per message.
            max_attachment_bytes (int): Largest single file.
            max_total_attachment_bytes (int): Largest sum of files per message.
            allowed_extensions (Collection[str]): Lower-case extensions accepted.
            clock (Callable[[], datetime]): Current instant (replaced in tests).
        """

        self.mail = mail
        self.emails = emails
        self.cases = cases
        self.requests = requests
        self.sites = sites
        self.storage = storage
        self.guard = AttachmentGuard(
            image_processor,
            malware_scanner,
            allowed_extensions,
            max_attachments,
            max_attachment_bytes,
            max_total_attachment_bytes,
        )
        self.mailbox = mailbox.lower()
        self.own_addresses = {self.mailbox, *(a.lower() for a in ignored_senders)}
        self.since = since
        self.max_attempts = max_attempts
        self.clock = clock

    # --- Sync ---

    async def sync(self) -> SyncReport:
        """Process what arrived since the last pass, then retry what failed.

        The cursor advances only after every listed message was claimed, so a
        message is never skipped; one that fails midway is retried from its row.
        """

        report = SyncReport()
        delta_link = await self.emails.get_delta_link(self.mailbox)
        cutoff = (self.since or self.clock()) if delta_link is None else None
        result = await self.mail.list_new_messages(self.mailbox, delta_link)
        tried: set[UUID] = set()
        for graph_id in result.message_ids:
            message = await self.mail.get_message(self.mailbox, graph_id)
            if message is None or (cutoff is not None and message.received_at < cutoff):
                continue
            handled = await self._handle(message, None, report)
            if handled is not None:
                tried.add(handled.id)
        await self.emails.save_delta_link(self.mailbox, result.delta_link)

        # A message that just failed waits for the next pass: whatever broke
        # (antivirus, bucket) is unlikely to be back within seconds.
        for stored in await self.emails.list_retryable(self.max_attempts):
            if stored.id in tried:
                continue
            message = await self.mail.get_message(self.mailbox, stored.graph_id)
            if message is None:
                stored.status, stored.error = IGNORED, "El correo ya no existe en el buzón"
                await self.emails.save_message(stored)
                continue
            await self._handle(message, stored, report)
        if report.created or report.threaded or report.failed:
            logger.info(
                "PQRSF mail sync: %s opened, %s threaded, %s ignored, %s failed",
                report.created,
                report.threaded,
                report.ignored,
                report.failed,
            )
        return report

    async def _handle(
        self, message: InboundMessage, row: EmailMessage | None, report: SyncReport
    ) -> EmailMessage | None:
        """Process one message; ``None`` means it was already stored (a repeated delivery)."""

        if row is None:
            row = EmailMessage(
                id=uuid4(),
                internet_message_id=message.internet_message_id,
                graph_id=message.graph_id,
                conversation_id=message.conversation_id,
                mailbox=self.mailbox,
                sender_address=message.sender_address,
                sender_name=message.sender_name,
                subject=message.subject,
                body_text=message.body_text,
                received_at=message.received_at,
                status=RECEIVED,
            )
            if not await self.emails.add_message(row):
                return None
        row.attempts += 1
        try:
            await self._route(message, row)
        except Exception as exc:
            logger.exception("Could not process email %s", message.internet_message_id)
            row.status, row.error = FAILED, str(exc)[:500]
            report.failed += 1
        else:
            row.error = None
            if row.status == IGNORED:
                report.ignored += 1
            elif row.status == THREADED:
                report.threaded += 1
            else:
                report.created += 1
        await self.emails.save_message(row)
        return row

    # --- Routing ---

    async def _route(self, message: InboundMessage, row: EmailMessage) -> None:
        if row.case_id is None:
            reason = is_automatic(message.headers, message.sender_address, self.own_addresses)
            if reason:
                row.status, row.detail = IGNORED, {**row.detail, "reason": reason}
                return
            existing = await self._open_case_of(message)
            if existing is not None:
                await self._join(message, row, existing)
            else:
                await self._open(message, row, None)
        # Past this point the case exists: each step is flagged so a retry resumes.
        if not row.detail.get("files_done"):
            stored = await self._store_attachments(message, row)
            row.detail = {
                **row.detail,
                "files_done": True,
                "files": [a.original_filename for a in stored.saved],
                "rejected_files": stored.rejected,
            }
            await self.emails.save_message(row)
        code = row.detail.get("code")
        if row.detail.get("opened") and not row.detail.get("ack_sent"):
            await self.mail.reply(
                self.mailbox,
                message.graph_id,
                acknowledgment(code, bool(row.detail.get("needs_review"))),
            )
            row.detail = {**row.detail, "ack_sent": True}
            await self.emails.save_message(row)
        try:
            await self.mail.mark_processed(self.mailbox, message.graph_id, code)
        except DependencyUnavailableError:
            logger.warning("Could not mark email %s as processed", message.internet_message_id)
        # A retried message comes back as ``failed``: settle it by what it did.
        row.status = CREATED if row.detail.get("opened") else THREADED

    async def _open_case_of(self, message: InboundMessage):
        """The open case this message continues, found by conversation then by subject code."""

        case_id = (
            await self.emails.find_case_by_conversation(message.conversation_id)
            if message.conversation_id
            else None
        )
        if case_id is None:
            code = find_case_code(message.subject)
            case_id = await self.emails.find_case_by_code(code) if code else None
        if case_id is None:
            return None
        return await self.cases.find_case(case_id)

    async def _join(self, message: InboundMessage, row: EmailMessage, case) -> None:
        if case.is_closed:
            # A closed case is not reopened (it would distort its response times):
            # the message starts a new case that points back to it.
            await self._open(message, row, case.code)
            return
        text = strip_quoted_reply(message.body_text)
        await self.cases.record_email(
            case.id,
            row.id,
            message.sender_address,
            text[:_EXCERPT],
            {"subject": message.subject},
        )
        row.case_id, row.status = case.id, THREADED
        row.detail = {**row.detail, "code": case.code}
        await self.emails.save_message(row)

    async def _open(self, message: InboundMessage, row: EmailMessage, related: str | None) -> None:
        parsed = parse_request(message.body_text)
        request_type = await self._request_type(parsed)
        site = await self._site(parsed)
        review = (
            parsed.free_text
            or not parsed.description
            or (parsed.request_type is not None and request_type is None)
            or (parsed.site is not None and site is None)
        )
        chosen = request_type or await self._default_type()
        description = parsed.description or message.body_text.strip() or "(sin contenido)"
        # The case subject is what the client wrote in the Subject of the email, the same
        # text the team sees in the mailbox. A different «Motivo:» line is not lost: it
        # opens the description.
        header = _trim(clean_subject(message.subject), 200)
        reason = _trim(parsed.subject, 200)
        if reason and header and fold(reason) != fold(header):
            description = f"Motivo: {reason}\n\n{description}"
        case = await self.cases.open_from_email(
            EmailCaseInput(
                request_type=chosen,
                site=site,
                received_at=message.received_at,
                subject=header or reason,
                description=description,
                requester_name=_trim(parsed.requester_name or message.sender_name, 200),
                requester_email=message.sender_address,
                business_name=_trim(parsed.business_name, 200),
                nit=_trim(parsed.nit, 30),
                message_id=row.id,
                related_code=related,
            )
        )
        row.case_id, row.status = case.id, CREATED
        row.detail = {**row.detail, "code": case.code, "needs_review": review, "opened": True}
        if related:
            row.detail["related_case"] = related
        await self.emails.save_message(row)

    async def _request_type(self, parsed: ParsedRequest) -> RequestType | None:
        options = []
        for item in await self.requests.list_request_types():
            options += [(item.name, item), (item.code, item)]
        return match_name(parsed.request_type, options)

    async def _default_type(self) -> RequestType:
        types = await self.requests.list_request_types()
        chosen = next((t for t in types if t.code == DEFAULT_REQUEST_TYPE), None) or next(
            iter(types), None
        )
        if chosen is None:
            raise DependencyUnavailableError("No hay tipos de solicitud configurados")
        return chosen

    async def _site(self, parsed: ParsedRequest) -> Site | None:
        options: list[tuple[str | None, Site]] = []
        for site in await self.sites.list_sites():
            options += [(site.name, site), (site.code, site), (site.abbreviation, site)]
        return match_name(parsed.site, options)

    # --- Attachments ---

    async def _store_attachments(self, message: InboundMessage, row: EmailMessage) -> _Stored:
        """Validate, clean, scan and store the files of a message under its case.

        A file that fails validation is reported, never fatal. A scanner or bucket
        outage raises instead, so the whole step is retried; nothing is recorded
        until every object is stored, and stored objects are removed on failure.
        """

        if not message.has_attachments or row.case_id is None:
            return _Stored([], [])
        files = await self.mail.list_attachments(self.mailbox, message.graph_id)
        saved: list[CaseAttachment] = []
        rejected: list[dict] = []
        keys: list[str] = []
        total = 0
        # What the case already holds: a signature logo repeated in every reply, or a
        # file sent twice, is kept once.
        known = {a.checksum_sha256 for a in await self.emails.list_attachments(row.case_id)}
        try:
            for file in files:
                if (
                    file.is_inline
                    and extension_of(safe_filename(file.name)) not in IMAGE_EXTENSIONS
                ):
                    continue  # embedded objects that are not pictures
                reason = self.guard.refuse(file.name, file.size, len(saved), total)
                if reason:
                    rejected.append({"name": file.name, "reason": reason})
                    continue
                content = await self.mail.download_attachment(
                    self.mailbox, message.graph_id, file.id
                )
                try:
                    clean = await self.guard.clean(file.name, content)
                except InvalidInputError as exc:
                    rejected.append({"name": file.name, "reason": str(exc)})
                    continue
                name, extension, content_type, data = clean
                checksum = hashlib.sha256(data).hexdigest()
                if checksum in known:
                    continue
                known.add(checksum)
                attachment_id = uuid4()
                key = case_attachment_key(row.case_id, attachment_id, extension)
                await self.storage.put_object(key, data, content_type)
                keys.append(key)
                total += len(data)
                saved.append(
                    CaseAttachment(
                        id=attachment_id,
                        case_id=row.case_id,
                        message_id=row.id,
                        original_filename=name,
                        extension=extension,
                        content_type=content_type,
                        size_bytes=len(data),
                        checksum_sha256=checksum,
                        storage_key=key,
                        created_at=self.clock(),
                    )
                )
            await self.emails.add_attachments(saved)
        except Exception:
            for key in keys:
                try:
                    await self.storage.delete_object(key)
                except Exception:
                    logger.warning("Could not remove orphan object %s", key)
            raise
        if rejected:
            await self.cases.record_email(
                row.case_id,
                row.id,
                message.sender_address,
                "Adjuntos no almacenados",
                {"rejected_files": rejected},
            )
        return _Stored(saved, rejected)
