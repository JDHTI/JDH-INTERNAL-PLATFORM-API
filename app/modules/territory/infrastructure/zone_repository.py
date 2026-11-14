"""PostgreSQL store of the zones: official polygons and JDH's commercial zones."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any
from uuid import UUID

from sqlalchemy import (
    ColumnElement,
    case,
    delete,
    func,
    insert,
    literal_column,
    select,
    text,
    update,
)
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import InvalidInputError
from app.infrastructure.database import Database
from app.modules.territory.domain.entities import OfficialZoneData, Zone
from app.modules.territory.domain.vocabulary import COMMERCIAL, OFFICIAL
from app.modules.territory.infrastructure import geo
from app.modules.territory.infrastructure.models import GeoZoneModel as Z
from app.modules.territory.infrastructure.models import ProspectModel as P

_EMPTY_SHAPE = "La zona no tiene un área válida: dibuja al menos un polígono"


def _count() -> ColumnElement[int]:
    """Prospects of a zone: the ones placed in it (commercial), or inside it (official)."""

    placed = (
        select(func.count())
        .select_from(P)
        .where(P.commercial_zone_id == Z.id)
        .correlate(Z)
        .scalar_subquery()
    )
    inside = (
        select(func.count())
        .select_from(P)
        .where(func.ST_Covers(Z.geom, P.location))
        .correlate(Z)
        .scalar_subquery()
    )
    return case((Z.layer == COMMERCIAL, placed), else_=inside)


def _columns(simplify: bool) -> tuple[ColumnElement[Any], ...]:
    return (
        Z.id,
        Z.layer,
        Z.level,
        Z.name,
        Z.code,
        Z.parent_id,
        Z.color,
        Z.source,
        _count().label("prospect_count"),
        geo.as_geojson(Z.geom, simplify).label("geojson"),
    )


def _zone(row: Any) -> Zone:
    return Zone(
        id=row.id,
        layer=row.layer,
        level=row.level,
        name=row.name,
        code=row.code,
        parent_id=row.parent_id,
        color=row.color,
        source=row.source,
        prospect_count=row.prospect_count or 0,
        geometry=geo.parse_geojson(row.geojson) or {},
    )


async def _check_not_empty(session: AsyncSession, zone_id: UUID) -> None:
    empty = await session.scalar(select(func.ST_IsEmpty(Z.geom)).where(Z.id == zone_id))
    if empty:
        await session.rollback()
        raise InvalidInputError(_EMPTY_SHAPE)


class SqlAlchemyZoneRepository:
    """Zones as PostGIS multipolygons; the commercial ones place the prospects."""

    def __init__(self, database: Database) -> None:
        """Create the repository.

        Args:
            database (Database): Application database.
        """

        self.database = database

    async def list(self, layer: str, level: str | None, simplify: bool) -> list[Zone]:
        """Zones of a layer, largest first so the small ones paint on top.

        Args:
            layer (str): ``official`` or ``commercial``.
            level (str | None): Only this level.
            simplify (bool): Lighter geometry, for painting.

        Returns:
            list[Zone]: The zones with their prospect count (commercial only).
        """

        statement = select(*_columns(simplify)).where(Z.layer == layer)
        if level:
            statement = statement.where(Z.level == level)
        statement = statement.order_by(func.ST_Area(Z.geom).desc(), Z.name)
        async with self.database.session_factory() as session:
            return [_zone(row) for row in await session.execute(statement)]

    async def get(self, zone_id: UUID) -> Zone | None:
        """One zone with its full geometry.

        Args:
            zone_id (UUID): Its id.

        Returns:
            Zone | None: The zone, or ``None``.
        """

        async with self.database.session_factory() as session:
            row = (
                await session.execute(select(*_columns(False)).where(Z.id == zone_id))
            ).one_or_none()
            return _zone(row) if row else None

    async def name_taken(self, name: str, exclude_id: UUID | None = None) -> bool:
        """Whether a commercial zone already has this name (any case).

        Args:
            name (str): The name.
            exclude_id (UUID | None): The zone being renamed.

        Returns:
            bool: ``True`` when taken.
        """

        statement = select(Z.id).where(
            Z.layer == COMMERCIAL, func.lower(Z.name) == name.strip().lower()
        )
        if exclude_id is not None:
            statement = statement.where(Z.id != exclude_id)
        async with self.database.session_factory() as session:
            return await session.scalar(statement.limit(1)) is not None

    async def create(
        self,
        name: str,
        color: str,
        geometry: dict[str, Any] | None,
        official_ids: Sequence[UUID],
        created_by_id: UUID | None,
    ) -> Zone:
        """Create a commercial zone from a drawn shape or the union of official zones.

        The geometry is repaired; prospects are placed again.

        Args:
            name (str): Its name.
            color (str): Palette key.
            geometry (dict[str, Any] | None): GeoJSON polygon, when drawn.
            official_ids (Sequence[UUID]): Official zones to join, when adopted.
            created_by_id (UUID | None): Who created it.

        Returns:
            Zone: The new zone.

        Raises:
            InvalidInputError: When the shape is empty once repaired.
        """

        if geometry is not None:
            shape = geo.repaired_area(geo.from_geojson(geometry))
        else:
            shape = (
                select(geo.repaired_area(func.ST_Union(Z.geom)))
                .where(Z.layer == OFFICIAL, Z.id.in_(list(official_ids)))
                .scalar_subquery()
            )
        async with self.database.session_factory() as session:
            try:
                zone_id = await session.scalar(
                    insert(Z)
                    .values(
                        layer=COMMERCIAL,
                        level="custom",
                        name=name.strip(),
                        color=color,
                        geom=shape,
                        created_by_id=created_by_id,
                    )
                    .returning(Z.id)
                )
            except DBAPIError as exc:
                await session.rollback()
                raise InvalidInputError(_EMPTY_SHAPE) from exc
            if zone_id is None:
                raise RuntimeError("geo_zones did not return the new id")
            await _check_not_empty(session, zone_id)
            await geo.place_prospects(session)
            await session.commit()
        zone = await self.get(zone_id)
        if zone is None:
            raise RuntimeError("the new zone is missing")
        return zone

    async def update(
        self,
        zone_id: UUID,
        name: str | None,
        color: str | None,
        geometry: dict[str, Any] | None,
    ) -> Zone | None:
        """Change a commercial zone; a new shape places the prospects again.

        Args:
            zone_id (UUID): The zone.
            name (str | None): New name, or keep.
            color (str | None): New color, or keep.
            geometry (dict[str, Any] | None): New shape, or keep.

        Returns:
            Zone | None: The zone, or ``None`` when missing.

        Raises:
            InvalidInputError: When the shape is empty once repaired.
        """

        values: dict[str, Any] = {}
        if name is not None:
            values["name"] = name.strip()
        if color is not None:
            values["color"] = color
        if geometry is not None:
            values["geom"] = geo.repaired_area(geo.from_geojson(geometry))
        async with self.database.session_factory() as session:
            if values:
                try:
                    found = await session.scalar(
                        update(Z)
                        .where(Z.id == zone_id, Z.layer == COMMERCIAL)
                        .values(**values)
                        .returning(Z.id)
                    )
                except DBAPIError as exc:
                    await session.rollback()
                    raise InvalidInputError(_EMPTY_SHAPE) from exc
                if found is None:
                    return None
                if geometry is not None:
                    await _check_not_empty(session, zone_id)
                    await geo.place_prospects(session)
                await session.commit()
        return await self.get(zone_id)

    async def delete(self, zone_id: UUID) -> bool:
        """Delete a commercial zone; its prospects are placed again.

        Args:
            zone_id (UUID): The zone.

        Returns:
            bool: ``True`` when it existed.
        """

        async with self.database.session_factory() as session:
            found = await session.scalar(
                delete(Z).where(Z.id == zone_id, Z.layer == COMMERCIAL).returning(Z.id)
            )
            if found is None:
                return False
            # The foreign key left them without a zone; another one may cover them.
            await geo.place_prospects(session, P.commercial_zone_id.is_(None))
            await session.commit()
            return True

    async def official_count(self, zone_ids: Sequence[UUID]) -> int:
        """How many of these ids are official zones.

        Args:
            zone_ids (Sequence[UUID]): Ids to check.

        Returns:
            int: The count.
        """

        if not zone_ids:
            return 0
        async with self.database.session_factory() as session:
            count = await session.scalar(
                select(func.count())
                .select_from(Z)
                .where(Z.layer == OFFICIAL, Z.id.in_(list(zone_ids)))
            )
            return count or 0

    async def upsert_official(
        self, items: Sequence[OfficialZoneData], source: str | None
    ) -> tuple[int, int]:
        """Create or replace official zones by level and code.

        Args:
            items (Sequence[OfficialZoneData]): The polygons.
            source (str | None): Where they come from.

        Returns:
            tuple[int, int]: Created and replaced.
        """

        created = replaced = 0
        async with self.database.session_factory() as session:
            for item in items:
                statement = pg_insert(Z).values(
                    layer=OFFICIAL,
                    level=item.level,
                    name=item.name[:160],
                    code=item.code[:40],
                    source=source,
                    geom=geo.repaired_area(geo.from_geojson(item.geometry)),
                )
                inserted = await session.scalar(
                    statement.on_conflict_do_update(
                        index_elements=[Z.level, Z.code],
                        # A literal, not a bound value: from the sixth run of the prepared
                        # statement PostgreSQL plans it generically and could no longer match
                        # the partial unique index.
                        index_where=text("layer = 'official'"),
                        set_={
                            "name": statement.excluded.name,
                            "geom": statement.excluded.geom,
                            "source": statement.excluded.source,
                            "updated_at": func.now(),
                        },
                    ).returning(literal_column("xmax = 0"))
                )
                if inserted:
                    created += 1
                else:
                    replaced += 1
            await session.commit()
        return created, replaced


__all__ = ["SqlAlchemyZoneRepository"]
