"""PostgreSQL store of the prospects and their loads."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import asdict
from typing import Any
from uuid import UUID

from sqlalchemy import ColumnElement, Select, case, func, insert, or_, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.infrastructure.database import Database
from app.modules.territory.domain.contacts import Phone
from app.modules.territory.domain.entities import (
    FacetCount,
    LoadCounts,
    MapPoint,
    Point,
    Prospect,
    ProspectChange,
    ProspectData,
    ProspectFacets,
    ProspectFilters,
    ProspectLoad,
    ProspectPage,
    ZoneRef,
)
from app.modules.territory.domain.vocabulary import MANUAL, PENDING
from app.modules.territory.infrastructure import geo
from app.modules.territory.infrastructure.models import GeoZoneModel as Z
from app.modules.territory.infrastructure.models import ProspectLoadModel as L
from app.modules.territory.infrastructure.models import ProspectModel as P

# Rows per INSERT: asyncpg takes at most 32,767 parameters per statement.
_CHUNK = 500

_DATA_COLUMNS = (
    P.external_id,
    P.source_zone,
    P.name,
    P.category,
    P.is_former_customer,
    P.last_purchase_month,
    P.address,
    P.sector,
    P.area_status,
    P.phone_raw,
    P.email_raw,
    P.phones,
    P.emails,
    P.website,
    P.observation,
    P.location_quality,
    func.ST_Y(P.location).label("latitude"),
    func.ST_X(P.location).label("longitude"),
)
_PROSPECT_COLUMNS = (
    P.id,
    *_DATA_COLUMNS,
    P.visit_status,
    P.visit_note,
    P.visited_at,
    P.load_id,
    P.created_at,
    P.updated_at,
    Z.id.label("zone_id"),
    Z.name.label("zone_name"),
    Z.color.label("zone_color"),
)


def _data(row: Any) -> ProspectData:
    location = (
        Point(latitude=row.latitude, longitude=row.longitude)
        if row.latitude is not None and row.longitude is not None
        else None
    )
    return ProspectData(
        external_id=row.external_id,
        source_zone=row.source_zone,
        name=row.name,
        category=row.category,
        is_former_customer=row.is_former_customer,
        last_purchase_month=row.last_purchase_month,
        address=row.address,
        sector=row.sector,
        area_status=row.area_status,
        phone_raw=row.phone_raw,
        email_raw=row.email_raw,
        phones=tuple(Phone(**item) for item in row.phones or ()),
        emails=tuple(row.emails or ()),
        website=row.website,
        observation=row.observation,
        location=location,
        location_quality=row.location_quality,
    )


def _prospect(row: Any) -> Prospect:
    return Prospect(
        id=row.id,
        data=_data(row),
        commercial_zone=(
            ZoneRef(id=row.zone_id, name=row.zone_name, color=row.zone_color)
            if row.zone_id
            else None
        ),
        visit_status=row.visit_status,
        visit_note=row.visit_note,
        visited_at=row.visited_at,
        load_id=row.load_id,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _values(data: ProspectData, load_id: UUID) -> dict[str, Any]:
    return {
        "external_id": data.external_id,
        "source_zone": data.source_zone,
        "name": data.name,
        "category": data.category,
        "is_former_customer": data.is_former_customer,
        "last_purchase_month": data.last_purchase_month,
        "address": data.address,
        "sector": data.sector,
        "area_status": data.area_status,
        "phone_raw": data.phone_raw,
        "email_raw": data.email_raw,
        "phones": [asdict(phone) for phone in data.phones],
        "emails": list(data.emails),
        "website": data.website,
        "observation": data.observation,
        "location": geo.point(data.location) if data.location else None,
        "location_quality": data.location_quality,
        "load_id": load_id,
    }


def _like(text: str) -> str:
    escaped = text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def _conditions(filters: ProspectFilters) -> list[ColumnElement[bool]]:
    conditions: list[ColumnElement[bool]] = []
    if filters.source_zone:
        conditions.append(P.source_zone == filters.source_zone)
    if filters.category:
        conditions.append(P.category == filters.category)
    if filters.zone_id:
        conditions.append(P.commercial_zone_id == filters.zone_id)
    if filters.former is not None:
        conditions.append(P.is_former_customer.is_(filters.former))
    if filters.visit_status:
        conditions.append(P.visit_status == filters.visit_status)
    if filters.located is not None:
        conditions.append(P.location.is_not(None) if filters.located else P.location.is_(None))
    if filters.q and filters.q.strip():
        pattern = _like(filters.q.strip())
        conditions.append(
            or_(
                P.name.ilike(pattern, escape="\\"),
                P.address.ilike(pattern, escape="\\"),
                P.external_id.ilike(pattern, escape="\\"),
            )
        )
    return conditions


def _with_zone(statement: Select[Any]) -> Select[Any]:
    return statement.select_from(P).outerjoin(Z, Z.id == P.commercial_zone_id)


class SqlAlchemyProspectRepository:
    """Prospects keyed by the file's ``ID``, with their point and commercial zone."""

    def __init__(self, database: Database) -> None:
        """Create the repository.

        Args:
            database (Database): Application database.
        """

        self.database = database

    async def stored(self, external_ids: Sequence[str]) -> dict[str, ProspectData]:
        """The stored data of the prospects with these file ids.

        Args:
            external_ids (Sequence[str]): ``ID`` column values.

        Returns:
            dict[str, ProspectData]: By file id; missing ids are new.
        """

        found: dict[str, ProspectData] = {}
        async with self.database.session_factory() as session:
            for start in range(0, len(external_ids), 5000):
                chunk = list(external_ids[start : start + 5000])
                rows = await session.execute(select(*_DATA_COLUMNS).where(P.external_id.in_(chunk)))
                for row in rows:
                    found[row.external_id] = _data(row)
        return found

    async def apply_load(
        self,
        file_name: str,
        rows: Sequence[ProspectData],
        counts: LoadCounts,
        loaded_by_id: UUID | None,
        loaded_by: str | None,
    ) -> UUID:
        """Store a load in one transaction and place the prospects in their zones.

        A point moved by hand (``manual``) and the visit result are kept.

        Args:
            file_name (str): Uploaded file name.
            rows (Sequence[ProspectData]): Every row of the file.
            counts (LoadCounts): Created, updated and unchanged, for the trace.
            loaded_by_id (UUID | None): Who loaded it.
            loaded_by (str | None): Their e-mail, for the trace.

        Returns:
            UUID: The load id.
        """

        async with self.database.session_factory() as session:
            load_id = await session.scalar(
                insert(L)
                .values(
                    file_name=file_name[:255],
                    rows_read=len(rows),
                    created_count=counts.created,
                    updated_count=counts.updated,
                    unchanged_count=counts.unchanged,
                    loaded_by_id=loaded_by_id,
                    loaded_by=loaded_by,
                )
                .returning(L.id)
            )
            if load_id is None:
                raise RuntimeError("prospect_loads did not return the new id")
            for start in range(0, len(rows), _CHUNK):
                statement = pg_insert(P).values(
                    [_values(data, load_id) for data in rows[start : start + _CHUNK]]
                )
                kept_by_hand = P.location_quality == MANUAL
                incoming = statement.excluded
                await session.execute(
                    statement.on_conflict_do_update(
                        constraint="uq_prospects_external_id",
                        set_={
                            "source_zone": incoming.source_zone,
                            "name": incoming.name,
                            "category": incoming.category,
                            "is_former_customer": incoming.is_former_customer,
                            "last_purchase_month": incoming.last_purchase_month,
                            "address": incoming.address,
                            "sector": incoming.sector,
                            "area_status": incoming.area_status,
                            "phone_raw": incoming.phone_raw,
                            "email_raw": incoming.email_raw,
                            "phones": incoming.phones,
                            "emails": incoming.emails,
                            "website": incoming.website,
                            "observation": incoming.observation,
                            "location": case((kept_by_hand, P.location), else_=incoming.location),
                            "location_quality": case(
                                (kept_by_hand, P.location_quality),
                                else_=incoming.location_quality,
                            ),
                            "load_id": incoming.load_id,
                            "updated_at": func.now(),
                        },
                    )
                )
            await geo.place_prospects(session)
            await session.commit()
            return load_id

    async def map_points(self, filters: ProspectFilters) -> list[MapPoint]:
        """Every located prospect that matches the filters.

        Args:
            filters (ProspectFilters): Filters of the map.

        Returns:
            list[MapPoint]: Light rows for the map.
        """

        statement = (
            select(
                P.id,
                P.external_id,
                P.name,
                P.category,
                P.source_zone,
                P.sector,
                P.is_former_customer,
                P.location_quality,
                P.commercial_zone_id,
                P.visit_status,
                func.ST_Y(P.location).label("latitude"),
                func.ST_X(P.location).label("longitude"),
            )
            .where(P.location.is_not(None), *_conditions(filters))
            .order_by(P.name, P.id)
        )
        async with self.database.session_factory() as session:
            rows = await session.execute(statement)
            return [
                MapPoint(
                    id=row.id,
                    external_id=row.external_id,
                    name=row.name,
                    category=row.category,
                    source_zone=row.source_zone,
                    sector=row.sector,
                    is_former_customer=row.is_former_customer,
                    location_quality=row.location_quality,
                    commercial_zone_id=row.commercial_zone_id,
                    visit_status=row.visit_status,
                    location=Point(latitude=row.latitude, longitude=row.longitude),
                )
                for row in rows
            ]

    async def page(self, filters: ProspectFilters, page: int, size: int) -> ProspectPage:
        """A page of prospects ordered by name.

        Args:
            filters (ProspectFilters): Filters of the list.
            page (int): 1-based page.
            size (int): Page size.

        Returns:
            ProspectPage: The page and the total.
        """

        conditions = _conditions(filters)
        async with self.database.session_factory() as session:
            total = await session.scalar(select(func.count()).select_from(P).where(*conditions))
            rows = await session.execute(
                _with_zone(select(*_PROSPECT_COLUMNS))
                .where(*conditions)
                .order_by(P.name, P.external_id)
                .limit(size)
                .offset((page - 1) * size)
            )
            items = [_prospect(row) for row in rows]
        return ProspectPage(items=items, total=total or 0, page=page, size=size)

    async def facets(self) -> ProspectFacets:
        """Zones and categories with their counts, and how many are located.

        Returns:
            ProspectFacets: Values for the filters.
        """

        async with self.database.session_factory() as session:
            zones = await session.execute(
                select(P.source_zone, func.count()).group_by(P.source_zone).order_by(P.source_zone)
            )
            categories = await session.execute(
                select(P.category, func.count())
                .where(P.category.is_not(None))
                .group_by(P.category)
                .order_by(P.category)
            )
            totals = (
                await session.execute(select(func.count(), func.count(P.location)).select_from(P))
            ).one()
        total, located = totals[0], totals[1]
        return ProspectFacets(
            source_zones=[FacetCount(value=value, count=count) for value, count in zones],
            categories=[FacetCount(value=value, count=count) for value, count in categories],
            total=total,
            located=located,
            unlocated=total - located,
        )

    async def get(self, prospect_id: UUID) -> Prospect | None:
        """One prospect.

        Args:
            prospect_id (UUID): Its id.

        Returns:
            Prospect | None: The prospect, or ``None``.
        """

        async with self.database.session_factory() as session:
            row = (
                await session.execute(
                    _with_zone(select(*_PROSPECT_COLUMNS)).where(P.id == prospect_id)
                )
            ).one_or_none()
            return _prospect(row) if row else None

    async def get_many(self, prospect_ids: Sequence[UUID]) -> list[Prospect]:
        """Several prospects, in no particular order.

        Args:
            prospect_ids (Sequence[UUID]): Their ids.

        Returns:
            list[Prospect]: The ones that exist.
        """

        if not prospect_ids:
            return []
        async with self.database.session_factory() as session:
            rows = await session.execute(
                _with_zone(select(*_PROSPECT_COLUMNS)).where(P.id.in_(list(prospect_ids)))
            )
            return [_prospect(row) for row in rows]

    async def update(self, prospect_id: UUID, change: ProspectChange) -> Prospect | None:
        """Apply a change made by hand; a moved point is ``manual`` and changes zone.

        Args:
            prospect_id (UUID): The prospect.
            change (ProspectChange): What changes.

        Returns:
            Prospect | None: The prospect after the change, or ``None`` when missing.
        """

        values: dict[str, Any] = {}
        if change.location is not None:
            values["location"] = geo.point(change.location)
            values["location_quality"] = MANUAL
        if change.visit_status is not None:
            values["visit_status"] = change.visit_status
            values["visited_at"] = None if change.visit_status == PENDING else func.now()
        if change.set_note:
            values["visit_note"] = change.visit_note
        async with self.database.session_factory() as session:
            if values:
                found = await session.scalar(
                    update(P).where(P.id == prospect_id).values(**values).returning(P.id)
                )
                if found is None:
                    return None
                if change.location is not None:
                    await geo.place_prospects(session, P.id == prospect_id)
                await session.commit()
        return await self.get(prospect_id)

    async def loads(self, limit: int) -> list[ProspectLoad]:
        """The latest loads, newest first.

        Args:
            limit (int): How many.

        Returns:
            list[ProspectLoad]: The loads.
        """

        async with self.database.session_factory() as session:
            rows = await session.scalars(select(L).order_by(L.created_at.desc()).limit(limit))
            return [
                ProspectLoad(
                    id=row.id,
                    file_name=row.file_name,
                    rows_read=row.rows_read,
                    created=row.created_count,
                    updated=row.updated_count,
                    unchanged=row.unchanged_count,
                    loaded_by=row.loaded_by,
                    loaded_at=row.created_at,
                )
                for row in rows
            ]


__all__ = ["SqlAlchemyProspectRepository"]
