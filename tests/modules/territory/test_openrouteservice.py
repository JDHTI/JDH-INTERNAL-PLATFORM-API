"""The openrouteservice adapter against a mocked transport."""

from __future__ import annotations

import json

import httpx
import pytest

from app.modules.territory.domain.entities import Point
from app.modules.territory.domain.ports import RoutingUnavailable
from app.modules.territory.infrastructure.openrouteservice import (
    DIRECTIONS_URL,
    OPTIMIZATION_URL,
    OpenRouteServiceRouter,
)

A, B, C = Point(6.25, -75.6), Point(6.26, -75.59), Point(6.27, -75.58)


def router(handler, key: str = "secret") -> OpenRouteServiceRouter:
    return OpenRouteServiceRouter(
        key, http=httpx.AsyncClient(transport=httpx.MockTransport(handler))
    )


async def test_directions_send_lon_lat_and_read_the_line_and_legs() -> None:
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["auth"] = request.headers["Authorization"]
        seen["body"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={
                "features": [
                    {
                        "geometry": {
                            "coordinates": [[-75.6, 6.25], [-75.59, 6.26], [-75.58, 6.27]]
                        },
                        "properties": {
                            "summary": {"distance": 2500.4, "duration": 300.2},
                            "segments": [
                                {"distance": 1200.0, "duration": 140.0},
                                {"distance": 1300.4, "duration": 160.2},
                            ],
                        },
                    }
                ]
            },
        )

    path = await router(handler).directions([A, B, C])

    assert seen["url"] == DIRECTIONS_URL and seen["auth"] == "secret"
    assert seen["body"]["coordinates"][0] == [-75.6, 6.25]
    assert path.distance_m == 2500.4 and len(path.coordinates) == 3
    assert [leg.duration_s for leg in path.legs] == [140.0, 160.2]


async def test_optimization_returns_the_visiting_order() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == OPTIMIZATION_URL
        body = json.loads(request.content)
        assert body["vehicles"][0]["start"] == [-75.6, 6.25] and len(body["jobs"]) == 3
        steps = [
            {"type": "start"},
            {"type": "job", "job": 3},
            {"type": "job", "id": 1},
            {"type": "end"},
        ]
        return httpx.Response(200, json={"routes": [{"steps": steps}], "unassigned": [{"id": 2}]})

    order = await router(handler).optimize(A, [A, B, C])

    # Job 2 was not placed: it goes last.
    assert order == [2, 0, 1]


@pytest.mark.parametrize(
    ("status", "body", "message"),
    [
        (403, {"error": "Quota exceeded"}, "cuota"),
        (429, {"error": "Rate limit"}, "límite por minuto"),
        (404, {"error": {"code": 2010, "message": "Could not find routable point"}}, "vía"),
        (502, {}, "HTTP 502"),
    ],
)
async def test_errors_become_a_spanish_reason(status, body, message) -> None:
    with pytest.raises(RoutingUnavailable, match=message):
        await router(lambda request: httpx.Response(status, json=body)).directions([A, B])


async def test_timeouts_and_a_missing_key() -> None:
    def slow(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow", request=request)

    with pytest.raises(RoutingUnavailable, match="a tiempo"):
        await router(slow).directions([A, B])

    off = router(lambda request: httpx.Response(200), key="")
    assert not off.enabled
    with pytest.raises(RoutingUnavailable, match="no está configurado"):
        await off.directions([A, B])
    assert not OpenRouteServiceRouter("k", provider="none").enabled
