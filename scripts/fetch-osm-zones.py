"""Download official boundaries of the Aburrá valley from OpenStreetMap as GeoJSON.

Asks Nominatim (one request per second, as its usage policy requires) for the polygon of each
boundary and writes two files ready for ``scripts/import-geo-zones.py``:

- ``medellin-comunas.geojson``: the 16 comunas and 5 corregimientos of Medellín.
- ``aburra-municipios.geojson``: the 10 municipalities of the Aburrá valley.

Each feature has ``name`` and ``osm_id`` (the code to load by). The data is OpenStreetMap's,
under the ODbL: the map already shows "© OpenStreetMap".

OpenStreetMap has no complete layer of Medellín's barrios; those come from GeoMedellín.

Usage:
    poetry run python scripts/fetch-osm-zones.py <folder>
    poetry run python scripts/import-geo-zones.py <folder>/medellin-comunas.geojson \\
        --level commune --name-property name --code-property osm_id \\
        --source "OpenStreetMap (ODbL)" --apply
    poetry run python scripts/import-geo-zones.py <folder>/aburra-municipios.geojson \\
        --level municipality --name-property name --code-property osm_id \\
        --source "OpenStreetMap (ODbL)" --apply
"""

from __future__ import annotations

import argparse
import json
import time
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

NOMINATIM = "https://nominatim.openstreetmap.org/search"
HEADERS = {"User-Agent": "internal-platform-map-loader/1.0"}
COMUNAS = (
    "Comuna 1 - Popular",
    "Comuna 2 - Santa Cruz",
    "Comuna 3 - Manrique",
    "Comuna 4 - Aranjuez",
    "Comuna 5 - Castilla",
    "Comuna 6 - Doce de Octubre",
    "Comuna 7 - Robledo",
    "Comuna 8 - Villa Hermosa",
    "Comuna 9 - Buenos Aires",
    "Comuna 10 - La Candelaria",
    "Comuna 11 - Laureles-Estadio",
    "Comuna 12 - La América",
    "Comuna 13 - San Javier",
    "Comuna 14 - El Poblado",
    "Comuna 15 - Guayabal",
    "Comuna 16 - Belén",
)
CORREGIMIENTOS = (
    "Altavista",
    "San Antonio de Prado",
    "San Cristóbal",
    "San Sebastián de Palmitas",
    "Santa Elena",
)
MUNICIPIOS = (
    "Medellín",
    "Bello",
    "Itagüí",
    "Envigado",
    "Sabaneta",
    "La Estrella",
    "Caldas",
    "Copacabana",
    "Girardota",
    "Barbosa",
)


def _boundary(query: str, admin_level: str) -> dict[str, Any] | None:
    """The administrative boundary of that level Nominatim finds for the query."""

    params = urllib.parse.urlencode(
        {
            "q": query,
            "format": "jsonv2",
            "polygon_geojson": 1,
            "limit": 10,
            "countrycodes": "co",
            "extratags": 1,
        }
    )
    request = urllib.request.Request(f"{NOMINATIM}?{params}", headers=HEADERS)
    with urllib.request.urlopen(request, timeout=60) as response:
        results = json.loads(response.read().decode("utf-8"))
    time.sleep(1.1)
    for item in results:
        level = str((item.get("extratags") or {}).get("admin_level", ""))
        if (
            item.get("osm_type") == "relation"
            and item.get("category") == "boundary"
            and level == admin_level
            and (item.get("geojson") or {}).get("type") in ("Polygon", "MultiPolygon")
        ):
            return {
                "type": "Feature",
                "properties": {"name": item["name"], "osm_id": str(item["osm_id"])},
                "geometry": item["geojson"],
            }
    return None


def _collect(names: tuple[str, ...], suffix: str, admin_level: str) -> list[dict[str, Any]]:
    features = []
    for name in names:
        feature = _boundary(f"{name}, {suffix}", admin_level)
        if feature is None:
            print(f"  no encontrado: {name}")
            continue
        features.append(feature)
        print(f"  {feature['properties']['name']} (osm {feature['properties']['osm_id']})")
    return features


def _write(path: Path, features: list[dict[str, Any]]) -> None:
    collection = {"type": "FeatureCollection", "features": features}
    path.write_text(json.dumps(collection, ensure_ascii=False), encoding="utf-8")
    print(f"{len(features)} zonas en {path}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("folder", type=Path)
    folder = parser.parse_args().folder
    folder.mkdir(parents=True, exist_ok=True)

    print("Comunas y corregimientos de Medellín:")
    zones = _collect(COMUNAS, "Medellín, Antioquia", "8")
    zones += _collect(CORREGIMIENTOS, "Medellín, Antioquia", "7")
    _write(folder / "medellin-comunas.geojson", zones)
    print("Municipios del Valle de Aburrá:")
    _write(folder / "aburra-municipios.geojson", _collect(MUNICIPIOS, "Antioquia", "6"))


if __name__ == "__main__":
    main()
