"""HTTP middleware for correlation IDs and access timing logs."""

from __future__ import annotations

import time
from uuid import uuid4

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from app.core.context import request_id_context
from app.core.logging import logger


class RequestContextMiddleware(BaseHTTPMiddleware):
    """Attach a request ID to each request and response."""

    async def dispatch(self, request: Request, call_next) -> Response:
        request_id = request.headers.get("X-Request-ID", str(uuid4()))
        token = request_id_context.set(request_id)
        started = time.perf_counter()
        response: Response | None = None
        try:
            response = await call_next(request)
            return response
        finally:
            elapsed_ms = (time.perf_counter() - started) * 1000
            status_code = response.status_code if response is not None else 500
            logger.info(
                "%s %s -> %s (%.2f ms)",
                request.method,
                request.url.path,
                status_code,
                elapsed_ms,
            )
            if response is not None:
                response.headers["X-Request-ID"] = request_id
            request_id_context.reset(token)
