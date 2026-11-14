"""Commercial map: prospects with their location, zones and visit routes (PostGIS).

- ``postgis`` extension. Unlike ``pg_trgm`` (0014) there is no fallback: the geometry columns
  need it, so the migration stops with a clear message when the server lacks it (the image must
  be ``postgis/postgis``). The downgrade leaves the extension in place.
- ``prospect_loads``: each upload of the monthly file.
- ``geo_zones``: official polygons (reference) and JDH's commercial zones.
- ``prospects``: keyed by the file's ``ID``, with a point, its quality and its commercial zone.
- ``visit_routes`` and ``visit_route_stops``: planned routes; a stop copies the prospect.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from geoalchemy2 import Geometry
from sqlalchemy.dialects import postgresql

revision: str = "0063_territory"
down_revision: str | None = "0062_internal_support_requests"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TZ = sa.DateTime(timezone=True)
_UUID = postgresql.UUID(as_uuid=True)


def _id() -> sa.Column:
    return sa.Column("id", _UUID, primary_key=True, server_default=sa.text("gen_random_uuid()"))


def _timestamps() -> list[sa.Column]:
    return [
        sa.Column("created_at", _TZ, nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", _TZ, nullable=False, server_default=sa.func.now()),
    ]


def _geometry(kind: str) -> Geometry:
    return Geometry(kind, srid=4326, spatial_index=False)


def _require_postgis() -> None:
    bind = op.get_bind()
    available = bind.execute(
        sa.text("SELECT 1 FROM pg_available_extensions WHERE name = 'postgis'")
    ).scalar()
    if not available:
        raise RuntimeError(
            "PostgreSQL no tiene PostGIS: la base debe correr en la imagen postgis/postgis "
            "(ver docs/mapa-comercial.md)"
        )
    op.execute("CREATE EXTENSION IF NOT EXISTS postgis")


def upgrade() -> None:
    """Enable PostGIS and create the tables of the commercial map."""

    _require_postgis()
    op.create_table(
        "prospect_loads",
        _id(),
        sa.Column("file_name", sa.String(255), nullable=False),
        sa.Column("rows_read", sa.Integer, nullable=False),
        sa.Column("created_count", sa.Integer, nullable=False),
        sa.Column("updated_count", sa.Integer, nullable=False),
        sa.Column("unchanged_count", sa.Integer, nullable=False),
        sa.Column("loaded_by_id", _UUID, sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("loaded_by", sa.String(320)),
        sa.Column("created_at", _TZ, nullable=False, server_default=sa.func.now()),
    )
    op.create_table(
        "geo_zones",
        _id(),
        sa.Column("layer", sa.String(12), nullable=False),
        sa.Column("level", sa.String(16), nullable=False),
        sa.Column("name", sa.String(160), nullable=False),
        sa.Column("code", sa.String(40)),
        sa.Column("parent_id", _UUID, sa.ForeignKey("geo_zones.id", ondelete="SET NULL")),
        sa.Column("color", sa.String(12)),
        sa.Column("source", sa.String(160)),
        sa.Column("geom", _geometry("MULTIPOLYGON"), nullable=False),
        sa.Column("created_by_id", _UUID, sa.ForeignKey("users.id", ondelete="SET NULL")),
        *_timestamps(),
        sa.CheckConstraint("layer IN ('official', 'commercial')", name="ck_geo_zones_layer"),
        sa.CheckConstraint(
            "level IN ('department', 'municipality', 'commune', 'neighborhood', 'custom')",
            name="ck_geo_zones_level",
        ),
        sa.CheckConstraint(
            "color IS NULL OR color IN ('gray', 'brown', 'orange', 'yellow', 'green', 'blue', "
            "'purple', 'pink', 'red')",
            name="ck_geo_zones_color",
        ),
    )
    op.create_index(
        "uq_geo_zones_official_level_code",
        "geo_zones",
        ["level", "code"],
        unique=True,
        postgresql_where=sa.text("layer = 'official'"),
    )
    op.create_index("ix_geo_zones_geom", "geo_zones", ["geom"], postgresql_using="gist")
    op.create_index("ix_geo_zones_layer_level", "geo_zones", ["layer", "level"])

    op.create_table(
        "prospects",
        _id(),
        sa.Column("external_id", sa.String(60), nullable=False),
        sa.Column("source_zone", sa.String(120), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("category", sa.String(120)),
        sa.Column(
            "is_former_customer", sa.Boolean, nullable=False, server_default=sa.text("false")
        ),
        sa.Column("last_purchase_month", sa.Date),
        sa.Column("address", sa.Text),
        sa.Column("sector", sa.String(255)),
        sa.Column("area_status", sa.String(12)),
        sa.Column("phone_raw", sa.Text),
        sa.Column("email_raw", sa.Text),
        sa.Column(
            "phones", postgresql.JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")
        ),
        sa.Column(
            "emails", postgresql.JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")
        ),
        sa.Column("website", sa.Text),
        sa.Column("observation", sa.Text),
        sa.Column("location", _geometry("POINT")),
        sa.Column("location_quality", sa.String(12), nullable=False),
        sa.Column("commercial_zone_id", _UUID, sa.ForeignKey("geo_zones.id", ondelete="SET NULL")),
        sa.Column(
            "visit_status", sa.String(12), nullable=False, server_default=sa.text("'pending'")
        ),
        sa.Column("visit_note", sa.Text),
        sa.Column("visited_at", _TZ),
        sa.Column("load_id", _UUID, sa.ForeignKey("prospect_loads.id", ondelete="SET NULL")),
        *_timestamps(),
        sa.UniqueConstraint("external_id", name="uq_prospects_external_id"),
        sa.CheckConstraint(
            "location_quality IN ('exact', 'approximate', 'manual', 'missing')",
            name="ck_prospects_location_quality",
        ),
        sa.CheckConstraint(
            "(location IS NULL) = (location_quality = 'missing')", name="ck_prospects_location"
        ),
        sa.CheckConstraint(
            "area_status IS NULL OR area_status IN ('inside', 'outside', 'unlocated')",
            name="ck_prospects_area_status",
        ),
        sa.CheckConstraint(
            "visit_status IN ('pending', 'visited', 'interested', 'discarded')",
            name="ck_prospects_visit_status",
        ),
    )
    op.create_index("ix_prospects_location", "prospects", ["location"], postgresql_using="gist")
    op.create_index("ix_prospects_source_zone", "prospects", ["source_zone"])
    op.create_index("ix_prospects_commercial_zone_id", "prospects", ["commercial_zone_id"])

    op.create_table(
        "visit_routes",
        _id(),
        sa.Column("name", sa.String(160), nullable=False),
        sa.Column("planned_for", sa.Date),
        sa.Column("assignee_id", _UUID, sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("origin", _geometry("POINT")),
        sa.Column("origin_label", sa.String(160)),
        sa.Column("path", _geometry("LINESTRING")),
        sa.Column("geometry_status", sa.String(10), nullable=False),
        sa.Column("distance_m", sa.Integer),
        sa.Column("duration_s", sa.Integer),
        sa.Column("routing_note", sa.String(255)),
        sa.Column("created_by_id", _UUID, sa.ForeignKey("users.id", ondelete="SET NULL")),
        *_timestamps(),
        sa.CheckConstraint(
            "geometry_status IN ('road', 'straight', 'pending')",
            name="ck_visit_routes_geometry_status",
        ),
    )
    op.create_index("ix_visit_routes_assignee_id", "visit_routes", ["assignee_id"])

    op.create_table(
        "visit_route_stops",
        _id(),
        sa.Column(
            "route_id",
            _UUID,
            sa.ForeignKey("visit_routes.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("position", sa.Integer, nullable=False),
        sa.Column("prospect_id", _UUID, sa.ForeignKey("prospects.id", ondelete="SET NULL")),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("address", sa.Text),
        sa.Column("location", _geometry("POINT"), nullable=False),
        sa.Column(
            "visit_status", sa.String(12), nullable=False, server_default=sa.text("'pending'")
        ),
        sa.Column("visit_note", sa.Text),
        sa.Column("visited_at", _TZ),
        sa.Column("leg_distance_m", sa.Integer),
        sa.Column("leg_duration_s", sa.Integer),
        sa.UniqueConstraint("route_id", "position", name="uq_visit_route_stops_route_position"),
        sa.CheckConstraint(
            "visit_status IN ('pending', 'visited', 'interested', 'discarded')",
            name="ck_visit_route_stops_visit_status",
        ),
        sa.CheckConstraint("position >= 1", name="ck_visit_route_stops_position"),
    )
    op.create_index("ix_visit_route_stops_prospect_id", "visit_route_stops", ["prospect_id"])


def downgrade() -> None:
    """Drop the tables; PostGIS stays (another module may use it)."""

    op.drop_table("visit_route_stops")
    op.drop_table("visit_routes")
    op.drop_table("prospects")
    op.drop_table("geo_zones")
    op.drop_table("prospect_loads")
