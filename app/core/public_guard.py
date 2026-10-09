"""Guard for the public, unauthenticated forms (``POST {API_PREFIX}/v1/public/*``).

It runs before the request body is read, so a flood costs a Redis counter and not the
parsing of uploads: body size first, then the per-address limit, then one limit shared
by every visitor (a flood spread over many addresses), then the CAPTCHA when one is set.
"""

from __future__ import annotations

import logging
import re

import httpx
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from app.core.errors import DependencyUnavailableError
from app.core.rate_limit import check_limit, client_ip

logger = logging.getLogger(__name__)

CAPTCHA_HEADER = "X-Captcha-Token"
_CAPTCHA_TIMEOUT_SECONDS = 5.0


def is_public_post(scope: Scope) -> bool:
    """Whether the request is a ``POST`` to a public form (``{API_PREFIX}/v1/public/*``)."""

    if scope["type"] != "http" or scope["method"] != "POST":
        return False
    prefix = scope["app"].state.settings.API_PREFIX
    return re.match(rf"{re.escape(prefix)}/(v\d+/)?public/", scope["path"]) is not None


class PublicIntakeGuard:
    """Pure ASGI middleware: cheap checks first, and nothing reads the body before they pass."""

    def __init__(self, app: ASGIApp) -> None:
        """Wrap an application."""

        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """Admit or turn away a public POST; everything else passes untouched."""

        if not is_public_post(scope):
            await self.app(scope, receive, send)
            return
        request = Request(scope, receive)
        rejection = await self._rejection(request)
        if rejection is not None:
            await rejection(scope, receive, send)
            return
        await self.app(scope, receive, send)

    async def _rejection(self, request: Request) -> JSONResponse | None:
        settings = request.app.state.settings
        length = request.headers.get("content-length")
        limit_bytes = settings.PUBLIC_MAX_BODY_MB * 1024 * 1024
        if length is None or not length.isdigit():
            return _reply(411, "La solicitud debe indicar su tamaño.")
        if int(length) > limit_bytes:
            return _reply(
                413, f"La solicitud supera el máximo de {settings.PUBLIC_MAX_BODY_MB} MB."
            )
        try:
            for profile, shared in (("strict", False), ("public_global", True)):
                result = await check_limit(request, profile, shared=shared)
                if not result.allowed:
                    logger.warning(
                        "Public form limit %s reached (client %s)", profile, client_ip(request)
                    )
                    return _reply(
                        429,
                        "Se alcanzó el límite de solicitudes. Intenta más tarde.",
                        {
                            "Retry-After": str(result.retry_after),
                            "X-RateLimit-Limit": str(result.limit),
                            "X-RateLimit-Remaining": "0",
                        },
                    )
        except DependencyUnavailableError as exc:
            return _reply(503, str(exc))
        secret = settings.PUBLIC_CAPTCHA_SECRET.get_secret_value()
        if secret:
            return await self._captcha(request, secret)
        return None

    async def _captcha(self, request: Request, secret: str) -> JSONResponse | None:
        token = request.headers.get(CAPTCHA_HEADER, "").strip()
        if not token or len(token) > 4096:
            return _reply(403, "No pudimos verificar que eres una persona. Intenta de nuevo.")
        settings = request.app.state.settings
        try:
            async with httpx.AsyncClient(timeout=_CAPTCHA_TIMEOUT_SECONDS) as client:
                response = await client.post(
                    settings.PUBLIC_CAPTCHA_VERIFY_URL,
                    data={"secret": secret, "response": token, "remoteip": client_ip(request)},
                )
            verified = response.status_code == 200 and bool(response.json().get("success"))
        except (httpx.HTTPError, ValueError):
            logger.exception("CAPTCHA verification is unavailable")
            return _reply(503, "No pudimos verificar la solicitud ahora. Intenta más tarde.")
        if not verified:
            return _reply(403, "No pudimos verificar que eres una persona. Intenta de nuevo.")
        return None


def _reply(status_code: int, detail: str, headers: dict[str, str] | None = None) -> JSONResponse:
    return JSONResponse({"detail": detail}, status_code=status_code, headers=headers)


__all__ = ["CAPTCHA_HEADER", "PublicIntakeGuard", "is_public_post"]
