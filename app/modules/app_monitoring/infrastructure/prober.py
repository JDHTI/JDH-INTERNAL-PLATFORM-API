"""HTTP checks against the monitored applications."""

from __future__ import annotations

import time
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

import httpx

from app.modules.app_monitoring.domain.entities import (
    Application,
    CheckResult,
    CheckStatus,
    Target,
)

# Words a health body uses in ``status`` (case-insensitive).
_HEALTHY = {"ok", "up", "healthy", "pass", "ready", "available"}
_DEGRADED = {"degraded", "warn", "warning", "partial"}
_BODY_LIMIT = 4000


def classify_health(body: Any) -> CheckStatus | None:
    """Status a JSON health body declares, or ``None`` when it declares none.

    Reads ``status`` at the top and, under ``checks``/``components``, flags any
    component that is not healthy as degraded: the API answers but part of it fails.
    """

    if not isinstance(body, dict):
        return None
    declared = body.get("status")
    status: CheckStatus | None = None
    if isinstance(declared, str):
        word = declared.strip().lower()
        if word in _HEALTHY:
            status = CheckStatus.UP
        elif word in _DEGRADED:
            status = CheckStatus.DEGRADED
        else:
            status = CheckStatus.DOWN
    components = body.get("checks") or body.get("components")
    if isinstance(components, dict) and status is CheckStatus.UP:
        for component in components.values():
            state = component.get("status") if isinstance(component, dict) else component
            if isinstance(state, str) and state.strip().lower() not in _HEALTHY:
                return CheckStatus.DEGRADED
    return status


def _detail(response: httpx.Response) -> dict[str, Any] | None:
    if "json" not in response.headers.get("content-type", ""):
        return None
    try:
        body = response.json()
    except ValueError:
        return None
    if not isinstance(body, dict) or len(response.content) > _BODY_LIMIT:
        return None
    return body


class HttpProber:
    """Request an application and turn the answer into a check.

    The API check is up on a 2xx whose JSON body (if it says) is healthy. The UI
    check is up on anything below 400 without following redirects, so a page that
    sends users to the login still counts as answering. Either is degraded when it
    takes longer than the application's ``slow_ms``.
    """

    def __init__(
        self,
        http: httpx.AsyncClient | None = None,
        insecure_http: httpx.AsyncClient | None = None,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        """Create the prober.

        Args:
            http (httpx.AsyncClient | None): Client that verifies certificates.
            insecure_http (httpx.AsyncClient | None): Client for ``verify_tls`` off.
            clock (Callable[[], datetime]): Current time, replaceable in tests.
        """

        # Built on the first check, so a process that never checks opens nothing.
        self._clients: dict[bool, httpx.AsyncClient] = {}
        if http is not None:
            self._clients[True] = http
        if insecure_http is not None:
            self._clients[False] = insecure_http
        self._clock = clock

    def _client(self, verify: bool) -> httpx.AsyncClient:
        if verify not in self._clients:
            self._clients[verify] = httpx.AsyncClient(
                headers={"User-Agent": "jdh-internal-platform-monitor/1.0"}, verify=verify
            )
        return self._clients[verify]

    async def close(self) -> None:
        """Release the HTTP connections."""

        for client in self._clients.values():
            await client.aclose()
        self._clients.clear()

    async def probe(self, application: Application, target: Target) -> CheckResult:
        """Run one check; a network failure is a ``down`` result, never an exception.

        Args:
            application (Application): What to check.
            target (Target): The health endpoint or the UI.

        Returns:
            CheckResult: The classified answer.
        """

        checked_at = self._clock()
        client = self._client(application.verify_tls)
        started = time.perf_counter()
        try:
            response = await client.get(
                application.url(target),
                timeout=application.timeout_s,
                follow_redirects=False,
            )
        except httpx.TimeoutException:
            return self._down(application, target, checked_at, started, "Sin respuesta (timeout)")
        except httpx.HTTPError as exc:
            message = f"{type(exc).__name__}: {exc}" if str(exc) else type(exc).__name__
            return self._down(application, target, checked_at, started, message)
        latency = round((time.perf_counter() - started) * 1000)
        detail = _detail(response) if target is Target.API else None
        status, error = self._classify(target, response, detail)
        if status is CheckStatus.UP and latency > application.slow_ms:
            status, error = (
                CheckStatus.DEGRADED,
                f"Lento: {latency} ms (umbral {application.slow_ms})",
            )
        if target is Target.UI and response.is_redirect:
            detail = {"redirige_a": response.headers.get("location")}
        return CheckResult(
            application_id=application.id,
            target=target,
            checked_at=checked_at,
            status=status,
            http_status=response.status_code,
            latency_ms=latency,
            error=error,
            detail=detail,
        )

    @staticmethod
    def _classify(
        target: Target, response: httpx.Response, detail: dict[str, Any] | None
    ) -> tuple[CheckStatus, str | None]:
        code = response.status_code
        if target is Target.UI:
            if code < 400:
                return CheckStatus.UP, None
            return CheckStatus.DOWN, f"HTTP {code}"
        if not 200 <= code < 300:
            return CheckStatus.DOWN, f"HTTP {code}"
        declared = classify_health(detail)
        if declared is CheckStatus.DOWN:
            return declared, f"El health reporta: {detail.get('status') if detail else '?'}"
        if declared is CheckStatus.DEGRADED:
            return declared, "El health reporta componentes con fallas"
        return CheckStatus.UP, None

    @staticmethod
    def _down(
        application: Application,
        target: Target,
        checked_at: datetime,
        started: float,
        error: str,
    ) -> CheckResult:
        return CheckResult(
            application_id=application.id,
            target=target,
            checked_at=checked_at,
            status=CheckStatus.DOWN,
            latency_ms=round((time.perf_counter() - started) * 1000),
            error=error,
        )
