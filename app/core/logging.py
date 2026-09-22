"""Application logging with development colors and request IDs."""

from __future__ import annotations

import logging
import sys
from typing import TYPE_CHECKING

import colorlog

from app.core.context import request_id_context

if TYPE_CHECKING:
    from app.core.config import Settings


class RequestIdFilter(logging.Filter):
    """Add the current correlation ID to every log record."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = request_id_context.get("-")
        return True


logger = logging.getLogger("internal_platform")


def configure_logging(app_settings: Settings) -> None:
    """Configure application and Uvicorn loggers for the process."""

    level = getattr(logging, app_settings.LOG_LEVEL.upper(), logging.INFO)
    use_colors = app_settings.ENVIRONMENT.value != "prod" and sys.stdout.isatty()
    log_format = (
        "%(log_color)s%(asctime)s | %(levelname)-8s | %(name)s | "
        "request_id=%(request_id)s | %(message)s%(reset)s"
        if use_colors
        else "%(asctime)s | %(levelname)-8s | %(name)s | request_id=%(request_id)s | %(message)s"
    )
    formatter = colorlog.ColoredFormatter(
        log_format,
        datefmt="%Y-%m-%dT%H:%M:%S%z",
        reset=use_colors,
        log_colors={
            "DEBUG": "cyan",
            "INFO": "green",
            "WARNING": "yellow",
            "ERROR": "red",
            "CRITICAL": "bold_red",
        },
    )
    handler = logging.StreamHandler(sys.stdout)
    handler.setLevel(level)
    handler.setFormatter(formatter)
    handler.addFilter(RequestIdFilter())

    root = logging.getLogger()
    root.handlers.clear()
    root.setLevel(level)
    root.addHandler(handler)

    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        uvicorn_logger = logging.getLogger(name)
        uvicorn_logger.handlers.clear()
        uvicorn_logger.propagate = True
        uvicorn_logger.setLevel(level)

    logger.setLevel(level)


__all__ = ["configure_logging", "logger"]
