"""Framework-free application errors.

Domain and application layers raise these; ``app.core.error_handlers`` is the
only place that turns them into HTTP responses. ``status_code`` is a plain
integer so this module never imports the web framework.
"""

from __future__ import annotations


class ApplicationError(Exception):
    """Base error for expected business failures."""

    status_code: int = 400

    def __init__(self, detail: str) -> None:
        super().__init__(detail)
        self.detail = detail


class UnauthenticatedError(ApplicationError):
    status_code = 401


class ForbiddenError(ApplicationError):
    status_code = 403


class NotFoundError(ApplicationError):
    status_code = 404


class ConflictError(ApplicationError):
    status_code = 409


class PayloadTooLargeError(ApplicationError):
    status_code = 413


class UnsupportedMediaError(ApplicationError):
    status_code = 415


class InvalidInputError(ApplicationError):
    status_code = 422


class DependencyUnavailableError(ApplicationError):
    status_code = 503


__all__ = [
    "ApplicationError",
    "ConflictError",
    "DependencyUnavailableError",
    "ForbiddenError",
    "InvalidInputError",
    "NotFoundError",
    "PayloadTooLargeError",
    "UnauthenticatedError",
    "UnsupportedMediaError",
]
