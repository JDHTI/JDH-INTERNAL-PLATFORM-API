"""The PQRSF case an action comes from, read through the PQRSF services."""

from __future__ import annotations

from typing import Protocol
from uuid import UUID

from app.modules.action_plans.domain.ports import CaseFacts
from app.modules.identity.domain.principal import Principal


class CaseTracking(Protocol):
    """The PQRSF case tracking service (``CaseTrackingService``)."""

    async def require_work(self, case_id: UUID, principal: Principal):  # noqa: ANN201
        """The case, when the caller may work it."""
        ...

    async def list_events_of(self, case):  # noqa: ANN001, ANN201
        """The timeline of the case."""
        ...

    async def record_action_plan(
        self, case_id: UUID, principal: Principal, plan_id: UUID, plan_code: str
    ) -> None:
        """Leave the action in the case timeline."""
        ...


class RequestReader(Protocol):
    """The PQRSF public requests (``PqrsfRepository``)."""

    async def get_request(self, request_id: UUID):  # noqa: ANN201
        """A public request with its evidence."""
        ...


class PqrsfCaseSource:
    """Turns a PQRSF case into what an action takes from it."""

    def __init__(self, tracking: CaseTracking, requests: RequestReader) -> None:
        """Create the adapter.

        Args:
            tracking (CaseTracking): Access rules and timeline of the cases.
            requests (RequestReader): The public request, for its photos.
        """

        self.tracking = tracking
        self.requests = requests

    async def facts(self, case_id: UUID, principal: Principal) -> CaseFacts:
        """The case, when the caller may work it."""

        case = await self.tracking.require_work(case_id, principal)
        request = await self.requests.get_request(case.request_id) if case.request_id else None
        photos = any(
            (a.content_type or "").startswith("image/")
            for a in (getattr(request, "attachments", None) or [])
        )
        events = await self.tracking.list_events_of(case)
        om_codes = tuple(
            dict.fromkeys(
                e.data.get("code")
                for e in events
                if e.event_type == "om_sent" and e.data.get("code")
            )
        )
        site = case.site
        city = (getattr(site, "city", None) or getattr(site, "name", None)) if site else None
        dependency = case.dependency
        subject = case.subject_ref.name if case.subject_ref else case.subject
        return CaseFacts(
            id=case.id,
            code=case.code,
            received_on=case.received_on,
            customer_nit=case.customer_nit,
            customer_name=case.customer_name,
            requester_name=case.requester_name,
            city=city,
            process_code=getattr(dependency, "process_code", None) if dependency else None,
            subject=subject,
            description=case.description,
            cause_label=case.cause_ref.name if case.cause_ref else None,
            has_photos=photos,
            pickup_order=case.pickup_order_number if case.pickup_order_requested == "yes" else None,
            om_codes=om_codes,
        )

    async def link(
        self, case_id: UUID, principal: Principal, plan_id: UUID, plan_code: str
    ) -> None:
        """Leave the action in the case timeline."""

        await self.tracking.record_action_plan(case_id, principal, plan_id, plan_code)
