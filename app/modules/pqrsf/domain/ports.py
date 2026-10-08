"""Persistence contracts for the PQRSF module."""

from __future__ import annotations

from collections.abc import Container, Iterable
from datetime import date, datetime
from typing import Protocol
from uuid import UUID

from app.modules.identity.domain.principal import DirectoryUser, Principal
from app.modules.pqrsf.domain.email import CaseAttachment, EmailCaseInput, EmailMessage
from app.modules.pqrsf.domain.entities import (
    DocumentCategory,
    PqrsfAttachment,
    PqrsfFilters,
    PqrsfPage,
    PqrsfRequest,
    RequestType,
    SanitizedImage,
)
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
