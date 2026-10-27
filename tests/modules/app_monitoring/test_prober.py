"""How an HTTP answer becomes a check."""

from __future__ import annotations

from uuid import uuid4

import httpx
import pytest

from app.modules.app_monitoring.domain.entities import Application, CheckStatus, Target
from app.modules.app_monitoring.infrastructure.prober import HttpProber, classify_health
from tests.modules.app_monitoring.conftest import NOW


def application(**values) -> Application:
    return Application(
        id=uuid4(),
        slug="intranet",
        name="Intranet",
        base_url="https://intranet.jdh.local",
        **values,
    )


def prober(handler) -> HttpProber:
    transport = httpx.MockTransport(handler)
    return HttpProber(
        http=httpx.AsyncClient(transport=transport),
        insecure_http=httpx.AsyncClient(transport=transport),
        clock=lambda: NOW,
    )


@pytest.mark.parametrize(
    ("body", "expected"),
    [
        ({"status": "ok"}, CheckStatus.UP),
        ({"status": "UP"}, CheckStatus.UP),
        ({"status": "degraded"}, CheckStatus.DEGRADED),
        ({"status": "not_ready"}, CheckStatus.DOWN),
        ({"status": "ready", "checks": {"db": {"status": "unavailable"}}}, CheckStatus.DEGRADED),
        ({"status": "ok", "components": {"redis": "ok"}}, CheckStatus.UP),
        ({"version": "1.0"}, None),
        (["ok"], None),
    ],
)
def test_health_bodies(body, expected) -> None:
    assert classify_health(body) is expected


async def test_a_healthy_api_is_up_with_its_body() -> None:
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        return httpx.Response(200, json={"status": "ok"})

    result = await prober(handler).probe(application(), Target.API)

    assert seen == ["https://intranet.jdh.local/api/health"]
    assert result.status is CheckStatus.UP and result.http_status == 200
    assert result.detail == {"status": "ok"} and result.checked_at == NOW


async def test_an_api_answering_500_or_reporting_down_is_down() -> None:
    error = await prober(lambda r: httpx.Response(503)).probe(application(), Target.API)
    declared = await prober(lambda r: httpx.Response(200, json={"status": "error"})).probe(
        application(), Target.API
    )

    assert error.status is CheckStatus.DOWN and error.error == "HTTP 503"
    assert declared.status is CheckStatus.DOWN


async def test_a_ui_redirecting_to_the_login_answers() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(302, headers={"location": "https://login.microsoftonline.com/x"})

    result = await prober(handler).probe(application(), Target.UI)

    assert result.status is CheckStatus.UP and result.http_status == 302
    assert result.detail == {"redirige_a": "https://login.microsoftonline.com/x"}


async def test_a_ui_answering_404_is_down() -> None:
    result = await prober(lambda r: httpx.Response(404)).probe(application(), Target.UI)

    assert result.status is CheckStatus.DOWN


async def test_a_slow_answer_is_degraded() -> None:
    result = await prober(lambda r: httpx.Response(200, json={"status": "ok"})).probe(
        application(slow_ms=-1), Target.API
    )

    assert result.status is CheckStatus.DEGRADED and result.error.startswith("Lento")


async def test_network_errors_are_down_results_not_exceptions() -> None:
    def refused(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("Name or service not known", request=request)

    def slow(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("timed out", request=request)

    refused_result = await prober(refused).probe(application(), Target.API)
    timeout_result = await prober(slow).probe(application(), Target.UI)

    assert refused_result.status is CheckStatus.DOWN and "ConnectError" in refused_result.error
    assert refused_result.http_status is None
    assert timeout_result.status is CheckStatus.DOWN and "timeout" in timeout_result.error


async def test_verify_tls_off_uses_the_insecure_client() -> None:
    secure = httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(500)))
    insecure = httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(200)))
    probe = HttpProber(http=secure, insecure_http=insecure, clock=lambda: NOW)

    result = await probe.probe(application(verify_tls=False), Target.UI)

    assert result.status is CheckStatus.UP
