"""Persistence contracts for the PQRSF module."""

from __future__ import annotations

from collections.abc import Container, Iterable, Sequence
from datetime import date, datetime
from typing import Protocol
from uuid import UUID

from app.modules.identity.domain.principal import DirectoryUser, Principal
from app.modules.mail.domain import OutgoingFile
from app.modules.pqrsf.domain.certificates import (
    Certificate,
    CertificateEntry,
    DeliveryFilters,
    DeliveryPage,
    DeliverySummary,
    DocumentDelivery,
    ManagerDocument,
)
from app.modules.pqrsf.domain.email import CaseAttachment, EmailCaseInput, EmailMessage
from app.modules.pqrsf.domain.entities import (
    Cause,
    DocumentCategory,
    PqrsfAttachment,
    PqrsfFilters,
    PqrsfPage,
    PqrsfRequest,
    ProductMaster,
    ProductOption,
    RequestType,
    SanitizedImage,
    Subject,
)
from app.modules.pqrsf.domain.feedback import (
    Feedback,
    FeedbackFile,
    FeedbackFilters,
    FeedbackPage,
    FeedbackSummary,
)
from app.modules.pqrsf.domain.item_delivery import ItemDelivery
from app.modules.pqrsf.domain.notifications import Notification
from app.modules.pqrsf.domain.supplier_messages import SupplierContact
from app.modules.pqrsf.domain.tracking import (
    ActivityFilters,
    CaseEvent,
    CaseFilters,
    CasePage,
    CaseStatus,
    CaseSummary,
    Dependency,
    DocumentType,
    LastSeen,
    TrackingCase,
)


class PqrsfRepository(Protocol):
    """Store classifications and requests."""

    async def list_request_types(self, active_only: bool = True) -> list[RequestType]:
        """List request types ordered by display name."""
        ...

    async def get_request_type(self, request_type_id: UUID) -> RequestType | None:
        """Find one request type by UUID."""
        ...

    async def list_document_categories(self) -> list[DocumentCategory]:
        """List the categories, with their documents, offered on the public form."""
        ...

    async def get_by_idempotency_key(self, key: str) -> PqrsfRequest | None:
        """Find a request previously submitted with a key."""
        ...

    async def create_request(self, request: PqrsfRequest) -> PqrsfRequest:
        """Persist a request."""
        ...

    async def get_request(self, request_id: UUID) -> PqrsfRequest | None:
        """Find one request with its type and site."""
        ...

    async def list_requests(self, filters: PqrsfFilters, page: int, size: int) -> PqrsfPage:
        """List administrator results."""
        ...

    async def add_attachments(self, attachments: list[PqrsfAttachment]) -> None:
        """Persist sanitized attachment metadata."""
        ...

    async def list_attachments(self, request_id: UUID) -> list[PqrsfAttachment]:
        """List a request's attachments."""
        ...

    async def get_attachment(self, request_id: UUID, attachment_id: UUID) -> PqrsfAttachment | None:
        """Find one attachment owned by a request."""
        ...

    async def delete_request(self, request_id: UUID) -> None:
        """Delete a request and its metadata after a storage rollback."""
        ...

    async def save_request_type(self, request_type: RequestType) -> RequestType:
        """Persist the follow-up settings of a request type."""
        ...

    async def list_subjects(self, active_only: bool = True) -> list[Subject]:
        """List subjects in display order, each with its causes in display order.

        Args:
            active_only (bool): Leave out disabled subjects and causes.

        Returns:
            list[Subject]: The subject catalog.
        """
        ...

    async def list_areas(self) -> list[Dependency]:
        """List the active dependencies, in display order: the areas a requester can pick.

        Returns:
            list[Dependency]: The areas of the public form.
        """
        ...

    async def save_subject(self, subject: Subject) -> Subject:
        """Insert or update a subject (its causes are saved apart).

        Args:
            subject (Subject): Subject to store.

        Returns:
            Subject: The stored subject, without causes.
        """
        ...

    async def save_cause(self, cause: Cause) -> Cause:
        """Insert or update a cause.

        Args:
            cause (Cause): Cause to store.

        Returns:
            Cause: The stored cause.
        """
        ...


class SupplierContactRepository(Protocol):
    """Store the addresses cases wrote to as supplier."""

    async def search(
        self, supplier_code: str | None, query: str | None, limit: int
    ) -> list[SupplierContact]:
        """Find contacts of a supplier, or by address or name, most recently used first.

        Args:
            supplier_code (str | None): Only this supplier's contacts.
            query (str | None): Part of the address, contact or supplier name.
            limit (int): Most results to return.

        Returns:
            list[SupplierContact]: Matching contacts.
        """
        ...

    async def remember(self, contacts: list[SupplierContact]) -> None:
        """Insert new contacts and refresh when the known ones were last used.

        Args:
            contacts (list[SupplierContact]): The addresses just written to.

        Returns:
            None
        """
        ...


class VideoInspector(Protocol):
    """Check an evidence video before it is scanned and stored."""

    async def inspect(
        self, filename: str | None, content_type: str | None, content: bytes
    ) -> SanitizedImage:
        """Validate name, MIME and real container of a video.

        Args:
            filename (str | None): Name sent by the browser.
            content_type (str | None): MIME sent by the browser.
            content (bytes): The video.

        Returns:
            SanitizedImage: The video with a safe name and its stored MIME type.
        """
        ...


class ProductCatalog(Protocol):
    """Read the JDH product master (implemented over the commercial masters)."""

    async def search(self, query: str, limit: int) -> list[ProductOption]:
        """Find active products whose reference is the text or whose name contains it.

        Args:
            query (str): Reference or part of the name.
            limit (int): Most results to return.

        Returns:
            list[ProductOption]: Matching products, by reference.
        """
        ...

    async def resolve(self, skus: Sequence[str]) -> dict[str, ProductMaster]:
        """Look up references with their name and supplier.

        Args:
            skus (Sequence[str]): JDH references.

        Returns:
            dict[str, ProductMaster]: The references that exist, by reference.
        """
        ...


class CaseOpener(Protocol):
    """Open the follow-up case of a public request (implemented by tracking)."""

    async def open_for_request(self, request: PqrsfRequest) -> TrackingCase:
        """Create the case for a request, or return the existing one."""
        ...

    async def code_for_request(self, request_id: UUID) -> str | None:
        """Return the consecutive of the request's case, if any."""
        ...


class CaseRepository(Protocol):
    """Store follow-up catalogs, cases, items and their timeline.

    Every mutation receives the timeline events it produces so both are
    written in the same transaction.
    """

    async def list_statuses(self, include_inactive: bool = False) -> list[CaseStatus]:
        """List statuses in display order."""
        ...

    async def get_status(self, status_id: UUID) -> CaseStatus | None:
        """Find a status by identifier."""
        ...

    async def get_status_by_code(self, code: str) -> CaseStatus | None:
        """Find a status by code."""
        ...

    async def save_status(self, status: CaseStatus) -> CaseStatus:
        """Insert or update a status; an initial one clears the flag on the rest."""
        ...

    async def list_dependencies(self, include_inactive: bool = False) -> list[Dependency]:
        """List internal dependencies in display order."""
        ...

    async def get_dependency(self, dependency_id: UUID) -> Dependency | None:
        """Find a dependency by identifier."""
        ...

    async def save_dependency(self, dependency: Dependency) -> Dependency:
        """Insert or update a dependency."""
        ...

    async def list_document_types(self, include_inactive: bool = False) -> list[DocumentType]:
        """List document types by name."""
        ...

    async def get_document_type(self, document_type_id: UUID) -> DocumentType | None:
        """Find a document type by identifier."""
        ...

    async def save_document_type(self, document_type: DocumentType) -> DocumentType:
        """Insert or update a document type."""
        ...

    async def create_case(
        self, case: TrackingCase, event: CaseEvent, number: int | None = None
    ) -> TrackingCase:
        """Insert a case with its items, allocating the next consecutive.

        ``number`` keeps a historical consecutive (imports) and moves the
        counter past it.
        """
        ...

    async def get_case(self, case_id: UUID) -> TrackingCase | None:
        """Find a case with its items."""
        ...

    async def get_case_by_request(self, request_id: UUID) -> TrackingCase | None:
        """Find the case opened for a public request."""
        ...

    async def legacy_key_exists(self, source_key: str) -> bool:
        """Whether an imported row was already loaded."""
        ...

    async def list_cases(self, filters: CaseFilters, page: int, size: int, today: date) -> CasePage:
        """Return one page of cases with their items."""
        ...

    async def summarize(self, filters: CaseFilters, today: date) -> CaseSummary:
        """Count the cases matching the filters."""
        ...

    async def save_case(self, case: TrackingCase, events: list[CaseEvent]) -> TrackingCase:
        """Save the header and make the stored items match ``case.items``.

        Items missing from the list are deleted and new ones inserted, all in
        one transaction with the events.
        """
        ...

    async def add_event(self, event: CaseEvent) -> None:
        """Append a timeline entry that changes nothing else (a comment)."""
        ...

    async def list_events(self, case_id: UUID) -> list[CaseEvent]:
        """List the timeline oldest first."""
        ...

    async def delete_case(self, case_id: UUID, event: CaseEvent) -> None:
        """Delete a case and its documents; its history stays, with the event given."""
        ...

    async def list_activity(self, filters: ActivityFilters) -> list[CaseEvent]:
        """History of the whole module, newest first, at most ``filters.limit``."""
        ...

    async def has_recent_access(
        self, event_type: str, actor_user_id: UUID, case_id: UUID | None, since: datetime
    ) -> bool:
        """Whether the person already left this access record after ``since``."""
        ...

    async def purge_access_events(self, before: datetime) -> int:
        """Delete access records older than ``before``; the rest is never purged."""
        ...

    async def last_seen(self, since: datetime) -> list[LastSeen]:
        """Last inbox opening of each person, most recent first."""
        ...

    async def suggest(self, field: str, query: str, limit: int) -> list[str]:
        """Distinct values previously typed in a free-text field."""
        ...


class EmailCases(Protocol):
    """The follow-up cases an email can open or join (implemented by tracking)."""

    async def open_from_email(self, command: EmailCaseInput) -> TrackingCase:
        """Create the case of a message that matched none."""
        ...

    async def record_email(
        self, case_id: UUID, message_id: UUID, sender: str, note: str, data: dict
    ) -> None:
        """Add a received email to the case timeline."""
        ...

    async def find_case(self, case_id: UUID) -> TrackingCase | None:
        """Load a case, or ``None`` when it was deleted."""
        ...


class EmailRepository(Protocol):
    """Store the emails received for PQRSF, their attachments and the sync cursor."""

    async def add_message(self, message: EmailMessage) -> bool:
        """Store a message once; ``False`` means its Internet message id was already there."""
        ...

    async def get_message(self, internet_message_id: str) -> EmailMessage | None:
        """Find a stored message by its Internet message id."""
        ...

    async def save_message(self, message: EmailMessage) -> None:
        """Update the status, case, attempts and detail of a stored message."""
        ...

    async def list_retryable(self, max_attempts: int) -> list[EmailMessage]:
        """Messages claimed or failed that have not used up their attempts."""
        ...

    async def find_case_by_conversation(self, conversation_id: str) -> UUID | None:
        """The case a conversation already belongs to."""
        ...

    async def find_feedback_by_conversation(self, conversation_id: str) -> UUID | None:
        """The suggestion or compliment a conversation started."""
        ...

    async def find_case_by_code(self, code: str) -> UUID | None:
        """The case with this consecutive."""
        ...

    async def get_delta_link(self, mailbox: str) -> str | None:
        """The cursor of the last sync, or ``None`` before the first one."""
        ...

    async def save_delta_link(self, mailbox: str, delta_link: str) -> None:
        """Remember where the next sync starts."""
        ...

    async def add_attachments(self, attachments: list[CaseAttachment]) -> None:
        """Persist attachment metadata."""
        ...

    async def list_attachments(self, case_id: UUID) -> list[CaseAttachment]:
        """A case's attachments, oldest first."""
        ...

    async def get_attachment(self, case_id: UUID, attachment_id: UUID) -> CaseAttachment | None:
        """One attachment owned by the case."""
        ...

    async def get_attachments(self, case_id: UUID, ids: list[UUID]) -> list[CaseAttachment]:
        """Several attachments of one case, in the order asked."""
        ...

    async def latest_inbound_graph_id(self, case_id: UUID) -> str | None:
        """The newest message the client sent for the case, to answer inside its thread."""
        ...


class NotificationRepository(Protocol):
    """Store the email outbox, the organization timers and which jobs ran each day."""

    async def enqueue(self, notification: Notification) -> bool:
        """Queue an email; ``False`` when its dedupe key was already queued."""
        ...

    async def claim_due(self, now: datetime, limit: int) -> list[Notification]:
        """Pending emails whose next attempt is due, oldest first."""
        ...

    async def save_notification(self, notification: Notification) -> None:
        """Persist the status, attempts and error of a queued email."""
        ...

    async def get_notification(self, notification_id: UUID) -> Notification | None:
        """One queued email by identifier."""
        ...

    async def list_for_case(self, case_id: UUID, kind: str) -> list[Notification]:
        """The emails of one kind queued for a case, oldest first."""
        ...

    async def count_recent(self, kind: str, recipient_email: str, since: datetime) -> int:
        """Emails of one kind queued for an address since an instant."""
        ...

    async def get_settings(self) -> dict | None:
        """The stored timers, or ``None`` before anyone changed them."""
        ...

    async def save_settings(self, value: dict, user_id: UUID) -> None:
        """Replace the timers."""
        ...

    async def claim_job_run(self, job: str, run_date: date) -> bool:
        """Record that a job runs on a date; ``False`` when it already did."""
        ...

    async def release_job_run(self, job: str, run_date: date) -> None:
        """Forget a run that failed so the next tick can try again."""
        ...


class FeedbackRepository(Protocol):
    """Store suggestions and compliments, their files and the state of their thank-you."""

    async def add(self, feedback: Feedback) -> tuple[Feedback, bool]:
        """Store a message once; the second value is ``False`` when it already existed.

        A message is the same one when it has the same request or the same email.
        """
        ...

    async def get(self, feedback_id: UUID) -> Feedback | None:
        """One message with its files and the state of its reply."""
        ...

    async def list_feedback(self, filters: FeedbackFilters, page: int, size: int) -> FeedbackPage:
        """One page of messages, newest first, without their files."""
        ...

    async def summarize(self) -> FeedbackSummary:
        """Totals and unread counts per type."""
        ...

    async def mark_read(self, feedback_id: UUID, user_id: UUID, at: datetime) -> None:
        """Record who opened the message first; a later opening changes nothing."""
        ...

    async def set_reply(
        self, feedback_id: UUID, notification_id: UUID | None, skipped_reason: str | None
    ) -> None:
        """Remember that the thank-you was queued, or why it was not."""
        ...

    async def add_files(self, files: list[FeedbackFile]) -> None:
        """Persist file metadata."""
        ...

    async def list_files(self, feedback_id: UUID) -> list[FeedbackFile]:
        """The files of a message, oldest first."""
        ...

    async def get_file(self, feedback_id: UUID, file_id: UUID) -> FeedbackFile | None:
        """One file owned by the message."""
        ...


class CertificateRepository(Protocol):
    """Store which file of the document manager answers each document type."""

    async def list_entries(self) -> list[CertificateEntry]:
        """Every document type the public form offers, with its certificate if it has one."""
        ...

    async def get_entry(self, document_type_id: UUID) -> CertificateEntry | None:
        """One document type with its certificate."""
        ...

    async def type_names(self, document_type_ids: list[UUID]) -> dict[UUID, str]:
        """Names of document types, including those of the category answered by a person."""
        ...

    async def automatic_for(self, document_type_ids: list[UUID]) -> dict[UUID, Certificate]:
        """The certificates that are on and have a file, by document type."""
        ...

    async def save(
        self, document_type_id: UUID, document_id: UUID | None, is_active: bool, user_id: UUID
    ) -> Certificate:
        """Create or replace the certificate of a document type."""
        ...

    async def delete(self, document_type_id: UUID) -> None:
        """Remove the certificate of a document type."""
        ...

    async def list_documents(
        self, area_slug: str, query: str | None, folder_id: UUID | None, limit: int
    ) -> list[ManagerDocument]:
        """Files of an area of the manager, to choose a certificate from."""
        ...

    async def get_document(self, document_id: UUID) -> ManagerDocument | None:
        """One file of the manager with where it is kept."""
        ...


class DocumentFileSource(Protocol):
    """Read a file of the document manager at the moment it is attached to an email."""

    async def get_file(self, document_id: UUID) -> OutgoingFile | None:
        """The current content of the document, or ``None`` when it no longer exists."""
        ...


class DeliveryRepository(Protocol):
    """Store the requests answered automatically with certificates."""

    async def add(self, delivery: DocumentDelivery) -> tuple[DocumentDelivery, bool]:
        """Store a delivery once per request; ``False`` when it already existed."""
        ...

    async def get(self, delivery_id: UUID) -> DocumentDelivery | None:
        """One delivery with the state of its emails."""
        ...

    async def get_by_request(self, request_id: UUID) -> DocumentDelivery | None:
        """The delivery of a public request."""
        ...

    async def list_deliveries(self, filters: DeliveryFilters, page: int, size: int) -> DeliveryPage:
        """One page, newest first, with the state of their emails."""
        ...

    async def summarize(self) -> DeliverySummary:
        """Totals and unread."""
        ...

    async def mark_read(self, delivery_id: UUID, user_id: UUID, at: datetime) -> None:
        """Record who opened it first."""
        ...

    async def set_notifications(self, delivery_id: UUID, notification_ids: list[UUID]) -> None:
        """Remember the emails queued for the delivery."""
        ...

    async def set_case(self, delivery_id: UUID, case_code: str) -> None:
        """Remember the case opened for what a person answers."""
        ...


class ItemDeliveryRepository(Protocol):
    """Store the answers given to the requested documents of a case."""

    async def add(self, delivery: ItemDelivery) -> None:
        """Store an answer."""
        ...

    async def list_for_case(self, case_id: UUID) -> list[ItemDelivery]:
        """The answers of a case, oldest first, with where their email stands."""
        ...


class AssignmentNotifier(Protocol):
    """Tell someone a case was handed to them (implemented by the notification service)."""

    async def case_assigned(
        self, case: TrackingCase, assignee: DirectoryUser, actor: Principal, note: str | None
    ) -> None:
        """Queue the «you have a new case» email."""
        ...


class RecipientDirectory(Protocol):
    """Who receives the area-wide emails."""

    async def team_user_ids(self) -> list[UUID]:
        """Active members of the area that manages PQRSF."""
        ...


class HolidayProvider(Protocol):
    """Decide whether a date is a working day."""

    async def is_business_day(self, day: date) -> bool:
        """Monday to Friday and not a public holiday."""
        ...


class PeopleDirectory(Protocol):
    """Platform users, read through the identity module."""

    async def require_user(self, email: str) -> DirectoryUser:
        """Return the user with this email or raise not found."""
        ...

    async def get_users(self, user_ids: Iterable[UUID]) -> dict[UUID, DirectoryUser]:
        """Load several users at once."""
        ...

    async def search_users(self, query: str, limit: int = 20) -> list[DirectoryUser]:
        """Find active users by name or email."""
        ...


class ManagerPolicy(Protocol):
    """Decide who manages PQRSF: assigns cases, sees them all and edits catalogs."""

    async def is_manager(self, principal: Principal) -> bool:
        """Whether the caller manages PQRSF."""
        ...


class BusinessCalendar(Protocol):
    """Local dates and non-working days used for due dates."""

    def today(self) -> date:
        """Current date in the business time zone."""
        ...

    def local_date(self, moment: datetime) -> date:
        """Date of an instant in the business time zone."""
        ...

    def holidays(self, start: date, end: date) -> Container[date]:
        """Holidays that may fall between two dates (inclusive)."""
        ...


class ImageProcessor(Protocol):
    """Validate and sanitize an uploaded image."""

    async def sanitize(
        self, filename: str | None, content_type: str | None, content: bytes
    ) -> SanitizedImage:
        """Return safe bytes or raise an application validation error."""
        ...


class MalwareScanner(Protocol):
    """Scan sanitized bytes with an antivirus service."""

    async def scan(self, content: bytes) -> None:
        """Return when clean or raise when the scanner rejects the content."""
        ...


class BinaryStorage(Protocol):
    """Private object storage needed by PQRSF attachments."""

    async def put_object(self, key: str, body: bytes, content_type: str | None = None) -> None:
        """Store bytes under a generated key."""
        ...

    async def get_object(self, key: str) -> bytes:
        """Read bytes from a private key."""
        ...

    async def delete_object(self, key: str) -> None:
        """Delete an object during cleanup."""
        ...
