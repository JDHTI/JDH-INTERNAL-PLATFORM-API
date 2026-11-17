"""HTTP translation of application errors."""

from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse

from app.core.errors import ApplicationError, UnauthenticatedError


def register_error_handlers(app: FastAPI) -> None:
    """Answer every ApplicationError with its status code and a JSON detail."""

    @app.exception_handler(ApplicationError)
    async def _application_error(_: Request, exc: ApplicationError) -> JSONResponse:
        headers = {"WWW-Authenticate": "Bearer"} if isinstance(exc, UnauthenticatedError) else None
        return JSONResponse(
            status_code=exc.status_code,
            content={**jsonable_encoder(exc.extra), "detail": exc.detail},
            headers=headers,
        )


__all__ = ["register_error_handlers"]
