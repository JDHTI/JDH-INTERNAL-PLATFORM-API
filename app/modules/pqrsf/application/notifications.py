"""Queue and send PQRSF emails: assignment notices and the two organization timers."""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import UTC, date, datetime
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

from app.core.errors import DependencyUnavailableError, ForbiddenError
from app.modules.identity.domain.principal import DirectoryUser, Principal
from app.modules.mail.domain import MailGateway, OutgoingFile
from app.modules.mail.layout import BrandedEmail, logo_file, render_fragment, render_html
from app.modules.pqrsf.application import notification_templates as templates
from app.modules.pqrsf.domain.notifications import (
    ASSIGNMENT,
    CASE_NEWS,
    CLIENT_REPLY,
    CUSTOMER_FORWARD,
    DAILY_SUMMARY,
    DOCUMENT_DELIVERY,
    FAILED,
    FEEDBACK_ACK,
    GRACE,
    OVERDUE_REMINDER,
    PENDING,
    REQUEST_ACK,
    SENT,
    SUPPLIER_MESSAGE,
    Notification,
    NotificationSettings,
    retry_after,
)
from app.modules.pqrsf.domain.ports import (
    Bell,
    BinaryStorage,
    CaseRepository,
    DocumentFileSource,
    EmailRepository,
    HolidayProvider,
    ManagerPolicy,
    NotificationRepository,
    PeopleDirectory,
    PqrsfRepository,
    RecipientDirectory,
)
from app.modules.pqrsf.domain.tracking import CaseFilters, TrackingCase

logger = logging.getLogger(__name__)


def _copy_of(item: Notification) -> dict[str, list[str]]:
    """The ``cc`` keyword for the mail gateway: empty when the email copies nobody."""

    copied = [str(address) for address in item.payload.get("cc") or []]
    return {"cc": copied} if copied else {}


_PAGE = 100
_MAX_PAGES = 200


class NotificationService:
    """Build, queue and deliver the emails PQRSF sends from its own mailbox."""

    def __init__(
        self,
        repository: NotificationRepository,
        cases: CaseRepository,
        people: PeopleDirectory,
        team: RecipientDirectory,
        mail: MailGateway,
        managers: ManagerPolicy,
        holidays: HolidayProvider,
        mailbox: str,
        area_slug: str,
        ui_base_url: str = "",
        timezone: str = "America/Bogota",
        redirect_to: str | None = None,
        enabled: bool = True,
        emails: EmailRepository | None = None,
        storage: BinaryStorage | None = None,
        documents: DocumentFileSource | None = None,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        requests: PqrsfRepository | None = None,
        bell: Bell | None = None,
        noreply_sender: str = "",
    ) -> None:
        """Create the service.

        Args:
            repository (NotificationRepository): Outbox, timers and job runs.
            cases (CaseRepository): Case queries for the reports.
            people (PeopleDirectory): Platform users (names and emails).
            team (RecipientDirectory): Members of the area that receives the reports.
            mail (MailGateway): Sends from the PQRSF mailbox.
            managers (ManagerPolicy): Who may read and change the timers.
            holidays (HolidayProvider): Tells whether a date is a working day.
            mailbox (str): The PQRSF mailbox every email leaves from.
            area_slug (str): Area slug used in links to the case panel.
            ui_base_url (str): Frontend root for links; blank leaves links out.
            timezone (str): IANA zone of the timers.
            redirect_to (str | None): Sends every email here instead (testing).
            enabled (bool): When off, nothing is queued (assignment notices are skipped).
            emails (EmailRepository | None): Case files, to attach them to client replies.
            storage (BinaryStorage | None): Private bucket the case files are read from.
            documents (DocumentFileSource | None): The document manager, to attach certificates.
            clock (Callable[[], datetime]): Current instant (replaced in tests).
            requests (PqrsfRepository | None): Public requests, to attach the client's
                evidence to supplier messages.
            bell (Bell | None): The intranet bell, for news on a case.
            noreply_sender (str): Mailbox nobody reads, for forwards to the customer.
        """

        self.requests = requests
        self.bell = bell
        self.noreply_sender = noreply_sender.strip()
        self.repository = repository
        self.cases = cases
        self.people = people
        self.team = team
        self.mail = mail
        self.managers = managers
        self.holidays = holidays
        self.mailbox = mailbox
        self.area_slug = area_slug
        self.ui_base_url = ui_base_url.strip().rstrip("/")
        self.zone = ZoneInfo(timezone)
        self.redirect_to = (redirect_to or "").strip() or None
        self.enabled = enabled
        self.emails = emails
        self.storage = storage
        self.documents = documents
        self.clock = clock

    # --- Timers ---

    async def get_settings(self, principal: Principal) -> NotificationSettings:
        """The organization timers, for anyone who works PQRSF."""

        await self._require_manager(principal)
        return await self._settings()

    async def update_settings(
        self, settings: NotificationSettings, principal: Principal
    ) -> NotificationSettings:
        """Replace the organization timers; they apply to everyone and survive restarts."""

        await self._require_manager(principal)
        await self.repository.save_settings(settings.to_json(), principal.user_id)
        return settings

    async def _settings(self) -> NotificationSettings:
        return NotificationSettings.from_json(await self.repository.get_settings())

    async def _require_manager(self, principal: Principal) -> None:
        if not await self.managers.is_manager(principal):
            raise ForbiddenError("Solo quien gestiona PQRSF puede hacer esta operación")

    # --- Assignment notice ---

    async def case_assigned(
        self, case: TrackingCase, assignee: DirectoryUser, actor: Principal, note: str | None
    ) -> None:
        """Queue «you have a new case» for the person a colleague handed it to."""

        if not self.enabled or not assignee.email or not assignee.is_active:
            return
        subject, body = templates.assignment(
            case, assignee.display_name, actor.display_name or actor.email, note, self._url(case)
        )
        await self.repository.enqueue(
            Notification(
                id=uuid4(),
                kind=ASSIGNMENT,
                recipient_email=assignee.email,
                recipient_user_id=assignee.id,
                subject=subject,
                body=body,
                case_id=case.id,
                dedupe_key=None,
                next_attempt_at=self.clock(),
            )
        )

    # --- News on a case ---

    async def case_news(self, case: TrackingCase, sender: str, excerpt: str) -> None:
        """Tell who works the case that an email from the client or a supplier joined it.

        The responsible gets it; a case without one goes to the whole team. It rings
        their bell (and Web Push) and queues an email. The bell does not depend on
        ``enabled``: it never leaves the intranet.
        """

        recipients = await self._news_recipients(case)
        if not recipients:
            return
        who = templates.news_sender(case, sender)
        if self.bell is not None:
            try:
                await self.bell.ring(
                    [user.id for user in recipients],
                    f"pqrsf-news:{case.id}:{self.clock().isoformat()}",
                    f"Novedad en el caso {case.code}",
                    f"{who} escribió por correo",
                    f"/{self.area_slug}/pqrsf/{case.id}",
                )
            except Exception:
                logger.warning("Could not ring the bell for case %s", case.code, exc_info=True)
        if not self.enabled:
            return
        for user in recipients:
            subject, body = templates.case_news(
                case, user.display_name, sender, excerpt, self._url(case)
            )
            await self.repository.enqueue(
                Notification(
                    id=uuid4(),
                    kind=CASE_NEWS,
                    recipient_email=user.email or "",
                    recipient_user_id=user.id,
                    subject=subject,
                    body=body,
                    case_id=case.id,
                    dedupe_key=None,
                    next_attempt_at=self.clock(),
                )
            )

    async def _news_recipients(self, case: TrackingCase) -> list[DirectoryUser]:
        if case.assignee_user_id is not None:
            owner = (await self.people.get_users({case.assignee_user_id})).get(
                case.assignee_user_id
            )
            if owner is not None and owner.is_active and owner.email:
                return [owner]
        return await self._team_users()

    # --- Scheduled jobs ---

    async def run_scheduled(self) -> list[str]:
        """Run every job whose time has come today; call it every minute or so.

        A job runs once per business day, from its time until two hours later (so a
        restart does not lose it, and a stale report is never sent the next morning).

        Returns:
            list[str]: The jobs that ran in this call.
        """

        local = self.clock().astimezone(self.zone)
        today = local.date()
        if not await self.holidays.is_business_day(today):
            return []
        settings = await self._settings()
        ran = []
        for job, runner in (
            (DAILY_SUMMARY, self.run_daily_summary),
            (OVERDUE_REMINDER, self.run_overdue_reminder),
        ):
            schedule = settings.schedule(job)
            due = datetime.combine(today, schedule.at, tzinfo=self.zone)
            if not schedule.enabled or not (due <= local <= due + GRACE):
                continue
            if not await self.repository.claim_job_run(job, today):
                continue
            try:
                await runner(today)
            except Exception:
                await self.repository.release_job_run(job, today)
                raise
            ran.append(job)
        return ran

    async def run_daily_summary(self, today: date) -> int:
        """Queue the end-of-day summary for every member of the team.

        Returns:
            int: Emails queued.
        """

        open_cases = await self._cases(CaseFilters(open_only=True), today)
        overdue = [c for c in open_cases if c.is_overdue(today)]
        owners = await self.people.get_users(
            {c.assignee_user_id for c in open_cases if c.assignee_user_id}
        )
        lines = [
            templates.case_line(
                c,
                today,
                owners[c.assignee_user_id].display_name or ""
                if c.assignee_user_id in owners
                else "",
            )
            for c in sorted(open_cases, key=lambda c: (c.due_on or date.max, c.code))
        ]
        queued = 0
        for user in await self._team_users():
            subject, body = templates.daily_summary(
                user.display_name, lines, len(open_cases), len(overdue), today, self._list_url()
            )
            queued += await self._queue(
                DAILY_SUMMARY, user, subject, body, f"{DAILY_SUMMARY}:{today}:{user.id}"
            )
        return queued

    async def run_overdue_reminder(self, today: date) -> int:
        """Queue one email per person that groups every case they have overdue.

        Cases without a responsible go to the whole team, in the same email as their own.

        Returns:
            int: Emails queued.
        """

        overdue = await self._cases(CaseFilters(open_only=True, overdue=True), today)
        mine: dict[UUID, list[TrackingCase]] = {}
        orphans: list[TrackingCase] = []
        for case in sorted(overdue, key=lambda c: (c.due_on or date.max, c.code)):
            if case.assignee_user_id is None:
                orphans.append(case)
            else:
                mine.setdefault(case.assignee_user_id, []).append(case)
        team = {u.id: u for u in await self._team_users()}
        owners = await self.people.get_users(mine)
        recipients = {**{i: u for i, u in owners.items() if u.is_active and u.email}, **team}
        orphan_lines = [templates.case_line(c, today) for c in orphans]
        queued = 0
        for user_id, user in recipients.items():
            own_lines = [templates.case_line(c, today) for c in mine.get(user_id, [])]
            team_lines = orphan_lines if user_id in team else []
            if not own_lines and not team_lines:
                continue
            subject, body = templates.overdue_reminder(
                user.display_name, own_lines, team_lines, self._list_url()
            )
            queued += await self._queue(
                OVERDUE_REMINDER, user, subject, body, f"{OVERDUE_REMINDER}:{today}:{user_id}"
            )
        return queued

    # --- Delivery ---

    async def dispatch(self, limit: int = 20) -> int:
        """Send the emails that are due; a failure is retried later with growing waits.

        Returns:
            int: Emails sent in this call.
        """

        sent = 0
        for item in await self.repository.claim_due(self.clock(), limit):
            to, subject, body = item.recipient_email, item.subject, item.body
            if self.redirect_to:
                to, subject = self.redirect_to, f"[PRUEBA para {item.recipient_email}] {subject}"
            now = self.clock()
            try:
                await self._deliver(item, to, subject, body)
            except Exception as exc:
                item.attempts += 1
                item.error = str(exc)[:500]
                item.next_attempt_at = retry_after(item.attempts, now)
                item.status = PENDING if item.next_attempt_at else FAILED
                logger.warning("Notification %s failed (attempt %s)", item.id, item.attempts)
            else:
                item.status, item.sent_at, item.error = SENT, now, None
                sent += 1
            await self.repository.save_notification(item)
        return sent

    async def _deliver(self, item: Notification, to: str, subject: str, body: str) -> None:
        """Send one email; a client reply goes inside the client's conversation when it can."""

        if item.kind == DOCUMENT_DELIVERY:
            files = [*await self._certificates(item), *await self._reply_files(item)]
            await self.mail.send(self.mailbox, to, subject, body, files)
            return
        if item.kind == CUSTOMER_FORWARD:
            # From the mailbox nobody reads: the customer's answers must not reach a case.
            if not self.noreply_sender:
                raise DependencyUnavailableError("No hay cuenta no-reply configurada")
            content = BrandedEmail.from_payload(item.payload.get("email") or {})
            files = [*await self._reply_files(item), logo_file()]
            await self.mail.send(
                self.noreply_sender, to, subject, render_html(content), files, html=True
            )
            return
        # People of the organization copied on the email. With a testing redirect nobody
        # else gets a copy either.
        copied = {} if self.redirect_to else _copy_of(item)
        if item.kind == SUPPLIER_MESSAGE:
            # A new email (not an answer in a thread): it can carry large evidence files.
            files = [*await self._reply_files(item), *await self._evidence(item)]
            await self.mail.send(self.mailbox, to, subject, body, files, **copied)
            return
        if item.kind not in (CLIENT_REPLY, FEEDBACK_ACK, REQUEST_ACK):
            await self.mail.send(self.mailbox, to, subject, body)
            return
        files = await self._reply_files(item)
        # Emails to the customer queued with their content go out in the JDH layout, with
        # the logo inline; older ones in the queue keep their plain text.
        branded = item.payload.get("email")
        content = BrandedEmail.from_payload(branded) if branded else None
        options: dict = {**copied, "html": True} if content else copied
        if content:
            files = [*files, logo_file()]
        graph_id = item.payload.get("graph_id")
        # With a testing redirect the reply must not reach the real client, so it is a
        # new email to the redirect address instead of an answer inside their thread.
        if graph_id and not self.redirect_to:
            text = render_fragment(content) if content else body
            if await self.mail.reply(self.mailbox, graph_id, text, files, **options) is not False:
                return
            # The message to answer was deleted from the mailbox: write a new email instead.
        text = render_html(content) if content else body
        await self.mail.send(self.mailbox, to, subject, text, files, **options)

    async def _certificates(self, item: Notification) -> list[OutgoingFile]:
        """The certificates of a delivery, read from the manager as they are right now."""

        if self.documents is None:
            raise DependencyUnavailableError("El gestor documental no está disponible")
        files: list[OutgoingFile] = []
        for value in item.payload.get("document_ids", []):
            found = await self.documents.get_file(UUID(value))
            if found is None:
                raise DependencyUnavailableError(
                    "Un documento ya no existe en el gestor documental"
                )
            files.append(found)
        return files

    async def _evidence(self, item: Notification) -> list[OutgoingFile]:
        """The client's photos and video picked for a supplier message."""

        ids = [UUID(value) for value in item.payload.get("evidence_ids", [])]
        request_id = item.payload.get("request_id")
        if not ids or not request_id:
            return []
        if self.requests is None or self.storage is None:
            raise DependencyUnavailableError("Las evidencias del cliente no están disponibles")
        files: list[OutgoingFile] = []
        for attachment_id in ids:
            found = await self.requests.get_attachment(UUID(request_id), attachment_id)
            if found is None:
                continue
            content = await self.storage.get_object(found.storage_key)
            files.append(OutgoingFile(found.original_filename, found.content_type, content))
        return files

    async def _reply_files(self, item: Notification) -> list[OutgoingFile]:
        ids = [UUID(value) for value in item.payload.get("file_ids", [])]
        if not ids:
            return []
        if self.emails is None or self.storage is None or item.case_id is None:
            raise DependencyUnavailableError("Los adjuntos de la respuesta no están disponibles")
        found = await self.emails.get_attachments(item.case_id, ids)
        return [
            OutgoingFile(
                a.original_filename, a.content_type, await self.storage.get_object(a.storage_key)
            )
            for a in found
        ]

    # --- Helpers ---

    async def _queue(
        self, kind: str, user: DirectoryUser, subject: str, body: str, dedupe_key: str
    ) -> int:
        queued = await self.repository.enqueue(
            Notification(
                id=uuid4(),
                kind=kind,
                recipient_email=user.email or "",
                recipient_user_id=user.id,
                subject=subject,
                body=body,
                case_id=None,
                dedupe_key=dedupe_key,
                next_attempt_at=self.clock(),
            )
        )
        return int(queued)

    async def _team_users(self) -> list[DirectoryUser]:
        users = await self.people.get_users(await self.team.team_user_ids())
        return [u for u in users.values() if u.is_active and u.email]

    async def _cases(self, filters: CaseFilters, today: date) -> list[TrackingCase]:
        found: list[TrackingCase] = []
        for page in range(1, _MAX_PAGES + 1):
            result = await self.cases.list_cases(filters, page, _PAGE, today)
            found.extend(result.items)
            if not result.items or len(found) >= result.total:
                break
        return found

    def _list_url(self) -> str | None:
        return f"{self.ui_base_url}/{self.area_slug}/pqrsf" if self.ui_base_url else None

    def _url(self, case: TrackingCase) -> str | None:
        base = self._list_url()
        return f"{base}/{case.id}" if base else None
