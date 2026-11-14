"""Tables of the commercial map. Geometries are PostGIS, WGS84 (SRID 4326).

Spatial indexes are declared in ``__table_args__`` (``spatial_index=False`` on the columns) so
the migration and the models name them the same way.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Any

from geoalchemy2 import Geometry
from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.infrastructure.database import Base

__all__ = [
    "GeoZoneModel",
    "ProspectLoadModel",
    "ProspectModel",
    "VisitRouteModel",
    "VisitRouteStopModel",
]


def _id() -> Mapped[uuid.UUID]:
    return mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )


def _created() -> Mapped[datetime]:
    return mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


def _updated() -> Mapped[datetime]:
    return mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class ProspectLoadModel(Base):
    """One upload of the monthly prospects file."""

    __tablename__ = "prospect_loads"

    id: Mapped[uuid.UUID] = _id()
    file_name: Mapped[str] = mapped_column(String(255), nullable=False)
    rows_read: Mapped[int] = mapped_column(Integer, nullable=False)
    created_count: Mapped[int] = mapped_column(Integer, nullable=False)
    updated_count: Mapped[int] = mapped_column(Integer, nullable=False)
    unchanged_count: Mapped[int] = mapped_column(Integer, nullable=False)
    loaded_by_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    loaded_by: Mapped[str | None] = mapped_column(String(320), nullable=True)
    created_at: Mapped[datetime] = _created()


class GeoZoneModel(Base):
    """A polygon: an official one (municipality, commune, neighborhood) or a JDH zone."""

    __tablename__ = "geo_zones"
    __table_args__ = (
        CheckConstraint("layer IN ('official', 'commercial')", name="ck_geo_zones_layer"),
        CheckConstraint(
            "level IN ('department', 'municipality', 'commune', 'neighborhood', 'custom')",
            name="ck_geo_zones_level",
        ),
        CheckConstraint(
            "color IS NULL OR color IN ('gray', 'brown', 'orange', 'yellow', 'green', 'blue', "
            "'purple', 'pink', 'red')",
            name="ck_geo_zones_color",
        ),
        # An official layer is reloaded by level and code.
        Index(
            "uq_geo_zones_official_level_code",
            "level",
            "code",
            unique=True,
            postgresql_where=text("layer = 'official'"),
        ),
        Index("ix_geo_zones_geom", "geom", postgresql_using="gist"),
        Index("ix_geo_zones_layer_level", "layer", "level"),
    )

    id: Mapped[uuid.UUID] = _id()
    layer: Mapped[str] = mapped_column(String(12), nullable=False)
    level: Mapped[str] = mapped_column(String(16), nullable=False)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    code: Mapped[str | None] = mapped_column(String(40), nullable=True)
    parent_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("geo_zones.id", ondelete="SET NULL"), nullable=True
    )
    color: Mapped[str | None] = mapped_column(String(12), nullable=True)
    source: Mapped[str | None] = mapped_column(String(160), nullable=True)
    geom: Mapped[Any] = mapped_column(
        Geometry("MULTIPOLYGON", srid=4326, spatial_index=False), nullable=False
    )
    created_by_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = _created()
    updated_at: Mapped[datetime] = _updated()


class ProspectModel(Base):
    """A prospect of the monthly file, keyed by the file's ``ID``."""

    __tablename__ = "prospects"
    __table_args__ = (
        UniqueConstraint("external_id", name="uq_prospects_external_id"),
        CheckConstraint(
            "location_quality IN ('exact', 'approximate', 'manual', 'missing')",
            name="ck_prospects_location_quality",
        ),
        CheckConstraint(
            "(location IS NULL) = (location_quality = 'missing')", name="ck_prospects_location"
        ),
        CheckConstraint(
            "area_status IS NULL OR area_status IN ('inside', 'outside', 'unlocated')",
            name="ck_prospects_area_status",
        ),
        CheckConstraint(
            "visit_status IN ('pending', 'visited', 'interested', 'discarded')",
            name="ck_prospects_visit_status",
        ),
        Index("ix_prospects_location", "location", postgresql_using="gist"),
        Index("ix_prospects_source_zone", "source_zone"),
        Index("ix_prospects_commercial_zone_id", "commercial_zone_id"),
    )

    id: Mapped[uuid.UUID] = _id()
    external_id: Mapped[str] = mapped_column(String(60), nullable=False)
    source_zone: Mapped[str] = mapped_column(String(120), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    category: Mapped[str | None] = mapped_column(String(120), nullable=True)
    is_former_customer: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    last_purchase_month: Mapped[date | None] = mapped_column(Date, nullable=True)
    address: Mapped[str | None] = mapped_column(Text, nullable=True)
    sector: Mapped[str | None] = mapped_column(String(255), nullable=True)
    area_status: Mapped[str | None] = mapped_column(String(12), nullable=True)
    phone_raw: Mapped[str | None] = mapped_column(Text, nullable=True)
    email_raw: Mapped[str | None] = mapped_column(Text, nullable=True)
    # ``[{number, display, label, is_mobile}]`` and ``[email]``, cleaned from the raw cells.
    phones: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )
    emails: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )
    website: Mapped[str | None] = mapped_column(Text, nullable=True)
    observation: Mapped[str | None] = mapped_column(Text, nullable=True)
    location: Mapped[Any | None] = mapped_column(
        Geometry("POINT", srid=4326, spatial_index=False), nullable=True
    )
    location_quality: Mapped[str] = mapped_column(String(12), nullable=False)
    commercial_zone_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("geo_zones.id", ondelete="SET NULL"), nullable=True
    )
    visit_status: Mapped[str] = mapped_column(
        String(12), nullable=False, server_default=text("'pending'")
    )
    visit_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    visited_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    load_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("prospect_loads.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = _created()
    updated_at: Mapped[datetime] = _updated()


class VisitRouteModel(Base):
    """A planned visit route: an origin and ordered stops joined by road (or straight)."""

    __tablename__ = "visit_routes"
    __table_args__ = (
        CheckConstraint(
            "geometry_status IN ('road', 'straight', 'pending')",
            name="ck_visit_routes_geometry_status",
        ),
        Index("ix_visit_routes_assignee_id", "assignee_id"),
    )

    id: Mapped[uuid.UUID] = _id()
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    planned_for: Mapped[date | None] = mapped_column(Date, nullable=True)
    assignee_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    origin: Mapped[Any | None] = mapped_column(
        Geometry("POINT", srid=4326, spatial_index=False), nullable=True
    )
    origin_label: Mapped[str | None] = mapped_column(String(160), nullable=True)
    path: Mapped[Any | None] = mapped_column(
        Geometry("LINESTRING", srid=4326, spatial_index=False), nullable=True
    )
    geometry_status: Mapped[str] = mapped_column(String(10), nullable=False)
    distance_m: Mapped[int | None] = mapped_column(Integer, nullable=True)
    duration_s: Mapped[int | None] = mapped_column(Integer, nullable=True)
    routing_note: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_by_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = _created()
    updated_at: Mapped[datetime] = _updated()


class VisitRouteStopModel(Base):
    """A stop of a route: a copy of the prospect, so the route outlives new loads."""

    __tablename__ = "visit_route_stops"
    __table_args__ = (
        UniqueConstraint("route_id", "position", name="uq_visit_route_stops_route_position"),
        CheckConstraint(
            "visit_status IN ('pending', 'visited', 'interested', 'discarded')",
            name="ck_visit_route_stops_visit_status",
        ),
        CheckConstraint("position >= 1", name="ck_visit_route_stops_position"),
        Index("ix_visit_route_stops_prospect_id", "prospect_id"),
    )

    id: Mapped[uuid.UUID] = _id()
    route_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("visit_routes.id", ondelete="CASCADE"), nullable=False
    )
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    prospect_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("prospects.id", ondelete="SET NULL"), nullable=True
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    address: Mapped[str | None] = mapped_column(Text, nullable=True)
    location: Mapped[Any] = mapped_column(
        Geometry("POINT", srid=4326, spatial_index=False), nullable=False
    )
    visit_status: Mapped[str] = mapped_column(
        String(12), nullable=False, server_default=text("'pending'")
    )
    visit_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    visited_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    leg_distance_m: Mapped[int | None] = mapped_column(Integer, nullable=True)
    leg_duration_s: Mapped[int | None] = mapped_column(Integer, nullable=True)
