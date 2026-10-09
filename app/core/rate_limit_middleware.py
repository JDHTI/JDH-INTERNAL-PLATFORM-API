"""Rate limiting for every endpoint, by level (see ``rate_policy``).

It runs before routing and before the body is read, so a request over its budget costs a
Redis counter and nothing else. Each level has its own counter per client address: a screen
that fires many reads never uses up the budget of the changes.
"""

from __future__ import annotations

import logging

from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.core.errors import DependencyUnavailableError
from app.core.public_guard import is_public_post
from app.core.rate_limit import check_limit
from app.core.rate_policy import get_policy

logger = logging.getLogger(__name__)


class RateLimitMiddleware:
    """Pure ASGI middleware: look up the level of the endpoint and count the request."""

    def __init__(self, app: ASGIApp) -> None:
        """Wrap an application."""

        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """Admit or turn away a request; websockets and lifespan events pass untouched."""

        if scope["type"] != "http" or is_public_post(scope):
            # The public form has its own, stricter guard, which already counted it.
            await self.app(scope, receive, send)
            return
        prefix = scope["app"].state.settings.API_PREFIX
        level = get_policy(prefix).level_for(scope["method"], scope["path"])
        if level is None:
            await self.app(scope, receive, send)
            return
        try:
            result = await check_limit(Request(scope, receive), level)
        except DependencyUnavailableError as exc:
            await JSONResponse({"detail": str(exc)}, status_code=503)(scope, receive, send)
            return
        headers = {
            "X-RateLimit-Policy": level,
            "X-RateLimit-Limit": str(result.limit),
            "X-RateLimit-Remaining": str(result.remaining),
        }
        if not result.allowed:
            logger.warning("Rate limit %s reached on %s %s", level, scope["method"], scope["path"])
            await JSONResponse(
                {"detail": "Se alcanzó el límite de solicitudes. Intenta más tarde."},
                status_code=429,
                headers={**headers, "Retry-After": str(result.retry_after)},
            )(scope, receive, send)
            return

        async def send_with_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                message["headers"] = [
                    *message.get("headers", []),
                    *((k.lower().encode(), v.encode()) for k, v in headers.items()),
                ]
            await send(message)

        await self.app(scope, receive, send_with_headers)


__all__ = ["RateLimitMiddleware"]
