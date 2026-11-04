"""FastAPI dependencies that gate a workspace router or endpoint by module permission.

Writes are decided inside each service by its permission policy; these gates make
sure every workspace endpoint needs at least ``read`` of its module. Owner and admin
always pass, a moderator passes to read.

The area service is resolved from the application container of the request (not
with ``Provide``): the dependencies are built at import time by a factory, and a
wired closure would not see the provider overrides of the tests.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from fastapi import Depends, Request

from app.modules.areas.application.service import AreaService
from app.modules.areas.domain.catalog import READ
from app.modules.identity.domain.principal import Principal
from app.modules.identity.presentation.principal import get_current_principal

Gate = Callable[..., Awaitable[Principal]]


def _areas(request: Request) -> AreaService:
    return request.app.state.container.v1_container.area_service()


def require_permission(*modules: str, action: str = READ, area_slug: str | None = None) -> Gate:
    """A dependency that requires ``action`` on any of the modules.

    Args:
        *modules (str): Module keys; holding one of them is enough (a master that
            several modules read).
        action (str): ``read`` or ``manage``.
        area_slug (str | None): Only in this area; ``None`` accepts any area where
            the module exists.

    Returns:
        Gate: The dependency; it returns the caller.
    """

    if not modules:
        raise ValueError("require_permission needs at least one module")

    async def gate(
        request: Request,
        principal: Principal = Depends(get_current_principal),  # noqa: B008
    ) -> Principal:
        areas = _areas(request)
        for module in modules[:-1]:
            if await areas.allows(principal, area_slug, module, action):
                return principal
        await areas.require_permission(principal, area_slug, modules[-1], action)
        return principal

    gate.__name__ = f"require_{'_'.join(m.replace('-', '_') for m in modules)}_{action}"
    return gate


__all__ = ["require_permission"]
