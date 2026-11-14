"""Zones use cases: JDH's commercial zones (drawn or adopted) and the official reference layer."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any
from uuid import UUID

from app.core.errors import ConflictError, InvalidInputError, NotFoundError
from app.modules.identity.domain.principal import Principal
from app.modules.territory.domain.entities import OfficialZoneData, Zone
from app.modules.territory.domain.geometry import check_area
from app.modules.territory.domain.ports import ManagerPolicy, ZoneRepository
from app.modules.territory.domain.vocabulary import (
    COMMERCIAL,
    ZONE_COLORS,
    ZONE_LAYERS,
    ZONE_LEVELS,
)

MAX_ADOPTED = 200


def _name(name: str) -> str:
    cleaned = " ".join((name or "").split())
    if not cleaned or len(cleaned) > 160:
        raise InvalidInputError("El nombre de la zona debe tener entre 1 y 160 caracteres")
    return cleaned


def _color(color: str) -> str:
    if color not in ZONE_COLORS:
        raise InvalidInputError(f"Color desconocido: {color}")
    return color


class ZoneService:
    """Anyone with ``read`` sees the zones; ``manage`` creates, changes and deletes them."""

    def __init__(self, repository: ZoneRepository, managers: ManagerPolicy) -> None:
        """Create the service.

        Args:
            repository (ZoneRepository): Zones storage.
            managers (ManagerPolicy): Who manages ``mapa-comercial``.
        """

        self.repository = repository
        self.managers = managers

    async def list(self, layer: str, level: str | None, simplify: bool) -> list[Zone]:
        """The zones of a layer.

        Args:
            layer (str): ``commercial`` or ``official``.
            level (str | None): Only this level.
            simplify (bool): Lighter geometry, for painting.

        Returns:
            list[Zone]: Largest first.

        Raises:
            InvalidInputError: With an unknown layer or level.
        """

        if layer not in ZONE_LAYERS:
            raise InvalidInputError(f"Capa desconocida: {layer}")
        if level is not None and level not in ZONE_LEVELS:
            raise InvalidInputError(f"Nivel desconocido: {level}")
        return await self.repository.list(layer, level, simplify)

    async def get(self, zone_id: UUID) -> Zone:
        """One zone with its full geometry.

        Args:
            zone_id (UUID): Its id.

        Returns:
            Zone: The zone.

        Raises:
            NotFoundError: When it does not exist.
        """

        zone = await self.repository.get(zone_id)
        if zone is None:
            raise NotFoundError("Zona no encontrada")
        return zone

    async def _commercial(self, zone_id: UUID) -> Zone:
        zone = await self.get(zone_id)
        if zone.layer != COMMERCIAL:
            raise InvalidInputError(
                "Las zonas oficiales no se editan: adóptala como zona comercial"
            )
        return zone

    async def _unique(self, name: str, exclude_id: UUID | None = None) -> None:
        if await self.repository.name_taken(name, exclude_id):
            raise ConflictError(f"Ya hay una zona comercial llamada {name}")

    async def create(
        self, principal: Principal, name: str, color: str, geometry: dict[str, Any]
    ) -> Zone:
        """Create a commercial zone from a drawn polygon (``manage``).

        Args:
            principal (Principal): The caller.
            name (str): Its name, unique among commercial zones.
            color (str): Palette key.
            geometry (dict[str, Any]): GeoJSON ``Polygon`` or ``MultiPolygon``.

        Returns:
            Zone: The new zone; its prospects are placed in it.

        Raises:
            ForbiddenError: Without ``manage``.
            ConflictError: When the name is taken.
            InvalidInputError: With a bad name, color or shape.
        """

        await self.managers.require(principal)
        name, color = _name(name), _color(color)
        shape = check_area(geometry)
        await self._unique(name)
        return await self.repository.create(name, color, shape, (), principal.user_id)

    async def adopt(
        self, principal: Principal, name: str, color: str, official_ids: Sequence[UUID]
    ) -> Zone:
        """Create a commercial zone joining official ones (``manage``).

        Args:
            principal (Principal): The caller.
            name (str): Its name, unique among commercial zones.
            color (str): Palette key.
            official_ids (Sequence[UUID]): Official zones to join.

        Returns:
            Zone: The new zone.

        Raises:
            ForbiddenError: Without ``manage``.
            ConflictError: When the name is taken.
            InvalidInputError: With no ids, too many or one that is not official.
        """

        await self.managers.require(principal)
        name, color = _name(name), _color(color)
        ids = list(dict.fromkeys(official_ids))
        if not ids or len(ids) > MAX_ADOPTED:
            raise InvalidInputError(f"Elige entre 1 y {MAX_ADOPTED} zonas oficiales")
        if await self.repository.official_count(ids) != len(ids):
            raise InvalidInputError("Alguna de las zonas elegidas no existe o no es oficial")
        await self._unique(name)
        return await self.repository.create(name, color, None, ids, principal.user_id)

    async def update(
        self,
        principal: Principal,
        zone_id: UUID,
        *,
        name: str | None = None,
        color: str | None = None,
        geometry: dict[str, Any] | None = None,
    ) -> Zone:
        """Rename, recolor or reshape a commercial zone (``manage``).

        Args:
            principal (Principal): The caller.
            zone_id (UUID): The zone.
            name (str | None): New name.
            color (str | None): New color.
            geometry (dict[str, Any] | None): New shape; places the prospects again.

        Returns:
            Zone: The zone after the change.

        Raises:
            ForbiddenError: Without ``manage``.
            NotFoundError: When it does not exist.
            InvalidInputError: For an official zone or bad values.
            ConflictError: When the new name is taken.
        """

        await self.managers.require(principal)
        await self._commercial(zone_id)
        if name is not None:
            name = _name(name)
            await self._unique(name, zone_id)
        if color is not None:
            color = _color(color)
        shape = check_area(geometry) if geometry is not None else None
        zone = await self.repository.update(zone_id, name, color, shape)
        if zone is None:
            raise NotFoundError("Zona no encontrada")
        return zone

    async def delete(self, principal: Principal, zone_id: UUID) -> None:
        """Delete a commercial zone (``manage``); its prospects are placed again.

        Args:
            principal (Principal): The caller.
            zone_id (UUID): The zone.

        Raises:
            ForbiddenError: Without ``manage``.
            NotFoundError: When it does not exist.
            InvalidInputError: For an official zone.
        """

        await self.managers.require(principal)
        await self._commercial(zone_id)
        if not await self.repository.delete(zone_id):
            raise NotFoundError("Zona no encontrada")

    async def load_official(
        self, items: Sequence[OfficialZoneData], source: str | None
    ) -> tuple[int, int]:
        """Create or replace official polygons by level and code (the import script).

        Args:
            items (Sequence[OfficialZoneData]): The polygons.
            source (str | None): Where they come from.

        Returns:
            tuple[int, int]: Created and replaced.

        Raises:
            InvalidInputError: With an unknown level, a repeated code or a bad shape.
        """

        seen: set[tuple[str, str]] = set()
        checked = []
        for item in items:
            if item.level not in ZONE_LEVELS or item.level == "custom":
                raise InvalidInputError(f"Nivel oficial desconocido: {item.level}")
            key = (item.level, item.code)
            if key in seen:
                raise InvalidInputError(f"El código {item.code} se repite en el nivel {item.level}")
            seen.add(key)
            checked.append(
                OfficialZoneData(
                    level=item.level,
                    name=_name(item.name),
                    code=item.code,
                    geometry=check_area(item.geometry, max_vertices=500_000),
                )
            )
        return await self.repository.upsert_official(checked, source)


__all__ = ["MAX_ADOPTED", "ZoneService"]
