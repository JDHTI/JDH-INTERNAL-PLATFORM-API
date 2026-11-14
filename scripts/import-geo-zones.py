"""Load official polygons (municipalities, communes, neighborhoods) into the commercial map.

Reads a GeoJSON ``FeatureCollection`` in WGS84 (EPSG:4326) and creates or replaces the official
zones of one level by their code. They are the reference layer: a commercial zone can adopt
them (``POST /territory/zones/adopt``), for instance Laureles = comuna 11. Runs as a dry run
unless ``--apply`` is given and can be repeated.

Sources (check each license when downloading):
    Municipalities: DANE, Marco Geoestadístico Nacional (geoportal.dane.gov.co).
    Communes and neighborhoods of Medellín: GeoMedellín (Alcaldía de Medellín).

A shapefile is converted first, for example with GDAL:
    ogr2ogr -f GeoJSON -t_srs EPSG:4326 barrios.geojson barrios.shp

Usage:
    poetry run python scripts/import-geo-zones.py barrios.geojson --level neighborhood \\
        --name-property NOMBRE --code-property CODIGO --source "GeoMedellín 2023"
    poetry run python scripts/import-geo-zones.py barrios.geojson --level neighborhood \\
        --name-property NOMBRE --code-property CODIGO --source "GeoMedellín 2023" --apply
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.app_container import (
    build_application_container,
    initialize_global_resources,
    shutdown_global_resources,
)
from app.core.config import Settings
from app.core.errors import ApplicationError
from app.modules.territory.domain.entities import OfficialZoneData
from app.modules.territory.domain.geometry import check_area

LEVELS = ("department", "municipality", "commune", "neighborhood")


def _text(value: Any) -> str:
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    return str(value).strip() if value is not None else ""


def read_zones(
    path: Path, level: str, name_property: str, code_property: str | None
) -> tuple[list[OfficialZoneData], list[str]]:
    """The polygons of the file and the problems of the features left out."""

    # utf-8-sig: files saved by Windows tools start with a BOM.
    data = json.loads(path.read_text(encoding="utf-8-sig"))
    if data.get("type") != "FeatureCollection":
        raise SystemExit("El archivo debe ser un FeatureCollection de GeoJSON")
    zones: list[OfficialZoneData] = []
    problems: list[str] = []
    for index, feature in enumerate(data.get("features") or [], 1):
        properties = feature.get("properties") or {}
        name = _text(properties.get(name_property))
        code = _text(properties.get(code_property)) if code_property else name
        if not name or not code:
            problems.append(f"#{index}: sin {name_property} o {code_property}")
            continue
        try:
            geometry = check_area(feature.get("geometry"), max_vertices=500_000)
        except ApplicationError as exc:
            problems.append(f"#{index} {name}: {exc.detail}")
            continue
        zones.append(OfficialZoneData(level=level, name=name, code=code, geometry=geometry))
    return zones, problems


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("file", type=Path)
    parser.add_argument("--level", required=True, choices=LEVELS)
    parser.add_argument("--name-property", required=True)
    parser.add_argument("--code-property", default=None, help="Defaults to the name")
    parser.add_argument("--source", default=None, help="Where the layer comes from")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()

    zones, problems = read_zones(args.file, args.level, args.name_property, args.code_property)
    print(f"{len(zones)} zonas de nivel {args.level} en {args.file.name}")
    for problem in problems:
        print(f"  omitida {problem}")
    codes = [zone.code for zone in zones]
    repeated = sorted({code for code in codes if codes.count(code) > 1})
    if repeated:
        raise SystemExit(f"Códigos repetidos: {', '.join(repeated[:20])}")
    if not args.apply:
        print("Simulación: nada se escribió. Agrega --apply para cargar.")
        return

    container = build_application_container(Settings())
    resources = await initialize_global_resources(container)
    try:
        service = container.v1_container.territory_zone_service()
        created, replaced = await service.load_official(zones, args.source)
        print(f"Creadas {created}, reemplazadas {replaced}")
    finally:
        await shutdown_global_resources(resources)


if __name__ == "__main__":
    asyncio.run(main())
