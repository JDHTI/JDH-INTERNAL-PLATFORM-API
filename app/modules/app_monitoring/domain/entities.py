"""Applications the TI area monitors and the checks run against them."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Any
from uuid import UUID


class Target(StrEnum):
    """What a check hits: the health endpoint or the page users open."""

    API = "api"
    UI = "ui"


class CheckStatus(StrEnum):
    """Outcome of one check, worst last."""

    UP = "up"
    # Answers, but slower than its threshold or its health body reports a problem.
    DEGRADED = "degraded"
    DOWN = "down"


_SEVERITY = {CheckStatus.UP: 0, CheckStatus.DEGRADED: 1, CheckStatus.DOWN: 2}


def worst(statuses: list[CheckStatus | None]) -> CheckStatus | None:
    """The most severe of the known statuses; ``None`` when none is known."""

    known = [status for status in statuses if status is not None]
    return max(known, key=_SEVERITY.__getitem__, default=None)


@dataclass
class Application:
    """A platform registered for monitoring, e.g. ``https://intranet.jdh.com.co``.

    The API check reads ``base_url + health_path``; the UI check reads
    ``base_url + ui_path`` when ``check_ui`` is on.
    """

    id: UUID
    slug: str
    name: str
    base_url: str
    description: str | None = None
    health_path: str = "/api/health"
    ui_path: str = "/"
    check_ui: bool = True
    enabled: bool = True
    verify_tls: bool = True
    timeout_s: float = 10.0
    slow_ms: int = 2000
    sort_order: int = 0
    created_at: datetime | None = None
    updated_at: datetime | None = None

    def url(self, target: Target) -> str:
        """Absolute URL a check of ``target`` requests."""

        path = self.health_path if target is Target.API else self.ui_path
        return self.base_url.rstrip("/") + "/" + path.lstrip("/")


@dataclass(frozen=True)
class CheckResult:
    """One request against an application."""

    application_id: UUID
    target: Target
    checked_at: datetime
    status: CheckStatus
    http_status: int | None = None
    latency_ms: int | None = None
    error: str | None = None
    detail: dict[str, Any] | None = None
