"""Permissions and use cases for commercial requests and BI decisions."""

from decimal import Decimal
from uuid import UUID

from app.core.errors import ForbiddenError, InvalidInputError
from app.modules.identity.domain.principal import Principal
from app.modules.supplier_supports.domain.ports import Calendar, ManagerPolicy
from app.modules.supplier_supports.domain.requests import SupportRequestRepository


class SupportRequestService:
    def __init__(
        self,
        repository: SupportRequestRepository,
        managers: ManagerPolicy,
        requesters: ManagerPolicy,
        calendar: Calendar,
    ) -> None:
        self.repository = repository
        self.managers = managers
        self.requesters = requesters
        self.calendar = calendar

    async def permissions(self, principal: Principal) -> dict[str, bool]:
        manage = await self.managers.is_manager(principal)
        return {
            "can_manage": manage,
            "can_review": manage,
            "can_request": manage or await self.requesters.is_manager(principal),
        }

    async def require_manager(self, principal: Principal) -> None:
        if not await self.managers.is_manager(principal):
            raise ForbiddenError("Solo BI y administradores pueden gestionar solicitudes")

    async def owner(self, principal: Principal) -> UUID | None:
        permissions = await self.permissions(principal)
        if not permissions["can_request"]:
            raise ForbiddenError(
                "Solo Comercial, BI y administradores pueden consultar solicitudes"
            )
        return None if permissions["can_review"] else principal.user_id

    @staticmethod
    def reason(text: str) -> str:
        text = text.strip()
        if not 3 <= len(text) <= 2000:
            raise InvalidInputError("Indica un motivo de 3 a 2000 caracteres")
        return text

    async def create(
        self, principal: Principal, support_id: UUID, version: int, value: Decimal, reason: str
    ):
        await self.owner(principal)
        return await self.repository.create(
            support_id,
            version,
            value,
            self.reason(reason),
            principal.user_id,
            principal.email,
            self.calendar.today(),
        )

    async def search(
        self,
        principal: Principal,
        status: str | None,
        q: str | None,
        page: int,
        size: int,
        mine: bool = False,
    ):
        owner = await self.owner(principal)
        return await self.repository.search(
            principal.user_id if mine else owner, status, q, page, size
        )

    async def get(self, principal: Principal, request_id: UUID, mine: bool = False):
        owner = await self.owner(principal)
        return await self.repository.get(request_id, principal.user_id if mine else owner)

    async def decide(
        self,
        principal: Principal,
        request_id: UUID,
        version: int,
        support_version: int | None,
        approved: bool,
        reason: str | None = None,
    ):
        await self.require_manager(principal)
        return await self.repository.decide(
            request_id,
            version,
            support_version,
            principal.email,
            approved,
            None if approved else self.reason(reason or ""),
            self.calendar.today(),
        )

    async def cancel(self, principal: Principal, support_id: UUID, version: int, reason: str):
        await self.require_manager(principal)
        await self.repository.cancel(support_id, version, principal.email, self.reason(reason))

    async def history(self, principal: Principal, support_id: UUID):
        await self.require_manager(principal)
        return await self.repository.history(support_id)
