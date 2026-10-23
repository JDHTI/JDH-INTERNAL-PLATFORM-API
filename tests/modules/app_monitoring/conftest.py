"""In-memory ports for the application monitoring module."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime

import pytest
from app.modules.app_monitoring.application.service import AppMonitoringService
from app.modules.app_monitoring.domain.entities import (
    Application,
    CheckResult,
    CheckStatus,
    Target,
)

from tests.modules.dwh_monitoring.conftest import TI_MEMBER, FakeAccess

NOW = datetime(2026, 10, 23, 15, 0, tzinfo=UTC)  # 10:00 in Bogota
__all__ = ["NOW", "TI_MEMBER", "FakeAccess", "FakeApplications", "FakeProber"]


class FakeApplications:
    """Applications and checks as plain dicts and lists."""

    def __init__(self) -> None:
        self.apps: dict = {}
        self.checks: list[CheckResult] = []
        self.purged_before: datetime | None = None

    async def list(self, only_enabled: bool = False) -> list[Application]:
        apps = sorted(self.apps.values(), key=lambda app: (app.sort_order, app.name))
        return [app for app in apps if app.enabled or not only_enabled]

    async def get(self, application_id):
        return self.apps.get(application_id)

    async def slug_taken(self, slug, exclude=None) -> bool:
        return any(app.slug == slug and app.id != exclude for app in self.apps.values())

    async def add(self, application):
        stored = replace(application, created_at=NOW, updated_at=NOW)
        self.apps[application.id] = stored
        return stored

    async def update(self, application):
        self.apps[application.id] = application
        return application

    async def delete(self, application_id) -> bool:
        self.checks = [c for c in self.checks if c.application_id != application_id]
        return self.apps.pop(application_id, None) is not None

    async def save_results(self, results) -> None:
        self.checks.extend(results)

    async def latest(self):
        newest: dict = {}
        for check in self.checks:
            key = (check.application_id, check.target)
            if key not in newest or check.checked_at >= newest[key].checked_at:
                newest[key] = check
        return newest

    async def summary_since(self, since):
        summary: dict = {}
        for check in self.checks:
            if check.checked_at < since:
                continue
            row = summary.setdefault(
                (check.application_id, check.target), {"total": 0, "up": 0, "lat": []}
            )
            row["total"] += 1
            row["up"] += check.status is not CheckStatus.DOWN
            if check.latency_ms is not None:
                row["lat"].append(check.latency_ms)
        for row in summary.values():
            lat = row.pop("lat")
            row["latencia_media_ms"] = sum(lat) / len(lat) if lat else None
        return summary

    async def history(self, application_id, target, start, end, page, size):
        rows = sorted(
            (
                c
                for c in self.checks
                if c.application_id == application_id
                and (target is None or c.target is target)
                and start <= c.checked_at < end
            ),
            key=lambda c: c.checked_at,
            reverse=True,
        )
        return rows[(page - 1) * size : page * size], len(rows)

    async def purge(self, before) -> int:
        self.purged_before = before
        kept = [c for c in self.checks if c.checked_at >= before]
        removed = len(self.checks) - len(kept)
        self.checks = kept
        return removed


class FakeProber:
    """Answers with a fixed status per application slug and target."""

    def __init__(self) -> None:
        self.answers: dict[tuple[str, Target], CheckStatus] = {}
        self.calls: list[tuple[str, Target]] = []

    async def probe(self, application: Application, target: Target) -> CheckResult:
        self.calls.append((application.slug, target))
        status = self.answers.get((application.slug, target), CheckStatus.UP)
        return CheckResult(
            application_id=application.id,
            target=target,
            checked_at=NOW,
            status=status,
            http_status=200 if status is not CheckStatus.DOWN else 503,
            latency_ms=120,
            error=None if status is CheckStatus.UP else "falla",
        )


@pytest.fixture
def apps() -> FakeApplications:
    return FakeApplications()


@pytest.fixture
def prober() -> FakeProber:
    return FakeProber()


@pytest.fixture
def service(apps, prober) -> AppMonitoringService:
    return AppMonitoringService(apps, prober, FakeAccess(), check_enabled=True, clock=lambda: NOW)
