"""Routing by road through openrouteservice (HeiGIT's hosted API, free plan).

Directions draw the road line; the optimization endpoint (VROOM) orders the stops. The free
plan answers 2,000 directions a day and 40 a minute, with up to 50 points per request; past
the quota it answers an error and never charges. Only the API calls it: a route is computed
once and stored.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from typing import Any

import httpx

from app.modules.territory.domain.entities import Leg, Point, RoadPath
from app.modules.territory.domain.ports import RoutingUnavailable

logger = logging.getLogger(__name__)

DIRECTIONS_URL = "https://api.heigit.org/openrouteservice/v2/directions/driving-car/geojson"
OPTIMIZATION_URL = "https://api.heigit.org/vroom/v0"
_PROFILE = "driving-car"


def _pair(point: Point) -> list[float]:
    return [point.longitude, point.latitude]


def _message(response: httpx.Response) -> str:
    status = response.status_code
    if status in (401, 403):
        return (
            "El servicio de ruteo rechazó la llave o se agotó la cuota del día; la ruta queda en "
            "línea recta"
        )
    if status == 429:
        return "Se superó el límite por minuto del servicio de ruteo; intenta de nuevo en un rato"
    detail = ""
    try:
        error = response.json().get("error")
        detail = error.get("message", "") if isinstance(error, dict) else str(error or "")
    except ValueError:
        detail = ""
    if "routable point" in detail.lower():
        return "Alguna parada no está cerca de una vía; la ruta queda en línea recta"
    return f"El servicio de ruteo respondió con error (HTTP {status}); la ruta queda en línea recta"


class OpenRouteServiceRouter:
    """Driving directions and stop ordering from openrouteservice."""

    def __init__(
        self,
        api_key: str,
        *,
        directions_url: str = DIRECTIONS_URL,
        optimization_url: str = OPTIMIZATION_URL,
        timeout_seconds: float = 20.0,
        provider: str = "openrouteservice",
        http: httpx.AsyncClient | None = None,
    ) -> None:
        """Create the router.

        Args:
            api_key (str): HeiGIT key; empty turns routing off (straight lines).
            directions_url (str): Directions endpoint returning GeoJSON.
            optimization_url (str): VROOM optimization endpoint.
            timeout_seconds (float): Per request.
            provider (str): ``openrouteservice``, or ``none`` to turn routing off.
            http (httpx.AsyncClient | None): Injected transport for tests.
        """

        self._key = (api_key or "").strip()
        self._on = provider.strip().lower() == "openrouteservice" and bool(self._key)
        self._directions_url = directions_url.rstrip("/")
        self._optimization_url = optimization_url.rstrip("/")
        self._timeout = timeout_seconds
        # Opened on the first route: most processes never compute one.
        self._http = http

    @property
    def enabled(self) -> bool:
        """Whether a key is configured and the provider is on."""

        return self._on

    async def close(self) -> None:
        """Release the HTTP connections, if a route ever opened them."""

        if self._http is not None:
            await self._http.aclose()
            self._http = None

    async def _post(self, url: str, body: dict[str, Any]) -> dict[str, Any]:
        if not self._on:
            raise RoutingUnavailable("El ruteo por calles no está configurado")
        if self._http is None:
            self._http = httpx.AsyncClient(timeout=self._timeout)
        try:
            response = await self._http.post(
                url,
                json=body,
                headers={
                    "Authorization": self._key,
                    "Accept": "application/json, application/geo+json",
                },
            )
        except httpx.TimeoutException as exc:
            raise RoutingUnavailable(
                "El servicio de ruteo no respondió a tiempo; la ruta queda en línea recta"
            ) from exc
        except httpx.HTTPError as exc:
            logger.warning("Routing request failed: %s", type(exc).__name__)
            raise RoutingUnavailable(
                "No se pudo contactar el servicio de ruteo; la ruta queda en línea recta"
            ) from exc
        if response.status_code >= 400:
            logger.warning("Routing answered HTTP %s", response.status_code)
            raise RoutingUnavailable(_message(response))
        try:
            return response.json()
        except ValueError as exc:
            raise RoutingUnavailable("El servicio de ruteo respondió algo ilegible") from exc

    async def optimize(self, start: Point, stops: Sequence[Point]) -> list[int]:
        """The visiting order that takes the least time.

        Args:
            start (Point): Where the vehicle starts.
            stops (Sequence[Point]): Points to visit.

        Returns:
            list[int]: Indexes of ``stops`` in visiting order; a stop the service could not
            place goes last.

        Raises:
            RoutingUnavailable: When the service does not answer.
        """

        body = {
            "jobs": [
                {"id": index + 1, "location": _pair(point)} for index, point in enumerate(stops)
            ],
            "vehicles": [{"id": 1, "profile": _PROFILE, "start": _pair(start)}],
        }
        data = await self._post(self._optimization_url, body)
        try:
            steps = data["routes"][0]["steps"]
            order = [
                int(step.get("job", step.get("id"))) - 1
                for step in steps
                if step.get("type") == "job"
            ]
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            raise RoutingUnavailable("El servicio de ruteo no devolvió un orden de visita") from exc
        order = [index for index in dict.fromkeys(order) if 0 <= index < len(stops)]
        return order + [index for index in range(len(stops)) if index not in order]

    async def directions(self, points: Sequence[Point]) -> RoadPath:
        """The road path through the points, in their order.

        Args:
            points (Sequence[Point]): Two or more points.

        Returns:
            RoadPath: The line, its distance and duration, and each leg.

        Raises:
            RoutingUnavailable: When the service does not answer.
        """

        body = {"coordinates": [_pair(point) for point in points], "instructions": False}
        data = await self._post(self._directions_url, body)
        try:
            feature = data["features"][0]
            coordinates = feature["geometry"]["coordinates"]
            properties = feature.get("properties") or {}
            summary = properties.get("summary") or {}
            legs = [
                Leg(distance_m=segment.get("distance"), duration_s=segment.get("duration"))
                for segment in properties.get("segments") or []
            ]
        except (KeyError, IndexError, TypeError) as exc:
            raise RoutingUnavailable("El servicio de ruteo no devolvió una ruta") from exc
        if len(coordinates) < 2:
            raise RoutingUnavailable("El servicio de ruteo devolvió una ruta vacía")
        return RoadPath(
            coordinates=[[float(lon), float(lat)] for lon, lat, *_ in coordinates],
            distance_m=float(summary.get("distance") or 0.0),
            duration_s=float(summary.get("duration") or 0.0),
            legs=legs,
        )


__all__ = ["DIRECTIONS_URL", "OPTIMIZATION_URL", "OpenRouteServiceRouter"]
